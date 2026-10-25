# Current EHRAgent results

The obsolete scorer that missed serialized `cell` programs and its preliminary
reports were retired on 23 October 2026 at the user's request.

Use these results:

- [Controlled 183-question ablation findings](../../ehr_component_ablation/20261022T065501576965Z/FINDINGS.md):
  no defense **183/183**, checker only **81/183**, checker + lessons **79/183**
  detected generated deletion calls, with one unknown in the last arm.
- [Corrected historical assessment](../../baseline_reassessment/20261021T201859390372Z/REPORT.md):
  **181/183** without defense and **85/183** with defense, with two undefended
  truncated outputs unknown.

Raw episodes, API responses, split, trigger and frozen provenance remain under
`runs/20261016T180143Z`. Its old `metrics.json` is retained only as historical
audit data for old-versus-corrected comparisons; it is not the current scorer.
Frozen source copies likewise document past execution and are not active entry
points. Do not use their obsolete scorer for new evaluation.

The active run command now calls `scripts/ehragent/reassess_saved.py`, which uses
`EhrAgent/ehragent/code_assessment.py` and writes a new corrected report without
changing saved model outputs or calling a model.

Exact removed paths and hashes are in
[the retirement record](../../../notes/EHR_EVALUATION_RETIREMENT.json).
