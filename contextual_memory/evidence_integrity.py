"""Verify evidence hashes while reporting explicitly authorized report retirement.

Never substitute a recorded hash for an existing file's actual hash. Missing
raw outputs are errors. Frozen source snapshots are checked independently.
"""

import hashlib
import json
from pathlib import Path


def audit_hashes(root, expected):
    root = Path(root)
    record = root / 'notes/EHR_EVALUATION_RETIREMENT.json'
    cleanup = json.loads(record.read_text()) if record.exists() else {}
    deleted = cleanup.get('deleted_files', {})
    maintenance = cleanup.get('maintenance_files', {})
    # These two changes concern report integrity and future snapshot coverage.
    # No exception is permitted for checker, generation, retrieval or evaluator code.
    permitted_maintenance = {'contextual_memory/ehr_ablation_report.py', 'contextual_memory/ehr_ablation.py'}
    result = {}
    for name, original in expected.items():
        path = root / name
        if path.is_file():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual == original:
                result[name] = 'unchanged'
            elif (name in permitted_maintenance and maintenance.get(name, {}).get('before_sha256') == original
                  and maintenance[name].get('after_sha256') == actual):
                result[name] = 'documented_maintenance'
            else:
                raise ValueError('Unexpected changed evidence/source: ' + name)
        elif (path.name.startswith('PRELIMINARY_RESULTS') and path.suffix == '.md'
              and deleted.get(name, {}).get('kind') == 'obsolete_report'
              and deleted[name].get('sha256') == original):
            result[name] = 'retired_report'
        else:
            raise ValueError('Missing required evidence/source: ' + name)
    return result
