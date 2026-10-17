"""Small AgentPoison retrieval-only EHR optimization; no target or coherence proxy."""
import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path
import numpy as np
import torch
from sklearn.mixture import GaussianMixture

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'EhrAgent/ehragent'))
from algo.utils import load_models, get_embeddings
from replication import load_db_ehr, embed_texts

def canonical(tokenizer, ids):
    text=tokenizer.decode(ids,skip_special_tokens=True,clean_up_tokenization_spaces=False).strip()
    if not text or tokenizer.encode(text,add_special_tokens=False)!=list(ids):
        raise ValueError('Trigger does not round-trip')
    return text

def construct_inputs(tokenizer, questions, trigger):
    if not trigger.strip():raise ValueError('Empty trigger')
    texts=[q+' '+trigger for q in questions]
    # No transfer of IDs across tokenizers. Always decode DPR IDs first and tokenize text.
    encoded=tokenizer(texts,return_tensors='pt',padding=True,truncation=True,max_length=512,return_offsets_mapping=True)
    offsets=encoded.pop('offset_mapping')
    masks=[]
    trigger_ids=tokenizer.encode(trigger,add_special_tokens=False)
    for q,ids,off in zip(questions,encoded['input_ids'],offsets):
        positions=[i for i,(a,b) in enumerate(off.tolist()) if b>len(q)+1 and a>=len(q)+1]
        if [int(ids[i]) for i in positions]!=trigger_ids:
            raise ValueError('Trigger lost or changed by tokenization/truncation')
        masks.append(positions)
    return encoded,masks,texts

def objective(embeddings,centers):
    # Released AP isolation/compactness objective, with unambiguous [K,D] center shape.
    return torch.cdist(embeddings,centers).mean()-.1*torch.norm(embeddings-embeddings.mean(0,keepdim=True),dim=1).mean()

def choose_candidate(current_score,scores):
    if not all(np.isfinite(s) for s in [current_score]+list(scores)):raise ValueError('Nonfinite score')
    idx=int(np.argmax(scores))
    return idx if scores[idx]>current_score else None

def optimize(args):
    random.seed(0);np.random.seed(0);torch.manual_seed(0);torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    if args.target_gradient_guidance or args.ppl_filter or args.report_to_wandb:
        raise ValueError('EHR pilot explicitly disables unavailable EHR target scoring, coherence filter, and W&B')
    split=json.loads(Path(args.split_file).read_text())
    training=split['training'];assert len(training)==16
    model,tokenizer,_=load_models(args.model,device='cpu');model.to('cpu').eval()
    for p in model.parameters():p.requires_grad_(False)
    db,_,memory=load_db_ehr(model_code=args.model,model=model,tokenizer=tokenizer)
    gmm=GaussianMixture(n_components=5,covariance_type='full',random_state=0)
    gmm.fit(db.numpy())
    centers=torch.tensor(gmm.means_,dtype=db.dtype)
    ids=tokenizer.encode(args.initial_trigger,add_special_tokens=False)
    if len(ids)!=args.num_adv_passage_tokens:raise ValueError('Initialization length mismatch')
    initial_text=canonical(tokenizer,ids)
    questions=[m['template'] for m in training]
    source_questions=[m['question'] for m in memory[:4]]
    embedding_layer=get_embeddings(model)
    def score(candidate,grad=False):
        text=canonical(tokenizer,candidate)
        tokens,positions,texts=construct_inputs(tokenizer,questions,text)
        embeds=embedding_layer(tokens['input_ids']).detach().requires_grad_(grad)
        with torch.set_grad_enabled(grad):
            e=model.ctx_encoder.bert_model(inputs_embeds=embeds,attention_mask=tokens['attention_mask']).last_hidden_state[:,0,:]
            if model.config.projection_dim>0:e=model.ctx_encoder.encode_proj(e)
            value=objective(e,centers)
        if grad:
            value.backward()
            gradient=torch.stack([embeds.grad[j,p] for j,p in enumerate(positions)]).sum(0)
            if not torch.isfinite(gradient).all():raise ValueError('Nonfinite retrieval gradient')
            return float(value.detach()),gradient
        return float(value)
    def hits(candidate):
        text=canonical(tokenizer,candidate)
        q=embed_texts(model,tokenizer,[q+' '+text for q in questions])
        p=embed_texts(model,tokenizer,[q+' '+text for q in source_questions])
        all_db=torch.cat([db,p]);sim=torch.nn.functional.normalize(q)@torch.nn.functional.normalize(all_db).T
        selected=torch.argsort(sim,dim=1,descending=True,stable=True)[:,:4]
        return {'hits':int((selected>=len(db)).any(1).sum()),'denominator':len(questions)}
    current=score(ids);initial_score=current;initial_hits=hits(ids);history=[]
    # Only independent, non-special single-word tokens whose canonical decoding round-trips.
    allowed=torch.tensor([i for i in range(len(tokenizer)) if i not in tokenizer.all_special_ids and tokenizer.convert_ids_to_tokens(i).isalpha() and not tokenizer.convert_ids_to_tokens(i).startswith('##')])
    for iteration in range(args.num_iter):
        current,gradient=score(ids,True)
        position=random.randrange(len(ids))
        rank=(embedding_layer.weight[allowed]@gradient[position]).argsort(descending=True,stable=True)
        proposals=allowed[rank[:32]].tolist();candidates=[]
        for token in proposals:
            candidate=ids.copy();candidate[position]=token
            try:
                text=canonical(tokenizer,candidate);construct_inputs(tokenizer,questions,text)
            except ValueError:continue
            candidates.append(candidate)
            if len(candidates)==16:break
        if len(candidates)!=16:raise ValueError('Fewer than sixteen valid candidates from 32 proposals')
        scores=[score(c) for c in candidates]
        chosen=choose_candidate(current,scores)
        before=current
        if chosen is not None:ids=candidates[chosen];current=scores[chosen]
        entry=dict(iteration=iteration+1,position=position,gradient_norm=float(gradient.norm()),proposals=proposals,
            candidate_ids=candidates,candidate_scores=scores,selected=chosen,objective_before=before,objective_after=current,
            trigger_text=canonical(tokenizer,ids),retrieval=hits(ids))
        history.append(entry);print(json.dumps({k:entry[k] for k in ['iteration','objective_after','trigger_text','retrieval']}),flush=True)
    result=dict(status='success',optimization='retrieval-only',configuration=vars(args),training_ids=[x['id'] for x in training],
        model=str(model.config._name_or_path),device='cpu',initialization=initial_text,effective_trigger_length=len(ids),
        trigger_token_ids=ids,trigger_text=canonical(tokenizer,ids),round_trip=True,initial_objective=initial_score,
        final_objective=score(ids),initial_retrieval=initial_hits,final_retrieval=hits(ids),iterations=history,
        objectives={'retrieval':{'model':'facebook/dpr-ctx_encoder-single-nq-base','formula':'mean ||q_i-c_j||_2 - 0.1 mean ||q_i-mean(q)||_2','direction':'maximize','weight':1,'role':'AP isolation and compactness'},
        'target':{'enabled':False,'reason':'Released fallback STOP is driving-specific; no defensible EHR target scorer implemented'},
        'coherence':{'enabled':False,'reason':'No perplexity model used; independent of target score'}},
        proposal_policy='Rank 32 single-token replacements by gradient dot embedding at seeded position; evaluate first 16 canonical, untruncated proposals; accept strict maximum only')
    dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('x') as f:json.dump(result,f,indent=2)
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--agent',choices=['ehr'],required=True);p.add_argument('--algo',choices=['ap'],required=True)
    p.add_argument('--model',default='dpr-ctx_encoder-single-nq-base');p.add_argument('--split_file',required=True)
    p.add_argument('--output',required=True);p.add_argument('--num_iter',type=int,default=10)
    p.add_argument('--num_cand',type=int,default=16);p.add_argument('--num_proposals',type=int,default=32)
    p.add_argument('--num_adv_passage_tokens',type=int,default=10)
    p.add_argument('--initial_trigger',default='be careful and use correct database for this patient query')
    for opt in ['target_gradient_guidance','ppl_filter','report_to_wandb']:p.add_argument('--'+opt,action='store_true')
    a=p.parse_args()
    if (a.num_iter,a.num_cand,a.num_proposals)!=(10,16,32):p.error('Small pilot requires 10 iterations, 32 proposals, 16 evaluated candidates')
    optimize(a)

if __name__=='__main__':main()
