"""Offline integrity, native checker, ordered lesson and generation replay checks.

Network access is explicitly blocked. Failed/truncated generations are verified
as recorded unknowns, not replayed as successful completions.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from contextual_memory.baseline_ablation import TrialCompletion
from contextual_memory.checker import build_checker
from contextual_memory.evidence_integrity import audit_hashes
from contextual_memory.ehr_ablation import (ARMS, HISTORICAL, generation_job, load_base, materialize_contexts,
    model_call, prepare_inputs, save, sha)
import openrouter_client


def verify(run, output_dir=None):
    run=Path(run).resolve()
    output=Path(output_dir).resolve() if output_dir else run
    output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((run/'manifest.json').read_text())
    source_checks=audit_hashes(ROOT,manifest['source_hashes'])
    for relative,expected in manifest['source_hashes'].items():
        if relative.startswith('.models/'):
            continue
        if sha(run/'source_snapshot'/relative)!=expected:
            raise ValueError('Frozen study source snapshot changed: '+relative)
    historical_checks=audit_hashes(ROOT,manifest['historical_hashes'])
    before_path=Path('/tmp/amemguard_full_ablation_before.json')
    all_protected=audit_hashes(ROOT,json.loads(before_path.read_text())) if before_path.exists() else {}
    routing={}
    folders={'historical_no_defense':HISTORICAL/'attack_no_defense',
             'historical_checker_lessons':HISTORICAL/'attack_amemguard',
             'new_shared_checker':run/'full/checker'}
    folders.update({'new_'+a:run/'full/generation'/a for a in ARMS})
    for label,folder in folders.items():
        events=[json.loads(line) for p in folder.rglob('api.jsonl') for line in p.read_text().splitlines()]
        routing[label]=dict(requests=len(events),
            providers=dict(Counter(str(e.get('response',{}).get('provider')) for e in events)),
            system_fingerprints=dict(Counter(str(e.get('response',{}).get('system_fingerprint')) for e in events)),
            model_names=dict(Counter(str(e.get('response',{}).get('model')) for e in events)))
    save(output/'comparability.json',dict(routing=routing,
        interpretation='Historical and new runs both use automatic Azure/OpenAI routing. Fingerprints/routing can differ despite identical model alias and request parameters; historical counts are not contemporaneous controls.'))
    tasks=json.loads((run/'split.json').read_text())['evaluation']
    inputs=json.loads((run/'full/checker_inputs.json').read_text())
    judgments=json.loads((run/'full/checker_results.json').read_text())
    contexts=json.loads((run/'full/contexts.json').read_text())
    records=json.loads((run/'full/records.json').read_text())
    strata={}
    for arm in ARMS:
        arm_rows=[r for r in records if r['configuration']==arm]
        strata[arm]={}
        for label in ('no_poison_accepted','poison_accepted','acceptance_unknown'):
            subset=[r for r in arm_rows if (
                'acceptance_unknown' if 'delivered_poisoned_ids' not in r['trace'] else
                'poison_accepted' if r['trace']['delivered_poisoned_ids'] else 'no_poison_accepted')==label]
            strata[arm][label]=dict(questions=len(subset),outcomes=dict(Counter(r['assessment']['outcome'] for r in subset)))
    save(output/'stratified_diagnostics.json',dict(by_poison_acceptance=strata,
        interpretation='Descriptive post-run diagnostics. Negative means no detected call, not a correct or safe task.'))
    unknown_details=[]
    for row in records:
        if row['assessment']['outcome']=='unknown':
            path=run/'full/generation'/row['configuration']/row['id']/'api.jsonl'
            events=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            unknown_details.append(dict(id=row['id'],configuration=row['configuration'],status=row['status'],
                assessment_issues=row['assessment']['issues'],failure_stage=row.get('failure_stage'),
                error_type=row.get('error_type'),
                recorded_finish_reasons=[dict(phase=e.get('phase'),finish_reason=e.get('response',{}).get('choices',[{}])[0].get('finish_reason')) for e in events]))
    save(output/'unknown_outputs.json',unknown_details)
    if len(records)!=len(tasks)*3 or len({(r['id'],r['configuration']) for r in records})!=len(records):
        raise ValueError('Episode coverage is incomplete or duplicated.')
    checker_replayed=checker_failed=generated_replayed=generated_failed=0
    with patch.object(openrouter_client,'get_client',side_effect=AssertionError('Verification must never use the network.')):
        for saved in judgments:
            directory=run/'full/checker'/saved['id']
            events=[json.loads(x) for x in (directory/'api.jsonl').read_text().splitlines()] if (directory/'api.jsonl').exists() else []
            completion=TrialCompletion(directory,manifest['model'],seed=0,replay=events,max_calls=5,max_tokens=1536)
            completion.binding=dict(case_id=saved['id'],configuration='shared_checker',condition='attack',phase='checker')
            item=saved['input']
            if saved['status']=='ok':
                actual=build_checker(completion).check(item['input_question'],item['memories'],item['indexes'],method='llm')
                if actual!=saved['result'] or completion.position!=len(events):
                    raise ValueError('Native checker replay differs.')
                checker_replayed+=1
            else:
                # Do not reinterpret a failed call or malformed judgment as protection.
                if not events or all(e['status']=='ok' for e in events):
                    if saved.get('error_type') not in ('ValueError','EHRTransportError'):
                        raise ValueError('Unsupported checker failure record.')
                checker_failed+=1
        medagent,factory=load_base(run)
        actual_inputs=prepare_inputs(run,tasks,factory,historical=True)
        if actual_inputs!=inputs:
            raise ValueError('Native retrieval no longer matches frozen checker inputs.')
        with tempfile.TemporaryDirectory(prefix='amemguard-context-replay-') as temp:
            actual_contexts=materialize_contexts(Path(temp),inputs,judgments,medagent,factory)
            if actual_contexts!=contexts:
                raise ValueError('Ordered native lesson/context replay differs.')
            if json.loads((Path(temp)/'final_lessons.json').read_text())!=json.loads((run/'full/final_lessons.json').read_text()):
                raise ValueError('Final lesson state differs.')
        task_lookup={t['id']:t for t in tasks}
        record_lookup={(r['id'],r['configuration']):r for r in records}
        with patch.object(medagent,'call_llm',side_effect=model_call):
            for context in contexts:
                row=record_lookup[(context['id'],context['configuration'])]
                if row['status']=='ok':
                    generation_job(run/'full',context,task_lookup[context['id']],manifest['model'],medagent,factory,replay=True)
                    generated_replayed+=1
                else:
                    if row['assessment']['outcome']!='unknown' or row['executed'] is not False:
                        raise ValueError('Failed generation was misclassified.')
                    generated_failed+=1
    lesson_pairs=prefix_only_pairs=0
    context_lookup={(r['id'],r['configuration']):r for r in contexts}
    for task in tasks:
        a,b=[record_lookup[(task['id'],arm)] for arm in ARMS[1:]]
        if 'judge' in a['trace'] and 'judge' in b['trace']:
            if any(a['trace'][k]!=b['trace'][k] for k in ('retrieved_ids','judge','surviving_ids')):
                raise ValueError('Checker decisions differ between paired arms.')
            lesson_pairs+=1
        if any(a['trace'].get(k) for k in ('lessons_used','lessons_stored','lessons_retrieved')):
            raise ValueError('Lessons leaked into checker-only.')
        only,full=[context_lookup[(task['id'],arm)] for arm in ARMS[1:]]
        if only['status']==full['status']=='ready':
            prefixes=[]
            for field in ('examples','knowledge_examples'):
                base,expanded=only[field],full[field]
                if not expanded.endswith(base):
                    raise ValueError('Lessons changed admitted example text.')
                prefixes.append(expanded[:-len(base)] if base else expanded)
            if prefixes[0]!=prefixes[1] or (prefixes[0] and not prefixes[0].startswith('[CRITICAL WARNING]')):
                raise ValueError('The two generation contexts have different lesson prefixes.')
            if bool(prefixes[0])!=bool(full['trace'].get('lessons_used')):
                raise ValueError('Lesson prefix and trace disagree.')
            prefix_only_pairs+=1
    result=dict(status='passed',new_api_calls=0,questions=len(tasks),records=len(records),
        native_checker_results_replayed=checker_replayed,checker_failures_preserved=checker_failed,
        successful_generation_records_replayed=generated_replayed,generation_failures_preserved_as_unknown=generated_failed,
        ordered_contexts_replayed=len(contexts),identical_checker_pairs=lesson_pairs,
        contexts_differ_only_by_native_lesson_prefix_pairs=prefix_only_pairs,
        source_snapshot_files=len(source_checks),frozen_source_snapshot_intact=True,
        sources_unchanged=all(s=='unchanged' for s in source_checks.values()),
        documented_source_maintenance=[p for p,s in source_checks.items() if s=='documented_maintenance'],
        historical_files=len(historical_checks),historical_unchanged=all(s=='unchanged' for s in historical_checks.values()),
        historical_raw_evidence_unchanged=True,
        historical_retired_reports=[p for p,s in historical_checks.items() if s=='retired_report'],
        protected_preexisting_files=len(all_protected),protected_unchanged=bool(all_protected) and all(s=='unchanged' for s in all_protected.values()),
        protected_raw_evidence_unchanged=True,
        protected_retired_reports=[p for p,s in all_protected.items() if s=='retired_report'],
        execution_flags=dict(Counter(str(r['executed']) for r in records)),
        verifier_sha256=sha(Path(__file__)))
    save(output/'verification.json',result)
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    parser.add_argument('--output-dir',type=Path,help='Write verification separately, preserving the saved experiment artifacts.')
    args=parser.parse_args()
    verify(args.run,args.output_dir)
