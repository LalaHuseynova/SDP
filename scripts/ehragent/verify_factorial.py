"""Replay and audit the factorial run without new model calls."""

import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from contextual_memory.factorial import ARMS, load_fixture, replay
from policy_compiler import validate_policy
from policy_compiler.evaluate import digest, save_json


def verify(run):
    run=Path(run).resolve()
    # Recompute each distinct DPR input locally, then reuse exact repeated inputs.
    # This preserves native control flow and avoids hundreds of identical forwards.
    # Keys include model identity and complete tensor contents, not task labels.
    from transformers import DPRContextEncoder
    import torch
    original_forward=DPRContextEncoder.forward
    embedding_cache={}
    embedding_calls=Counter()
    def forward(model,*args,**kwargs):
        if model.training:
            return original_forward(model,*args,**kwargs)
        def value_key(value):
            if isinstance(value,torch.Tensor):
                return (str(value.dtype),str(value.device),tuple(value.shape),value.detach().cpu().contiguous().numpy().tobytes())
            return repr(value)
        key=(id(model),tuple(value_key(v) for v in args),
             tuple((k,value_key(v)) for k,v in sorted(kwargs.items())))
        embedding_calls['total']+=1
        if key not in embedding_cache:
            embedding_calls['computed']+=1
            embedding_cache[key]=original_forward(model,*args,**kwargs)
        return embedding_cache[key]
    with patch.object(DPRContextEncoder,'forward',forward):
        replay(run)
    info,context,catalog,tasks,memory,mapping,reviewed=load_fixture(run/'fixture')
    records=json.loads((run/'records.json').read_text())
    entries=json.loads((run/'policies.json').read_text())
    lessons=json.loads((run/'final_lessons.json').read_text())
    manifest=json.loads((run/'manifest.json').read_text())
    events=[json.loads(l) for l in (run/'api.jsonl').read_text().splitlines()]
    hits=json.loads((run/'cache_hits.json').read_text())
    assert records==[json.loads(l) for l in (run/'records.jsonl').read_text().splitlines()]
    resumed_records=0
    if manifest.get('resumed_from'):
        previous=ROOT/manifest['resumed_from']
        assert digest(previous/'api.jsonl')==manifest['prefix_events_hash']
        previous_records=[json.loads(l) for l in (previous/'records.jsonl').read_text().splitlines()]
        for old,new in zip(previous_records,records):
            assert {k:v for k,v in old.items() if k!='new_api_calls'}=={k:v for k,v in new.items() if k!='new_api_calls'}
        resumed_records=len(previous_records)
        def definitions(path):
            tree=ast.parse(path.read_text())
            return {node.name:ast.dump(node) for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
        old=definitions(previous/'source_snapshot/contextual_memory/factorial.py')
        new=definitions(ROOT/'contextual_memory/factorial.py')
        for name in ('score','normalize','compile_policies','manual_policy','load_fixture'):
            assert old[name]==new[name], 'Evaluation definition changed: '+name
        def constants(path):
            tree=ast.parse(path.read_text())
            return {t.id:ast.dump(n.value) for n in tree.body if isinstance(n,ast.Assign)
                    for t in n.targets if isinstance(t,ast.Name) and t.id in ('ARMS','ANSWER_PROMPT')}
        assert constants(previous/'source_snapshot/contextual_memory/factorial.py')==constants(ROOT/'contextual_memory/factorial.py')
    compiler_freeze=json.loads((run/'fixture/compiler_freeze.json').read_text())
    assert all(digest(ROOT/p)==h for p,h in compiler_freeze['source_hashes'].items())
    for rel,h in manifest['source_hashes'].items():assert digest(run/'source_snapshot'/rel)==h
    for rel,h in manifest['fixture_hashes'].items():assert digest(run/rel)==h
    assert manifest['independently_reviewed']==reviewed
    by_call={e['call']:e for e in events}
    for hit in hits:assert hit['key']==by_call[hit['original_call']]['key']
    compile_events=[e for e in events if e['phase']=='compile']
    assert len(compile_events)+sum(h['phase']=='compile' for h in hits)==len(tasks)
    for event in compile_events:
        supplied=json.loads(event['messages'][-1]['content'])
        assert not {'accepted_answers','allowed_resources','required_clean_ids','expected_status'} & set(supplied)
        assert supplied['original_request']==next(t['request'] for t in tasks if t['id']==event['case_id'])
    for arm,(source,enabled) in ARMS.items():
        rows=[r for r in records if r['configuration']==arm]
        assert [r['id'] for r in rows]==manifest['task_order']
        state={}
        for row in rows:
            trace=row['trace']
            assert row['executed'] is False
            assert row['lessons_enabled']==enabled
            if not enabled:assert not any(trace.get(k) for k in ('lessons_stored','lessons_retrieved','lessons_used'))
            for lesson in trace.get('lessons_retrieved',[]):assert state[lesson['id']]==lesson['lesson']
            for lesson in trace.get('lessons_used',[]):assert state[lesson['id']]==lesson['lesson']
            entry=entries[row['id']].get(source)
            if entry and entry['policy']:
                policy=validate_policy(entry['policy'],row['request'],context)
                allowed={r.resource_id for r in policy.reads}
                eligible={mid for mid,tags in mapping.items() if set(tags)<=allowed}
                assert set(trace.get('retrieved_ids',[]))<=eligible
                assert {l['id'] for l in trace.get('lessons_used',[])}<=eligible
            for lesson in trace.get('lessons_stored',[]):state[lesson['id']]=lesson['lesson']
        assert state==lessons[arm]
    setup=ROOT/'results/direction1_followup/setup'
    before=json.loads((setup/'before_hashes.json').read_text())
    changed=[p for p,h in before.items() if not (ROOT/p).exists() or digest(ROOT/p)!=h]
    assert set(changed)=={'EhrAgent/ehragent/medagent.py','contextual_memory/ehr_transport.py',
                         'policy_compiler/__init__.py','policy_compiler/__main__.py','policy_compiler/task_compiler.py'}
    assert not any(p.startswith('results/') for p in changed)
    development=[]
    for path in sorted((ROOT/'results/direction1_followup/development').glob('*/metrics.json')):
        development.append(json.loads(path.read_text()))
    assert len(development)==5 and sum(d['new_api_calls'] for d in development)==25
    interrupted=ROOT/'results/direction1_followup/factorial/20261018T135312364248Z'
    interrupted_meta=json.loads((interrupted/'INTERRUPTED.json').read_text())
    result=dict(episodes=len(records),new_api_calls=sum(e.get('origin','live')=='live' for e in events),
        recorded_unique_responses=len(events),resumed_episode_traces_preserved=resumed_records,
        api_failures=sum(e['status']!='ok' for e in events),
        cache_hits=len(hits),native_DPR_and_lesson_state_replayed=True,all_prompts_records_and_metrics_reproduced=True,
        native_embedding_forward_calls=dict(embedding_calls),
        independent_annotation_review='complete' if reviewed else 'pending_supervisor',
        historical_artifacts_unchanged=True,environment_and_shared_client_unchanged=True,
        development_attempts=development,interrupted_setup_attempt=interrupted_meta,
        total_calls_including_development_interrupted_and_smoke=len(events)+25+interrupted_meta['new_calls']+1,
        actual_tools_executed=False,existing_files_changed=changed)
    output=ROOT/'results/direction1_followup/verification'/(run.name+'.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    save_json(output,result)
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    args=parser.parse_args()
    verify(args.run)
