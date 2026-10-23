# Revised compiler: fresh comparison results

The revision fixes basic limit calculation and separates task-needs inference
from policy construction. It is the current CLI default. The original compiler
and schema are preserved. The shared validator later received the bounded-alias
audit fix documented below. No model was trained or calibrated.

An intermediate revision scored 11/20 on the old development set. After further
development on those observed failures, the final method was frozen before the
fresh inputs and annotations were written. There was no method tuning during the
fresh comparison. A later robustness/reporting audit is separate from that frozen
experiment; saved outputs and scores are unchanged.

## Fresh results

| Method | Correct task scope | Task-overgrant cases |
|---|---:|---:|
| Ceiling copying | 0/20 | 20/20 |
| Simple English keywords + trusted assembly | 18/20 | 1/20 |
| Original LLM compiler | 0/20 | 1/20 accepted; 19 rejected |
| Revised LLM compiler | **18/20** | **0/20 accepted; 2 rejected** |

The original and revised methods used the same configured GPT-4o-mini through
OpenRouter. Failures remain in the denominator. Both controls make no API calls.
The revision matched all 13 ready-policy annotations and all three clarification
annotations. Two of four denial cases returned valid denials; the other two were
rejected before any grants were accepted.

These are task-policy matches, not completed tasks or prevented real attacks.
The narrow synthetic tasks were authored and annotated by the coding assistant,
without independent human review. They include new entities, resource combinations,
wording, negation, and repetition, within related task families. They are not an
independent hidden benchmark or broad domain-generalization test.
The score covers status, read/source/recipient/operation sets, counts, evidence
labels, and a lexical clarification-topic screen. Task-specific argument values
and tighter requested durations were not varied; effective ceiling checks alone
do not establish understanding of those additional restrictions.

## What the failures show

The revised model emitted a denied/clarification status while still listing a
forbidden requested resource in its needs. The protocol requires non-ready
results to contain no grants, so both candidates were rejected. They are not
counted as correct denials, and the method was not changed to improve this score.

The keyword baseline failed on a negated sending instruction and on combining
words from two resource names into an extra resource grant. The revision handled
those cases, but failed the two denial-protocol cases the keyword baseline handled.
Their overall accuracy is tied. Thus this experiment shows engineering progress
over the original implementation, **not an overall accuracy advantage over simple
rules, proof of a learning contribution, or ICML-level novelty**.

The revision's gains combine several changes: deterministic ceiling intersection,
source-read dependencies, recipient/reference binding, per-recipient counts,
narrow lexical scope guards, a smaller output representation, and a different
prompt. This experiment does not isolate their individual contributions.

## Artifacts and verification

Read the full [comparison report](../../results/policy_compiler/comparisons/20261017T190552688095Z/COMPARISON.md).
Its directory preserves fresh inputs, annotations, source snapshots/hashes,
deterministic-control records, and both methods' raw model outputs and metrics.
`revision_freeze.json` records that method sources were frozen before fresh fixture
files existed. No expected-policy fields entered the inference prompts.

This work made exactly 60 live calls: 20 intermediate development calls and 40
fresh comparison calls, all with SDK retries disabled. Total provider-reported
usage was 164,841 tokens. No price is inferred. Verification at comparison completion
passed 49 offline tests. The later audit passes **54**; these implementation checks
are separate from live accuracy.

`.env`, the shared OpenRouter helper, original compiler/schema, original
development fixtures, and previous pilot artifacts were preserved. No task memory
or tools were executed; A-MemGuard is not connected in this milestone.

The [post-comparison audit](../../results/policy_compiler/audits/20261017T192916986749Z/AUDIT.md)
fixes long-request/alias handling and checks cached report scores against saved
records. It replays all fresh saved responses with identical policies/rejections,
without API calls or rewriting the original experiment artifacts. The recorded
18/20 score remains a measurement of the archived frozen version.

## Run the current version

```bash
.venv/bin/python -m policy_compiler \
  --request 'Send Dana the Orion release brief on Slack' \
  --context policy_compiler/evaluation/fresh_context.json
```

This makes one new API compilation call. Use `--method original` for the baseline.
Use `compile_task_policy` for the revised Python API; `compile_policy` remains the
original API. See [../REVISION.md](../REVISION.md) for design limits and commands.

The next scientific question is whether semantic inference improves performance
on tasks that strong deterministic baselines cannot solve, while avoiding extra
authority and unnecessary clarification. Human annotation review, more varied
tasks, and component ablations are needed before making that claim. A controlled
memory-gate integration can use this prototype, but it would test a further claim.
