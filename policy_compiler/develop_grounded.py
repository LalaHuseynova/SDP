"""Five exposed clinical cases: development diagnostics, never held-out accuracy."""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from contextual_memory.ehr import load_fixture
from contextual_memory.ehr_prepare import FIXTURE, ROOT
from contextual_memory.ehr_transport import EHRCompletion
from .evaluate import digest, save_json
from .grounded import compile_grounded_policy, keyword_grounded_policy


def run():
    from openrouter_client import get_model
    output = ROOT/'results/direction1_followup/development'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True, exist_ok=False)
    _, context, tasks, _, _ = load_fixture(FIXTURE)
    catalog_path = ROOT/'policy_compiler/evaluation/clinical_catalog.json'
    catalog = json.loads(catalog_path.read_text())
    model = get_model()
    sources = list((ROOT/'policy_compiler').glob('*.py')) + [catalog_path]
    hashes = {str(p.relative_to(ROOT)):digest(p) for p in sources}
    for path in sources:
        target = output/'source_snapshot'/path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    save_json(output/'manifest.json', dict(model=model, source_hashes=hashes,
        development_only=True, cases_previously_exposed=True, max_new_calls=5))
    completion = EHRCompletion(output, model)
    records = []
    for task in tasks:
        completion.binding = dict(case_id=task['id'], configuration='grounded', condition='development', phase='compile')
        entry = dict(id=task['id'], request=task['template'], expected_reads=task['allowed_tables'])
        try:
            policy = compile_grounded_policy(task['template'], **context.model_dump(),
                resource_catalog=catalog, model=model, completion=completion)
            entry.update(outcome='accepted', policy=policy.model_dump(), scope_match=(policy.status=='ready' and
                {r.resource_id for r in policy.reads}==set(task['allowed_tables'])))
        except Exception as exc:
            entry.update(outcome='error', error_type=type(exc).__name__, policy=None, scope_match=False)
        control = keyword_grounded_policy(task['template'], context, catalog)
        entry['keyword_scope_match'] = control.status=='ready' and {r.resource_id for r in control.reads}==set(task['allowed_tables'])
        records.append(entry)
        save_json(output/'records.json', records)
        print(task['id'], entry['outcome'], 'scope_match=',entry['scope_match'], flush=True)
    if any(digest(ROOT/p)!=h for p,h in hashes.items()):
        raise ValueError('Compiler changed during development measurement.')
    matches = sum(r['scope_match'] for r in records)
    save_json(output/'metrics.json', dict(cases=5, grounded_matches=matches,
        keyword_matches=sum(r['keyword_scope_match'] for r in records), new_api_calls=len(completion.events)))
    (output/'REPORT.md').write_text(f'# Clinical development check\n\nGrounded compiler: {matches}/5 scope matches.\n'
        'These five cases were already exposed and used for development. This is not a generalization result.\n'
        'Trusted aliases and dependency rules were added; no weights were trained.\n')
    save_json(output/'artifact_hashes.json', {str(p.relative_to(output)):digest(p) for p in output.rglob('*') if p.is_file()})
    print('Saved:',output.relative_to(ROOT))
    return output


if __name__=='__main__': run()
