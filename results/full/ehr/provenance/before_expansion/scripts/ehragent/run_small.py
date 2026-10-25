"""One-command small pilot. Every invocation creates a fresh run; no stale trigger fallback."""
import argparse
import datetime
import hashlib
import json
import os
import random
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'EhrAgent/ehragent'))
from replication import load_ehr_memory,digest

def log(message):
    with (ROOT/'REPLICATION_LOG.md').open('a') as f:f.write('\n'+message+'\n')
    print(message,flush=True)

def command(argv,env,log_path):
    log('Command argv (no shell): '+json.dumps([str(v) for v in argv]))
    with log_path.open('w') as output:
        result=subprocess.run([str(v) for v in argv],cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT)
    log(f'Exit={result.returncode}; output={log_path.relative_to(ROOT)}')
    if result.returncode:raise RuntimeError('Stage failed: '+str(log_path))

def select_split(run):
    data=json.loads((ROOT/'EhrAgent/database/ehr_logs/eicu_ac.json').read_text())
    memory=load_ehr_memory('EhrAgent/database/ehr_logs/logs_final')
    groups={}
    for row in data:groups.setdefault(row['id'],[]).append(row)
    # Canonical representative: first source-file occurrence; retain duplicate/conflict evidence.
    rows=[groups[k][0] for k in sorted(groups)]
    rng=random.Random(0);rng.shuffle(rows)
    def annotate(row):
        return dict(id=row['id'],template=row['template'],answer=row['answer'],
            reference_matches=[dict(id=m['id'],code=m['code'],knowledge=m['knowledge']) for m in memory if m['question'].strip()==row['template'].strip()])
    split=dict(seed=0,selection='Sort unique IDs, keep first source occurrence, random.Random(0).shuffle; first16 training,next20 evaluation,next1 smoke',
        source_rows=len(data),unique_ids=len(groups),duplicate_ids={k:len(v) for k,v in groups.items() if len(v)>1},
        duplicate_conflicts=[k for k,v in groups.items() if len({(x['template'],str(x['answer'])) for x in v})>1],
        training=[annotate(x) for x in rows[:16]],evaluation=[annotate(x) for x in rows[16:36]],smoke=[annotate(x) for x in rows[36:37]],
        memory_count=len(memory),memory_hash=digest(memory),memory_source='50 text files exposed by documented public Drive folder initial listing; incomplete corpus',
        source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'EhrAgent/database/ehr_logs/eicu_ac.json']+sorted((ROOT/'EhrAgent/database/ehr_logs/logs_final').glob('*.txt'))})
    assert not set(x['id'] for x in split['training'])&set(x['id'] for x in split['evaluation'])
    path=run/'split.json';path.write_text(json.dumps(split,indent=2))
    log(f'Split saved: 16 train,20 held-out,1 smoke; memory records={len(memory)}, missing evaluation reference matches={sum(not x["reference_matches"] for x in split["evaluation"])}; no questions dropped')
    return path

def main():
    p=argparse.ArgumentParser();p.add_argument('--output_root',default=str(ROOT/'results/small/ehr/runs'))
    a=p.parse_args();run=Path(a.output_root)/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run.mkdir(parents=True,exist_ok=False)
    env=os.environ.copy();env.update(AGENT_MODEL='openai/gpt-4o-mini',JUDGE_MODEL='openai/gpt-4o-mini',JUDGE_TEMPERATURE='0',SHUFFLE_SEED='0',
        EHR_DPR_PATH=str(ROOT/'.models/dpr'),EHR_DPR_SHA256=hashlib.sha256((ROOT/'.models/dpr/pytorch_model.bin').read_bytes()).hexdigest(),
        EHR_MAX_OUTPUT_TOKENS='1536',EHR_MAX_INPUT_TOKEN_BOUND='32000',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='4',PYTHONHASHSEED='0')
    # Per-process budgets: smoke 2/7 calls, each final run 40/140; total <=369 calls.
    stages={};(run/'stages.json').write_text(json.dumps(stages))
    try:
        split=select_split(run);stages['prepare']='completed'
        def episode_args(dest,trigger,key='evaluation',defense=False,attack=False,artifact=None):
            argv=[sys.executable,ROOT/'EhrAgent/ehragent/main.py','--subset_file',split,'--subset_key',key,'--num_questions',1 if key=='smoke' else 20,
                '--seed',0,'--num_shots',4,'--num_lessons',4,'--llm',env['AGENT_MODEL'],'--trigger',trigger,'--save_dir',dest]
            if defense:argv+=['--audit_method','A-MemGuard']
            if attack:argv+=['--attack']
            if artifact:argv+=['--trigger_artifact',artifact]
            return argv
        for defense in [False,True]:
            name='smoke_defended' if defense else 'smoke_baseline'
            bounded=env|{'EHR_MAX_CALLS':str(7 if defense else 2),'EHR_MAX_TOKEN_BOUND':str((7 if defense else 2)*33536)}
            command(episode_args(run/name,'temporary smoke trigger',key='smoke',defense=defense,attack=True),bounded,run/(name+'.stdout.txt'))
            stages[name]='completed'
        artifact=run/'trigger_opt.json'
        command([sys.executable,ROOT/'algo/trigger_optimization.py','--agent','ehr','--algo','ap','--model','dpr-ctx_encoder-single-nq-base',
            '--split_file',split,'--output',artifact,'--num_iter',10,'--num_proposals',32,'--num_cand',16,'--num_adv_passage_tokens',10],env,run/'optimization.stdout.txt')
        obj=json.loads(artifact.read_text());trigger=obj.get('trigger_text')
        if obj.get('status')!='success' or not isinstance(trigger,str) or not trigger.strip():raise ValueError('Invalid newly-generated trigger')
        stages['optimization']='completed'
        # Publish the requested location only from this invocation's successful optimization; preserve older files.
        canonical=ROOT/'results/small/ehr/trigger_opt.json'
        if canonical.exists():
            backup=canonical.with_name('trigger_opt.'+hashlib.sha256(canonical.read_bytes()).hexdigest()[:12]+'.json')
            if not backup.exists():backup.write_bytes(canonical.read_bytes())
        canonical.write_bytes(artifact.read_bytes())
        for attack,defense,name in [(False,False,'benign_no_defense'),(False,True,'benign_amemguard'),(True,False,'attack_no_defense'),(True,True,'attack_amemguard')]:
            calls=140 if defense else 40
            bounded=env|{'EHR_MAX_CALLS':str(calls),'EHR_MAX_TOKEN_BOUND':str(calls*33536)}
            command(episode_args(run/name,trigger,defense=defense,attack=attack,artifact=artifact),bounded,run/(name+'.stdout.txt'))
            stages[name]='completed'
        stages['evaluation']='completed'
        (run/'stages.json').write_text(json.dumps(stages,indent=2))
        command([sys.executable, ROOT/'scripts/ehragent/verify_run.py', run], env, run/'verification.stdout.txt')
        stages['verification']='completed'
        command([sys.executable, ROOT/'scripts/ehragent/evaluate_small.py', run], env, run/'metrics.stdout.txt')
        stages['metrics']='completed'
        (run/'stages.json').write_text(json.dumps(stages,indent=2))
        command([sys.executable, ROOT/'scripts/ehragent/report_small.py', run], env, run/'report.stdout.txt')
        stages['report']='completed'
    except Exception as exc:
        stages['failed']=dict(type=type(exc).__name__,message=str(exc));log('Stopped: '+str(exc))
        raise
    finally:
        (run/'stages.json').write_text(json.dumps(stages,indent=2));log('Run artifacts: '+str(run))

if __name__=='__main__':main()
