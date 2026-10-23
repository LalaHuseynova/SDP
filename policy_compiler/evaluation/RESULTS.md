# Initial broad-context pilot: 2026-10-17

The unchanged prompt-based compiler was evaluated once on 20 synthetic requests
using a single broader context and the configured `openai/gpt-4o-mini` through
OpenRouter. Requests and annotations were frozen before the run. There was no
prompt tuning, output repair from annotations, or live retry during evaluation.

| Result | Count |
|---|---:|
| API calls/responses | 20/20 |
| Candidates rejected by the validator | 14/20 |
| Policies accepted by the validator | 6/20 |
| Accepted policies with correct status | 4/20 |
| Policies matching annotated task scope | **0/20** |
| Accepted policies granting excess task authority | 6/6 accepted |

All 14 rejected candidates first failed expiration validation: they specified
7,200 seconds although the global ceiling is 3,600 seconds. The validator stops
at the first error; these candidates may have additional errors. They are not
counted as successful denials or clarifications.

The four accepted, fully specified messaging requests granted three sends where
the task needed one. Three is the global per-operation maximum, which shows why
copying an access ceiling is insufficient for inferring task permissions. Two
other accepted policies guessed a project or messaging channel where the request
left that detail unresolved. Some accepted policies also omitted the required
source read. All six accepted policies stayed within the supplied access ceilings.

The seven expected denial/clarification cases produced no accepted result with
the correct status. This does not mean the raw model always selected the wrong
status: rejected candidates remain failures of the complete compilation pipeline.
Raw candidates and the first validation errors are preserved for diagnosis.

These results establish a failure of the current baseline on this diagnostic
specification. They do not establish general model accuracy, paper-level
performance, or an attack-success rate. There were no malicious memory records,
live tools, actual task completion, or independent human annotation in this pilot.

## Artifacts and verification

The complete [run report](../../results/policy_compiler/runs/20261017T184735253232Z/REPORT.md)
contains per-case outcomes. The same run contains frozen inputs, source snapshots,
model/settings/package metadata, incremental records, model responses, token usage,
and latency. Provider usage totals are 59,445 prompt tokens and 3,927 completion
tokens (63,372 total). Sum of API wall time is 63.23 seconds. No cost estimate is
inferred from those counts.

The original 23 compiler tests plus nine evaluator/scorer tests pass: **32 offline
tests**. The latter cover ceiling-valid task overgrants, missing grants, evidence
labels, optional reads, clarification-topic screening, report completeness, an
offline 20-response mock-SDK run, replay tampering, and disabled SDK retries with
sanitized failures. These tests verify implementation behavior, not LLM inference.

`.env`, the shared OpenRouter helper, compiler prompt, schema, and validator are
unchanged from the start of this evaluation. Source hashes in the run confirm
that evaluation code was unchanged during the live run. Existing agent pipelines
and earlier EHR results were not modified.

## Next method-development steps

1. Compute the intersection of trusted ceilings in deterministic code and expose
   effective limits clearly to the inference model. Expiration arithmetic should
   not depend solely on interpreting two duplicated ceiling objects.
2. Separate task-required grants from those effective ceilings, especially action
   counts and the source reads needed for an operation. Compare the resulting
   inference with the frozen baseline rather than silently changing its results.
3. Improve recognition of missing project/channel information and test whether
   clarification is useful, rather than merely matching a keyword.
4. Have humans review the annotation conventions, then evaluate any improved
   method on additional requests written before seeing those new model outputs.
   These 20 cases should remain a development set, not be relabeled as held-out.

These are recommendations; no compiler changes were made as part of this pilot.
