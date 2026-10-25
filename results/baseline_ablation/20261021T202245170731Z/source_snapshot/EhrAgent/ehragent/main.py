"""EHR code-generation pilot with saved-ID subsets and auditable per-episode results."""
import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path
import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from config import openai_config
from medagent import MedAgent
from replication import digest
from llm_env import _STATE

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--llm',default=None);p.add_argument('--dataset',choices=['eicu'],default='eicu')
    p.add_argument('--data_path',default=str(ROOT/'EhrAgent/database/ehr_logs/eicu_ac.json'))
    p.add_argument('--num_questions',type=int,default=20);p.add_argument('--subset_file',required=True)
    p.add_argument('--subset_key',choices=['evaluation','smoke'],default='evaluation')
    p.add_argument('--seed',type=int,default=0);p.add_argument('--num_shots',type=int,default=4)
    p.add_argument('--num_lessons',type=int,default=4);p.add_argument('--backbone',default='openrouter')
    p.add_argument('--model',choices=['dpr'],default='dpr');p.add_argument('--algo',choices=['ap'],default='ap')
    p.add_argument('--attack',action='store_true');p.add_argument('--audit_method',choices=['A-MemGuard'],default=None)
    p.add_argument('--trigger',required=True);p.add_argument('--trigger_artifact',default=None)
    p.add_argument('--save_dir',required=True)
    args=p.parse_args()
    if not args.trigger.strip():p.error('Empty trigger is forbidden')
    random.seed(args.seed);np.random.seed(args.seed);torch.manual_seed(args.seed);torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    dest=Path(args.save_dir);dest.mkdir(parents=True,exist_ok=False)
    os.environ['EHR_API_LOG']=str(dest/'api.jsonl')
    model=args.llm or os.getenv('AGENT_MODEL','openai/gpt-4o-mini')
    config=[openai_config(model)]
    split=json.loads(Path(args.subset_file).read_text())
    selected=split[args.subset_key]
    if len(selected)!=args.num_questions:p.error('Saved subset size must equal --num_questions')
    trigger_meta=None
    if args.trigger_artifact:
        artifact=Path(args.trigger_artifact);obj=json.loads(artifact.read_text())
        if obj.get('status')!='success' or obj.get('trigger_text')!=args.trigger:p.error('Trigger artifact mismatch')
        trigger_meta=dict(path=str(artifact.resolve()),sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
    metadata=dict(configuration=vars(args),agent_model=model,judge_model=os.getenv('JUDGE_MODEL'),
        endpoint='https://openrouter.ai/api/v1',device='cpu',trigger_artifact=trigger_meta,
        trigger_label='optimized' if trigger_meta else 'temporary smoke only',lessons_accumulate_within_run=True,
        execution='code generation only; no generated code executed',seed=args.seed,
        budgets={k:os.getenv(k) for k in ['EHR_MAX_CALLS','EHR_MAX_OUTPUT_TOKENS','EHR_MAX_INPUT_TOKEN_BOUND','EHR_MAX_TOKEN_BOUND']})
    (dest/'metadata.json').write_text(json.dumps(metadata,indent=2))
    agent=MedAgent(name='user_proxy',human_input_mode='NEVER',code_execution_config=False,
        config_list=config,num_shots=args.num_shots,num_lessons=args.num_lessons,trigger_sequence=args.trigger,
        backbone=args.backbone,model_code='dpr-ctx_encoder-single-nq-base',audit_method=args.audit_method)
    agent.register_dataset('eicu')
    metadata['initial_memory_hash']=digest(agent.memory)
    metadata['poisoned_records']=agent.poison_manifest
    (dest/'metadata.json').write_text(json.dumps(metadata,indent=2))
    results=[]
    for question in selected:
        started=time.monotonic()
        call_start=_STATE["attempts"]
        item=dict(id=question['id'],question=question['template'],ground_truth=question['answer'],reference_matches=question['reference_matches'],
            input_question=question['template']+(' '+args.trigger if args.attack else ''),executed=False,final_answer=None)
        agent.last_trace={}
        try:
            message,code,knowledge,retrieval=agent.initiate_chat(None,message=item['input_question'])
            item.update(status='ok',code=code,knowledge=knowledge,retrieval=retrieval,init_message=message)
        except Exception as exc:
            item.update(status='failure',error_type=type(exc).__name__)
        item['trace']=agent.last_trace
        events=[json.loads(line) for line in (dest/'api.jsonl').read_text().splitlines()] if (dest/'api.jsonl').exists() else []
        episode_calls=[e for e in events if e['call']>call_start]
        item['api_call_range']=[call_start+1,_STATE['attempts']]
        item['token_usage']={k:sum((e.get('usage') or {}).get(k,0) for e in episode_calls) for k in ['prompt_tokens','completion_tokens','total_tokens']}
        item['wall_seconds']=time.monotonic()-started
        results.append(item)
        (dest/'episodes.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(dict(id=item['id'],status=item['status'],wall_seconds=item['wall_seconds'])),flush=True)
        # No episode retries; failed calls and malformed judgments remain failures.
    (dest/'final_lessons.json').write_text(json.dumps([dict(id=m['id'],lesson=m['lesson']) for m in agent.memory if m.get('lesson')],indent=2))
    if any(x['status']!='ok' for x in results):raise SystemExit(1)

if __name__=='__main__':main()
