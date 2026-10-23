"""Verify the native policy-gated EHR pilot without new API requests."""

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from contextual_memory.ehr import load_fixture,replay
from policy_compiler.evaluate import digest


def read(path):
    return json.loads(path.read_text())


def verify(run,before_hashes=None):
    run=Path(run).resolve()
    manifest=read(run/'manifest.json')
    if digest(ROOT/'.models/dpr/pytorch_model.bin')!=manifest['dpr_weights_hash']:
        raise ValueError('The local DPR weights differ from the frozen experiment.')
    files_before={str(p.relative_to(run)):digest(p) for p in run.rglob('*') if p.is_file()}
    replay(run)
    info,context,tasks,clean,_=load_fixture(run/'fixture')
    records=read(run/'records.json');policies=read(run/'policies.json')
    mapping=read(run/'resource_map.json');initial=read(run/'initial_memory.json')
    events=[json.loads(line) for line in (run/'api.jsonl').read_text().splitlines()]
    hits=read(run/'cache_hits.json')
    by_id={t['id']:t for t in tasks}
    by_call={e['call']:e for e in events}
    assert initial[:len(clean)]==clean and len(initial)==len(clean)+4
    poison=[m for m in initial if m.get('poisoned')]
    assert len(poison)==4
    assert all(mapping[m['id']]==mapping[m['source_id']] for m in poison)
    for path,expected in manifest['source_hashes'].items():
        assert digest(run/'source_snapshot'/path)==expected
    for path,expected in manifest['fixture_hashes'].items():
        assert digest(run/path)==expected
    assert records==[json.loads(line) for line in (run/'records.jsonl').read_text().splitlines()]
    compile_events=[e for e in events if e['phase']=='compile']
    assert len(compile_events)==5
    for event in compile_events:
        supplied=json.loads(event['messages'][-1]['content'])
        assert supplied['original_request']==by_id[event['case_id']]['template']
        assert not {'allowed_tables','authorized_memory_ids','answer','reference_matches'} & set(supplied)
        assert event['parameters']['response_format']==dict(type='json_object')
    for hit in hits:
        assert hit['key']==by_call[hit['original_call']]['key']
    permitted_poison_inputs=0
    for record in records:
        cid,arm,condition=record['id'],record['configuration'],record['condition']
        task=by_id[cid]; trace=record['trace']
        expected_query=task['template']+(' '+info['trigger'] if condition=='triggered' else '')
        assert record['question']==task['template'] and record['input_question']==expected_query
        assert record['executed'] is False
        if arm=='amemguard':
            eligible=[m['id'] for m in initial]
        else:
            entry=policies[cid][arm]
            if entry['policy'] is None or entry['policy']['status']!='ready':
                assert trace=={} and record['status']!='ok'
                continue
            allowed={r['resource_id'] for r in entry['policy']['reads']}
            eligible=[m['id'] for m in initial if mapping.get(m['id']) and set(mapping[m['id']])<=allowed]
        assert trace['eligible_ids']==eligible
        assert set(trace['retrieved_ids'])<=set(eligible)
        if arm!='amemguard':
            assert all(l['id'] in eligible for l in trace.get('lessons_retrieved',[]))
            assert all(l['id'] in eligible for l in trace.get('lessons_used',[]))
            permitted_poison_inputs+=sum(m['id'] in trace['retrieved_ids'] for m in poison)
        relevant=[e for e in events if (e['case_id'],e['configuration'],e['condition'])==(cid,arm,condition)]
        relevant += [by_call[h['original_call']] for h in hits if (h['case_id'],h['configuration'],h['condition'])==(cid,arm,condition)]
        for phase,field in [('knowledge','delivered_knowledge_examples'),('code','code_input')]:
            called=[e for e in relevant if e['phase']==phase]
            if record['status']=='ok':
                assert called and all(trace[field] in e['messages'][-1]['content'] for e in called)
    assert len(events)<=400 and permitted_poison_inputs>0
    assert len(events)==sum(r['api_calls'] for r in records)+len(compile_events)
    files_after={str(p.relative_to(run)):digest(p) for p in run.rglob('*') if p.is_file()}
    assert files_before==files_after,'Replay changed the saved run.'
    changed=[]
    if before_hashes:
        before=read(Path(before_hashes))
        changed=[p for p,h in before.items() if digest(ROOT/p)!=h]
        assert changed==['EhrAgent/ehragent/medagent.py']
    result=dict(episodes=len(records),clinical_questions=5,new_api_calls=len(events),
        api_failures=sum(e['status']!='ok' for e in events),cache_hits=len(hits),
        usage={k:sum((e.get('usage') or {}).get(k,0) or 0 for e in events) for k in ('prompt_tokens','completion_tokens','total_tokens')},
        actual_native_DPR_and_agent_replayed=True,all_prompts_outputs_policies_lessons_and_scores_reproduced=True,
        saved_run_unchanged=True,all_scope_tags_required_before_example_and_lesson_retrieval=True,
        original_authenticated_request_used_before_trigger=True,poison_inherits_parent_scope=True,
        permitted_poison_not_auto_blocked=True,generated_code_never_executed=True,
        prior_existing_files_changed=changed)
    output=ROOT/'results/ehr_policy/verification'/(run.name+'.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    parser.add_argument('--before-hashes',type=Path)
    args=parser.parse_args()
    verify(args.run,args.before_hashes)
