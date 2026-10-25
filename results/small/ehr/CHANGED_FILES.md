# Changed files

Modified or added source/document files:
- `EhrAgent/ehragent/consistency.py`
- `EhrAgent/ehragent/llm_env.py`
- `EhrAgent/ehragent/main.py`
- `EhrAgent/ehragent/medagent.py`
- `EhrAgent/ehragent/replication.py`
- `EhrAgent/ehragent/utils.py`
- `algo/ehr_trigger_optimization.py`
- `algo/trigger_optimization.py`
- `algo/utils.py`
- `notes/paper_summary.md`
- `requirements-ehr-small.txt`
- `scripts/ehragent/evaluate_small.py`
- `scripts/ehragent/prepare_resources.py`
- `scripts/ehragent/report_small.py`
- `scripts/ehragent/run_comparison.sh`
- `scripts/ehragent/run_small.py`
- `scripts/ehragent/verify_run.py`
- `tests/test_ehr_replication.py`
- `REPLICATION_LOG.md` (append-only work log)

Added artifacts:
- `results/small/ehr/`: immutable run directories, trigger convenience artifact, report, offline fixture, provenance, caches.
- `EhrAgent/database/ehr_logs/logs_final/`: 50 original downloaded text files, per-file hashes in run split and resource log.
- `.models/dpr/` and `.models/bert/`: pinned local weights/tokenizers, hashes in provenance/log.
- `.venv/`: installed scikit-learn 1.7.2 and its dependencies; full before/after package snapshots retained.

No changes to `.env`, ReAct sources, existing embedding pickle files, root OpenRouter client, or original eval.py.
Archive before/after source diff: `provenance/changes.patch`. Source hashes: `provenance/changed_files.json`.
