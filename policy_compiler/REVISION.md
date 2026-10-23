# Revision: infer task needs, construct policy deterministically

The current CLI defaults to the revised method. The original direct-policy
compiler is preserved in `compiler.py` for baseline comparisons.

The fresh comparison is complete: the revision and keyword baseline each matched
18/20 annotated policies. See [evaluation/REVISION_RESULTS.md](evaluation/REVISION_RESULTS.md)
for the measured results, distinct failures, and limits on the conclusion.

```bash
.venv/bin/python -m policy_compiler \
  --request 'Send Alice the Project X update on Slack' \
  --context policy_compiler/evaluation/context.json
```

Use `--method original` to select the old compiler. In Python, use
`compile_task_policy(request, **context)` for the revision; `compile_policy` still
refers to the original API for backward compatibility and experiments. Both return
the unchanged `TaskPolicy` schema and enforce the same final validator.

## What changed

1. `effective.py` intersects user/global read sets, operation scopes, recipient
   sets, scalar argument restrictions, durations, and per-operation/aggregate
   action limits. Incompatible constraints remove an operation. The model sees
   one effective ceiling rather than two ceiling objects it must intersect.
2. `task_compiler.py` asks the model for a smaller `TaskNeeds` object: status,
   resource read needs/bases, operation/source IDs, recipient IDs, sends per
   recipient, questions, and a denial reason. It does not ask the model to invent
   expiration, tool argument restrictions, recipient quotes, or request excerpts.
3. Trusted code supplies expiration and argument constraints, adds read permissions
   for declared source inputs, and multiplies sends-per-recipient by recipient
   count. Excess needs result in a denial with no grants, never silent clamping.
4. `references.py` binds recipient IDs to exact names/aliases present in the
   authenticated request. Longest references avoid treating a full name as its
   ambiguous short alias. Ambiguous or absent identities require clarification.
5. Narrow lexical guards check source names/IDs (including reordered name words),
   and require an explicitly named tool when multiple permitted tools exist.
   Unresolved source/channel scope produces clarification with no grants.
6. Final policies are checked against both original ceilings by the deterministic
   validator. Denied/clarification results contain no permission grants.

Requests of up to 4,000 characters are used verbatim as evidence excerpts. Longer
valid requests retain their complete `original_request`, while evidence contains
the last 4,000 characters after removing trailing whitespace. Rationales identify
inference/assembly. This prevents invented quotations;
it does not prove that each inference follows from that request. Basis labels
remain model predictions. No model weights were trained and uncertainty is not
calibrated. Conditional per-recipient workflows remain outside the schema.

The lexical guards restrict the supported task language: implicit resources,
synonyms not represented in metadata, and unnamed supporting tools can trigger
unnecessary clarification. Word presence is not entailment; negation or relational
scope can still be misinterpreted. The method does not authenticate inputs, execute
tools, enforce counters/timers, or guarantee correct task intent.

The revised intent language focuses on reads, sources, recipients, tools, and
repetition counts. It does not express task-specific argument restrictions or
durations stricter than the effective ceiling. Those request types remain outside
this pilot's supported/scored scope; supplying trusted argument/expiration limits
does not establish that finer natural-language restrictions were understood.

## Development and frozen comparison

An intermediate revision was run once on the existing 20 development cases:
**11/20** policies matched annotated task scope. The preserved run is
`results/policy_compiler/development_revised/20261017T190007022688Z/`.
That intermediate version still requested quoted evidence/recipient references;
some model responses invented full names or paraphrased quotes. It also made
unnecessary clarifications and a channel guess.

After that development run, references/evidence construction moved to trusted
code and narrow source/channel guards were added. These changes were checked
offline. The final revision was frozen in
`evaluation/revision_freeze.json` **before** `fresh_context.json` and
`fresh_cases.json` were authored. No further method changes were made during
the fresh comparison. The intermediate 11/20 score is not a score for the final
revision; the fresh comparison supplies that measurement.

The comparison includes ceiling copying, a small English keyword parser, the
original LLM compiler, and the revision. The keyword parser shares deterministic
assembly/reference handling with the revision, helping identify whether the LLM
adds value. Both deterministic controls use only the request and trusted context.
They never see annotation labels. Both LLM methods use the configured OpenRouter
model with identical output/call budgets. Original calls precede revised calls,
so order/provider effects are not controlled.

The coding assistant authored these synthetic cases and annotations. They were
not independently human-reviewed, and they cover related task families rather
than a hidden benchmark or broad new application domains. Do not treat a high
score here as paper-level generalization or agent-security evidence.

## Reproduce

Run revised development cases (up to 20 API calls):

```bash
.venv/bin/python -m policy_compiler.evaluate --method revised
```

Run the fresh comparison (up to 40 API calls; deterministic controls cost no calls):

```bash
.venv/bin/python -m policy_compiler.compare
```

Run all offline compiler/scorer/comparison checks:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_policy*.py' -v
```

The offline suite passed **48 tests** before the final freeze. It checks effective
intersections, constraints, source-read dependencies, counts, trusted references,
clarification, invalid responses, baseline scoring, and mock-SDK comparison/report
replay. It does not measure model inference accuracy.

Only synthetic requests/metadata and model responses are logged. `.env`, the shared
OpenRouter client, original compiler/schema, development annotations, and prior
experimental artifacts are preserved. A-MemGuard is not connected to the compiler
in this milestone.

## Post-comparison audit

The later audit fixed long-request evidence overflow, bounded long aliases in
clarification questions (including the shared validator), and made comparison
report generation recompute scores from saved records and reject inconsistent
caches, inputs, source snapshots, and API-event bindings. Reports now use the
actual case count and avoid asserting an unverified order of case authorship.
The original comparison snapshots and freeze hashes remain historical records;
they are not hashes of this patched version. No prompt, annotation, scorer
definition, or live failure was changed to improve the recorded scores.

All **54 offline tests** pass. Saved responses reproduce the same accepted policies
and rejections for all 40 fresh calls and the earlier 20-call pilot. Both controls
also reproduce their 40 saved policies. No new API calls were made. See the
[audit record](../results/policy_compiler/audits/20261017T192916986749Z/AUDIT.md).
