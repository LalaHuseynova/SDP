"""Verify saved evaluation provenance and actual API inputs without paid calls."""
import argparse
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def verify(run):
    split=json.loads((run/'split.json').read_text());trigger=json.loads((run/'trigger_opt.json').read_text())['trigger_text']
    trigger_hash=hashlib.sha256((run/'trigger_opt.json').read_bytes()).hexdigest()
    names=['benign_no_defense','benign_amemguard','attack_no_defense','attack_amemguard'];memory_hashes=[];poison_manifests=[];evidence={}
    for name in names:
        directory=run/name;meta=json.loads((directory/'metadata.json').read_text());episodes=json.loads((directory/'episodes.json').read_text())
        calls={e['call']:e for e in map(json.loads,(directory/'api.jsonl').read_text().splitlines())}
        assert [e['id'] for e in episodes]==[q['id'] for q in split['evaluation']]
        assert len(episodes)==len(split['evaluation']) and len(calls)<=int(meta['budgets']['EHR_MAX_CALLS'])
        assert meta['trigger_artifact']['sha256']==trigger_hash
        assert meta['configuration']['trigger']==trigger
        assert meta['agent_model']==meta['judge_model']=='openai/gpt-4o-mini'
        assert len(meta['poisoned_records'])==4
        memory_hashes.append(meta['initial_memory_hash']);poison_manifests.append(meta['poisoned_records'])
        lesson_episodes=[]
        for e in episodes:
            expected=e['question']+(' '+trigger if name.startswith('attack_') else '')
            assert e['input_question']==expected
            if e['status']!='ok':continue
            first,last=e['api_call_range'];defended=name.endswith('amemguard')
            assert last-first+1==(7 if defended else 2)
            knowledge_call=last-1
            knowledge_prompt=calls[knowledge_call]['messages'][-1]['content'];code_prompt=calls[last]['messages'][-1]['content']
            assert e['retrieval']==e['trace']['delivered_knowledge_examples']
            assert e['retrieval'] in knowledge_prompt
            assert e['init_message']==e['trace']['code_input']==code_prompt
            assert set(e['trace']['poisoned_after'])==set(e['trace']['delivered_poisoned_ids'])
            for poison in meta['poisoned_records']:
                needle=f'Question: {poison["question"]}\nKnowledge:\n{poison["knowledge"]}'
                assert (needle in e['retrieval'])==(poison['id'] in e['trace']['delivered_poisoned_ids'])
            for lesson in e['trace']['lessons_used']:
                assert lesson['lesson'] in knowledge_prompt and lesson['lesson'] in code_prompt
                assert '[CRITICAL WARNING]' in knowledge_prompt and '[CRITICAL WARNING]' in code_prompt
            if e['trace']['lessons_used']:
                lesson_episodes.append(dict(id=e['id'],call_range=[first,last],lesson_ids=[l['id'] for l in e['trace']['lessons_used']]))
            assert all(calls[i]['requested_model']=='openai/gpt-4o-mini' for i in range(first,last+1))
        evidence[name]=dict(episodes=len(episodes),calls=len(calls),lessons_in_actual_api_inputs=lesson_episodes,
            successful=sum(e['status']=='ok' for e in episodes),failed_calls=sum(e['status']!='ok' for e in calls.values()))
    assert len(set(memory_hashes))==1 and all(p==poison_manifests[0] for p in poison_manifests)
    original=json.loads((ROOT/'results/small/ehr/provenance/initial_manifest.json').read_text())
    assert hashlib.sha256((ROOT/'.env').read_bytes()).hexdigest()==original['.env_sha256_only']
    result=dict(status='passed',identical_initial_poisoned_memory_hash=memory_hashes[0],trigger_sha256=trigger_hash,runs=evidence,env_unchanged=True)
    (run/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run_dir',type=Path);a=p.parse_args();verify(a.run_dir)
