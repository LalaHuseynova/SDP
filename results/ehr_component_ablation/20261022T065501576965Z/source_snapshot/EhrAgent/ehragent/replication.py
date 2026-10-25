"""EHR-only deterministic memory loading and content-addressed embedding caches."""
import hashlib
import json
import os
from pathlib import Path
import torch

ROOT = Path(__file__).resolve().parents[2]

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def load_ehr_memory(memory_log_dir):
    directory = Path(memory_log_dir)
    if not directory.is_absolute():
        directory = ROOT / directory
    files = sorted(directory.glob('*.txt'))
    if not files:
        raise FileNotFoundError(f'Required original EHR memory logs missing: {directory}')
    records, seen = [], set()
    # Preserve released parser semantics, but replace filesystem/set order with stable order.
    for path in files:
        text = path.read_text()
        if 'Question:' not in text or 'Knowledge:' not in text:
            raise ValueError(f'Invalid memory log: {path.name}')
        split = text.split('(END OF EXAMPLES)')
        blocks = split[0].split('\n\n')
        parsed = []
        for block in blocks[1:-1]:
            block = block.split('Question:')[-1]
            question = block.split('\nKnowledge:\n')[0]
            if len(question.split(' ')) > 20:
                continue
            block = block.split('\nKnowledge:\n')[-1]
            parsed.append((question,block.split('\nSolution:')[0],block.split('\nSolution:')[-1]))
        if len(split)>1:
            block=split[1].split('Knowledge:\n')[-1]
            knowledge=block.split('Question:')[0]
            block=block.split('Question:')[-1]
            question=block.split('Solution:')[0]
            if len(question.split(' '))<=20:
                parsed.append((question,knowledge,block.split('Solution:')[-1]))
        for ordinal,(q,k,c) in enumerate(parsed):
            key=(q,k,c)
            if key in seen:
                continue
            seen.add(key)
            records.append(dict(id='mem-'+digest(key)[:16],question=q,knowledge=k,code=c,
                                source_file=path.name,source_ordinal=ordinal,poisoned=False))
    if len(records)<4:
        raise ValueError('Need at least four original memory records')
    return records


def embed_texts(model, tokenizer, texts, batch_size=8):
    outputs=[]
    model.eval()
    with torch.no_grad():
        for start in range(0,len(texts),batch_size):
            t=tokenizer(texts[start:start+batch_size],return_tensors='pt',padding=True,truncation=True,max_length=512)
            outputs.append(model(input_ids=t['input_ids'].to(model.device),attention_mask=t['attention_mask'].to(model.device)).pooler_output.detach())
    return torch.cat(outputs)


def load_db_ehr(database_samples_dir='EhrAgent/database/ehr_logs/logs_final', db_dir='results/small/ehr/cache', model_code=None,model=None,tokenizer=None,device=None):
    records=load_ehr_memory(database_samples_dir)
    if model is None or tokenizer is None:
        raise ValueError('EHR cache requires actual model/tokenizer')
    model.eval()
    metadata={'schema':2,'records':digest(records),'model':str(model.config._name_or_path),
              'config':digest(model.config.to_dict()),'vocab':digest(tokenizer.get_vocab()),
              'weights':os.environ.get('EHR_DPR_SHA256','unknown'),'max_length':512,'pooling':'pooler_output'}
    # Existing unversioned pickles are deliberately never loaded.
    cache=ROOT/Path(db_dir)/('ehr-'+digest(metadata)+'.pt');cache.parent.mkdir(parents=True,exist_ok=True)
    if cache.exists():
        obj=torch.load(cache,map_location='cpu',weights_only=True)
        if obj['metadata']!=metadata or obj['ids']!=[m['id'] for m in records]:
            raise ValueError('Stale/misaligned EHR cache')
        q,a=obj['questions'],obj['actions']
    else:
        q=embed_texts(model,tokenizer,[m['question'] for m in records]).cpu()
        a=embed_texts(model,tokenizer,[m['code'] for m in records]).cpu()
        torch.save(dict(metadata=metadata,ids=[m['id'] for m in records],questions=q,actions=a),cache)
    if q.shape!=a.shape or q.ndim!=2 or q.shape[0]!=len(records) or not torch.isfinite(q).all() or not torch.isfinite(a).all():
        raise ValueError('Invalid EHR embedding alignment')
    return q.to(model.device),a.to(model.device),records
