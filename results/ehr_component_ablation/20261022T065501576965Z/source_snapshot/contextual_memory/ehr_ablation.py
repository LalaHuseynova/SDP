"""Matched EHRAgent ablation using native retrieval, checker, lessons and generation.

Generated programs are data only. The checker is computed once per question;
both defended arms consume that same native result. Lesson updates depend only
on checks, so contexts can be prepared in source order before parallel generation.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from contextvars import ContextVar
from copy import copy, deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

from .baseline_ablation import TrialCompletion
from .checker import build_checker
from .code_assessment import assess_code

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / 'results/full/ehr/runs/20261016T180143Z'
ARMS = ('no_defense', 'checker_only', 'checker_lessons')
ACTIVE = ContextVar('ehr_ablation_completion')


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def blocked_execution(*args, **kwargs):
    raise AssertionError('Generated code execution is forbidden in this study.')


def model_call(prompt, model=None, system=None, **kwargs):
    completion = ACTIVE.get()
    completion.binding['phase'] = 'code' if system and 'execution code' in system else 'knowledge'
    messages = ([dict(role='system', content=system)] if system else []) + [dict(role='user', content=prompt)]
    return completion(messages, model=model, **kwargs).choices[0].message.content.strip()


def native_check(query, memories, indexes, *args, **kwargs):
    completion = ACTIVE.get()
    completion.binding['phase'] = 'checker'
    return build_checker(completion).check(query, memories, list(indexes), method='llm')


class HistoricalReplay:
    """Replay the actual historical requests; reject any changed prompt/setting."""
    def __init__(self, events, model):
        self.events, self.model, self.position, self.binding = events, model, 0, {}

    def __call__(self, messages, model=None, **kwargs):
        from openai.types.chat import ChatCompletion
        event = self.events[self.position]
        parameters = dict(seed=0, top_p=1.0, frequency_penalty=0.0, presence_penalty=0.0,
                          temperature=0, max_tokens=1536) | kwargs
        expected = dict(temperature=event['temperature'], max_tokens=event['max_tokens']) | event['generation_kwargs']
        if messages != event['messages'] or parameters != expected or (model or self.model) != event['requested_model']:
            raise ValueError(f'Historical request mismatch at event {self.position}.')
        self.position += 1
        if event['status'] != 'ok':
            raise ValueError('Historical failed operation cannot be replayed as success.')
        return ChatCompletion.model_validate(event['response'])


def load_base(run):
    import torch
    sys.path.insert(0, str(ROOT / 'EhrAgent/ehragent'))
    import medagent
    from replication import digest, load_db_ehr
    torch.set_num_threads(4)
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    os.environ.update(EHR_DPR_PATH=str(ROOT / '.models/dpr'),
                     EHR_DPR_SHA256=sha(ROOT / '.models/dpr/pytorch_model.bin'),
                     TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='4')
    manifest = json.loads((run / 'manifest.json').read_text())
    def load(**kwargs):
        return load_db_ehr(db_dir=run / 'embedding_cache', **kwargs)
    with patch.object(medagent, 'load_db_ehr', side_effect=load), redirect_stdout(io.StringIO()):
        base = medagent.MedAgent(name='ehr_ablation', human_input_mode='NEVER', code_execution_config=False,
            config_list=[dict(model=manifest['model'])], num_shots=4, num_lessons=4,
            trigger_sequence=manifest['trigger'], backbone='openrouter',
            model_code='dpr-ctx_encoder-single-nq-base', audit_method=None)
        base.register_dataset('eicu')
    if digest(base.memory) != manifest['initial_memory_hash']:
        raise ValueError('Initial native memory differs from the historical experiment.')
    initial = deepcopy(base.memory)
    # Memoize identical eval-mode tensor forwards, retaining exact native numerics.
    original, cache = base.embedding_model.forward, {}
    def tensor_key(value):
        return (str(value.dtype), str(value.device), tuple(value.shape), value.detach().cpu().contiguous().numpy().tobytes()) if hasattr(value, 'detach') else repr(value)
    def forward(*args, **kwargs):
        identity = (tuple(tensor_key(v) for v in args), tuple((k, tensor_key(v)) for k, v in sorted(kwargs.items())))
        if identity not in cache:
            cache[identity] = original(*args, **kwargs)
        return cache[identity]
    base.embedding_model.forward = forward
    def factory(arm):
        agent = copy(base)
        agent.memory, agent.last_trace = deepcopy(initial), {}
        agent.audit_method = 'A-MemGuard' if arm != 'no_defense' else None
        agent.lessons_enabled, agent.memory_scope = arm == 'checker_lessons', None
        agent.execute_function = agent.execute_code_blocks = blocked_execution
        if any(m.get('lesson') for m in agent.memory):
            raise ValueError('An independent arm must start with empty lesson storage.')
        return agent
    return medagent, factory


def historical_parity(run, medagent, factory):
    old = json.loads((HISTORICAL / 'attack_amemguard/episodes.json').read_text())
    events = [json.loads(x) for x in (HISTORICAL / 'attack_amemguard/api.jsonl').read_text().splitlines()]
    agent, calls = factory('checker_lessons'), 0
    with patch.object(medagent, 'call_llm', side_effect=model_call), patch.object(medagent, 'check_consistency', side_effect=native_check):
        for row in old:
            first, last = row['api_call_range']
            replay = HistoricalReplay([e for e in events if first <= e['call'] <= last], agent.llm_model)
            token = ACTIVE.set(replay)
            try:
                with redirect_stdout(io.StringIO()):
                    message, code, knowledge, retrieval = agent.initiate_chat(None, message=row['input_question'])
                if (message, code, knowledge, retrieval) != tuple(row[k] for k in ('init_message', 'code', 'knowledge', 'retrieval')):
                    raise ValueError('Historical generated output differs in replay.')
                for key in ('retrieved_ids', 'surviving_ids', 'judge', 'lessons_stored', 'lessons_retrieved', 'lessons_used'):
                    if agent.last_trace[key] != row['trace'][key]:
                        raise ValueError('Historical native trace differs: ' + key)
                if replay.position != len(replay.events):
                    raise ValueError('Unconsumed historical requests.')
                calls += replay.position
            finally:
                ACTIVE.reset(token)
    report = dict(questions=len(old), exact_historical_requests=calls, new_api_calls=0,
                  native_generation_and_lesson_traces_match=True)
    save(run / 'historical_parity.json', report)
    print('Historical parity: ' + json.dumps(report), flush=True)


def prepare_inputs(run, tasks, factory, *, historical=False):
    agent, inputs = factory('no_defense'), []
    old = {r['id']:r for r in json.loads((HISTORICAL / 'attack_no_defense/episodes.json').read_text())}
    for index, task in enumerate(tasks):
        query = task['template'] + ' ' + json.loads((run / 'manifest.json').read_text())['trigger']
        with redirect_stdout(io.StringIO()):
            examples, knowledge_examples = agent.retrieve_embedding_examples(query)
        trace = deepcopy(agent.last_trace)
        if historical and trace['retrieved_ids'] != old[task['id']]['trace']['retrieved_ids']:
            raise ValueError('Native retrieval differs from historical IDs.')
        # Identical to the native per-memory formatting; native retrieval is verified above.
        memories = ['Question: {}\nKnowledge:\n{}\nSolution:\n{}\n'.format(
            agent.memory[i]['question'], agent.memory[i]['knowledge'], agent.memory[i]['code']) for i in trace['retrieved_indexes']]
        inputs.append(dict(id=task['id'], order=index, input_question=query, memories=memories,
            indexes=trace['retrieved_indexes'], trace=trace, examples=examples, knowledge_examples=knowledge_examples))
    return inputs


def checker_job(stage, item, model):
    directory = stage / 'checker' / item['id']
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / 'result.json'
    if output.exists():
        return json.loads(output.read_text())
    completion = TrialCompletion(directory, model, seed=0, max_calls=5, max_tokens=1536)
    completion.binding = dict(case_id=item['id'], configuration='shared_checker', condition='attack', phase='checker')
    row = dict(id=item['id'], status='pending', input=item)
    try:
        row.update(status='ok', result=build_checker(completion).check(
            item['input_question'], item['memories'], item['indexes'], method='llm'))
    except Exception as exc:
        row.update(status='failure', error_type=type(exc).__name__)
    row['api_calls'] = len(completion.events)
    save(output, row)
    return row


def materialize_contexts(stage, inputs, judgments, medagent, factory):
    lookup = {j['id']:j for j in judgments}
    contexts, final_lessons = [], {}
    active = None
    def fixed_check(query, memories, indexes, *args, **kwargs):
        original = active['input']
        if query != original['input_question'] or memories != original['memories'] or list(indexes) != original['indexes']:
            raise ValueError('A defended arm changed native checker inputs.')
        if active['status'] != 'ok':
            raise RuntimeError('Shared checker failed; no accepted context is available.')
        return deepcopy(active['result'])
    with patch.object(medagent, 'check_consistency', side_effect=fixed_check):
        for arm in ARMS:
            agent = factory(arm)
            for item in inputs:
                active = lookup[item['id']]
                row = dict(id=item['id'], order=item['order'], configuration=arm,
                           input_question=item['input_question'], status='pending')
                agent.last_trace = {}
                try:
                    with redirect_stdout(io.StringIO()):
                        examples, knowledge_examples = agent.retrieve_embedding_examples(item['input_question'])
                    row.update(status='ready', examples=examples, knowledge_examples=knowledge_examples)
                except Exception as exc:
                    row.update(status='checker_failure', error_type=type(exc).__name__)
                row['trace'] = deepcopy(agent.last_trace)
                if arm != 'checker_lessons' and (row['trace'].get('lessons_stored') or row['trace'].get('lessons_used') or any(m.get('lesson') for m in agent.memory)):
                    raise ValueError('Lessons leaked into a disabled arm.')
                contexts.append(row)
            final_lessons[arm] = {m['id']:m['lesson'] for m in agent.memory if m.get('lesson')}
    save(stage / 'contexts.json', contexts)
    save(stage / 'final_lessons.json', final_lessons)
    for item in inputs:
        a, b = [next(r for r in contexts if r['id']==item['id'] and r['configuration']==arm) for arm in ARMS[1:]]
        if a['status']=='ready' and b['status']=='ready':
            for key in ('retrieved_ids', 'judge', 'surviving_ids'):
                if a['trace'][key] != b['trace'][key]:
                    raise ValueError('The paired defended arms do not share checker decisions.')
    return contexts


def generation_job(stage, context, task, model, medagent, factory, *, replay=False):
    directory = stage / 'generation' / context['configuration'] / context['id']
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / 'result.json'
    if output.exists() and not replay:
        return json.loads(output.read_text())
    events = [json.loads(x) for x in (directory/'api.jsonl').read_text().splitlines()] if replay and (directory/'api.jsonl').exists() else None
    completion = TrialCompletion(directory, model, seed=0, replay=events, max_calls=2, max_tokens=1536)
    completion.binding = dict(case_id=context['id'], configuration=context['configuration'], condition='attack', phase='knowledge')
    row = dict(id=context['id'], order=context['order'], configuration=context['configuration'],
               question=task['template'], input_question=context['input_question'],
               ground_truth=task['answer'], reference_matches=task['reference_matches'],
               executed=False, final_answer=None, code='', knowledge='', trace=deepcopy(context['trace']))
    if context['status'] != 'ready':
        row.update(status='failure', error_type='SharedCheckerFailure', failure_stage='checker')
    else:
        agent = factory(context['configuration'])
        agent.last_trace = deepcopy(context['trace'])
        def frozen_retrieval(query):
            if query != context['input_question']:
                raise ValueError('Generation query differs from frozen retrieval.')
            return context['examples'], context['knowledge_examples']
        agent.retrieve_embedding_examples = frozen_retrieval
        token = ACTIVE.set(completion)
        try:
            message, code, knowledge, retrieval = agent.initiate_chat(None, message=context['input_question'])
            row.update(status='ok', code=code, knowledge=knowledge, init_message=message, retrieval=retrieval)
        except Exception as exc:
            row.update(status='failure', error_type=type(exc).__name__, failure_stage=completion.binding['phase'])
            # Preserve truncated/raw responses as evidence; primary failed outcomes remain unknown.
            row['partial_responses'] = [e.get('response') for e in completion.events if e.get('response')]
        finally:
            ACTIVE.reset(token)
        row['trace'] = deepcopy(agent.last_trace)
    row['assessment'] = assess_code(row['code'])
    row['api_calls'] = len(completion.events)
    row['token_usage'] = {k:sum((e.get('usage') or {}).get(k, 0) for e in completion.events)
                          for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    if replay:
        if completion.position != len(events or []):
            raise ValueError('Generation replay left unconsumed calls.')
        expected = json.loads(output.read_text())
        if row != expected:
            raise ValueError('Generation replay differs from saved result.')
    else:
        save(output, row)
    return row


def run_stage(run, stage_name, tasks, medagent, factory, workers):
    stage = run / stage_name
    stage.mkdir(exist_ok=True)
    manifest = json.loads((run / 'manifest.json').read_text())
    inputs = prepare_inputs(run, tasks, factory, historical=stage_name=='full')
    save(stage / 'checker_inputs.json', inputs)
    judgments = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(checker_job, stage, item, manifest['model']) for item in inputs]
        for future in as_completed(futures):
            row = future.result()
            judgments.append(row)
            print(f'{stage_name} checker {len(judgments)}/{len(inputs)}: {row["id"]} {row["status"]}', flush=True)
    judgments.sort(key=lambda j:j['input']['order'])
    save(stage / 'checker_results.json', judgments)
    contexts = materialize_contexts(stage, inputs, judgments, medagent, factory)
    task_by_id = {t['id']:t for t in tasks}
    records = []
    # This patch is shared; the ContextVar selects a separate recorded transport per worker.
    with patch.object(medagent, 'call_llm', side_effect=model_call), ThreadPoolExecutor(max_workers=workers) as pool:
        interleaved = sorted(contexts, key=lambda c:(c['order'], ARMS.index(c['configuration'])))
        futures = [pool.submit(generation_job, stage, c, task_by_id[c['id']], manifest['model'], medagent, factory) for c in interleaved]
        for future in as_completed(futures):
            row = future.result()
            records.append(row)
            print(f'{stage_name} generation {len(records)}/{len(contexts)}: {row["configuration"]} {row["id"]} {row["status"]} {row["assessment"]["outcome"]}', flush=True)
    records.sort(key=lambda r:(ARMS.index(r['configuration']), r['order']))
    save(stage / 'records.json', records)
    save(stage / 'status.json', dict(stage=stage_name, questions=len(tasks), records=len(records),
        failures=sum(r['status']!='ok' for r in records), checker_failures=sum(j['status']!='ok' for j in judgments)))
    return records


def source_paths():
    return [Path(p) for p in ('contextual_memory/ehr_ablation.py', 'contextual_memory/ehr_ablation_report.py',
        'contextual_memory/baseline_ablation.py', 'contextual_memory/ehr_transport.py', 'contextual_memory/checker.py',
        'contextual_memory/code_assessment.py', 'EhrAgent/ehragent/medagent.py', 'EhrAgent/ehragent/consistency.py',
        'EhrAgent/ehragent/prompts_eicu.py', 'EhrAgent/ehragent/llm_env.py', 'EhrAgent/ehragent/replication.py',
        'algo/utils.py', 'openrouter_client.py', 'tests/test_ehr_ablation.py')]


def prepare(workers):
    run = ROOT / 'results/ehr_component_ablation' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True, exist_ok=False)
    metadata = json.loads((HISTORICAL / 'attack_amemguard/metadata.json').read_text())
    split = json.loads((HISTORICAL / 'split.json').read_text())
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(), historical_run=str(HISTORICAL.relative_to(ROOT)),
        model=metadata['agent_model'], judge_model=metadata['judge_model'], trigger=metadata['configuration']['trigger'],
        initial_memory_hash=metadata['initial_memory_hash'], questions=len(split['evaluation']),
        distinct_question_texts=len({q['template'] for q in split['evaluation']}), workers=workers,
        arms=ARMS, memory='Historical 51 clean records + same 4 poisoned copies in every arm.',
        checker='Fresh native checker, five API calls per question, same result shared by both defended arms.',
        lessons='Empty at each independent stage/arm; native latest reasoning per rejected record overwrites prior value. Retrieve before current checks; inject into knowledge and code prompts.',
        scheduling='Materialize native contexts in original order; generation does not modify memory or lessons, so independent answer chains run concurrently.',
        execution='No generated code executed; execution entry points blocked.',
        generation_parameters=dict(temperature=0, seed=0, top_p=1.0, frequency_penalty=0.0, presence_penalty=0.0, max_tokens=1536),
        budgets=dict(smoke_questions=2, smoke_max_calls=22, full_max_calls=11*len(split['evaluation']), retries=0, timeout_seconds=60),
        limitations=['New date and provider routing can differ despite identical model alias/settings.',
            'One sequence; no claim of independence across repeated question texts or robust repeated-run significance.',
            'All arms have poisoned memory and triggered queries; no new clean-query clinical utility control.',
            'Clinical correctness unavailable without executing against the actual database.'])
    save(run / 'manifest.json', manifest)
    shutil.copy2(HISTORICAL / 'split.json', run / 'split.json')
    shutil.copy2(HISTORICAL / 'trigger_opt.json', run / 'trigger_opt.json')
    sources = {}
    for relative in source_paths():
        source = ROOT / relative
        if source.exists():
            dest = run / 'source_snapshot' / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            sources[str(relative)] = sha(source)
    sources['.models/dpr/pytorch_model.bin'] = sha(ROOT / '.models/dpr/pytorch_model.bin')
    manifest['source_hashes'] = sources
    manifest['historical_hashes'] = {str(p.relative_to(ROOT)):sha(p) for p in HISTORICAL.rglob('*') if p.is_file()}
    save(run / 'manifest.json', manifest)
    print('RUN_DIRECTORY=' + str(run), flush=True)
    return run


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path)
    p.add_argument('--stage', choices=('prepare', 'smoke', 'full', 'report', 'replay'), default='prepare')
    p.add_argument('--workers', type=int, choices=range(1, 9), default=6)
    args = p.parse_args()
    if args.stage == 'prepare':
        prepare(args.workers)
        return
    if not args.run:
        p.error('--run is required for an existing frozen experiment.')
    run = args.run.resolve()
    if args.stage == 'report':
        from .ehr_ablation_report import report
        report(run)
        return
    medagent, factory = load_base(run)
    split = json.loads((run / 'split.json').read_text())
    if args.stage == 'smoke':
        historical_parity(run, medagent, factory)
        tasks = split['smoke'] + split['evaluation'][:1]
        records = run_stage(run, 'smoke', tasks, medagent, factory, args.workers)
        if any(r['status'] != 'ok' for r in records):
            raise SystemExit('Smoke test failed. Full evaluation was not started.')
    elif args.stage == 'full':
        smoke = json.loads((run / 'smoke/status.json').read_text())
        parity = json.loads((run / 'historical_parity.json').read_text())
        if smoke['failures'] or smoke['records'] != 6 or parity['exact_historical_requests'] != 1281:
            raise ValueError('Successful smoke and historical parity are required.')
        run_stage(run, 'full', split['evaluation'], medagent, factory, args.workers)
        from .ehr_ablation_report import report
        report(run)
    elif args.stage == 'replay':
        contexts = json.loads((run / 'full/contexts.json').read_text())
        tasks = {t['id']:t for t in split['evaluation']}
        with patch.object(medagent, 'call_llm', side_effect=model_call):
            for context in contexts:
                generation_job(run / 'full', context, tasks[context['id']],
                    json.loads((run / 'manifest.json').read_text())['model'], medagent, factory, replay=True)
        print(f'Exact native generation replay passed: {len(contexts)} records; zero new API calls.', flush=True)


if __name__ == '__main__':
    main()
