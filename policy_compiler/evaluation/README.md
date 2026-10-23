# Broad-context diagnostic evaluation

This is a small developmental test of request-to-policy interpretation. Every
request uses the same context, and the current compiler remains unchanged during
the run. No tool or task-memory operation is executed.

The first live run is complete: see [RESULTS.md](RESULTS.md) for the measured
outcomes, artifacts, and interpretation. It found zero task-scope matches out of
20 requests; the offline implementation tests are not inference-accuracy claims.
The subsequent revised-method comparison is documented in
[REVISION_RESULTS.md](REVISION_RESULTS.md): both the revised LLM method and a
simple keyword baseline matched 18/20 fresh cases.

## Why the context is broader

The user ceiling permits both Slack and email, four recipient identities, both
projects' updates/timelines, and up to five total actions. Global rules limit each
messaging operation to three calls, require plain format, cap duration at one
hour, and prohibit the restricted roadmap even though the user ceiling lists it.
Salary access and the available calendar tool are not authorized. Two distinct
people share the alias Casey. No project or messaging channel is a default.

The compiler must narrow these existing permissions differently for each request.
The ceiling itself does not specify the answer for Alice versus Bob, Project X
versus Project Y, Slack versus email, or a read-only versus sending task.

## Annotations and scope

`cases.json` contains 20 requests: 13 expect a ready policy, three expect
clarification, and four expect denial. The cases cover resource scope, recipient
changes, channel changes, multiple reads, multiple operations/recipients, action
counts, ambiguous identity, missing project/channel, forbidden reads/tools, and a
self-contained arithmetic question requiring no external permissions.

Annotations were authored by the coding assistant **before live evaluation**, not
by the compiler and not by independent human reviewers. They define a small
diagnostic specification, not verified ground truth for general user intent.
All cases are developmental; there is no held-out split or claim of generalization.

Required reads must appear. Optional directory reads are accepted for identity
resolution in messaging tasks; neither their presence nor absence is penalized.
Other project context is not presumed necessary merely because it is available.
Source reads explicitly named in a request are annotated `explicit`, while an
optional directory read is `necessary_inference`. All annotated operations are
explicitly requested. These label conventions should be reviewed before larger
research use.

Expected policies are used only by the scorer. They never enter the compiler
prompt, and they do not repair a generated policy. A preflight checks that each
annotation can be represented by the existing schema and validator.

## Run or replay

From the repository root, this command makes **up to 20 real OpenRouter calls**:

```bash
.venv/bin/python -m policy_compiler.evaluate
```

It uses `OPENROUTER_MODEL` through the existing helper. Each call has a 2,048-token
output limit, 32,000 conservative input-token bound, 60-second timeout, and no SDK
retries. Maximum conservative token bound for 20 calls is 680,960. Temperature is
zero; a seed is not set, and provider nondeterminism remains possible. There is no
automatic rerun on invalid output. `.env` is not modified.

Each invocation creates a new directory under `results/policy_compiler/runs/`
with frozen context/annotations, source snapshots/hashes, settings/package versions,
incremental records, API responses/usage, metrics, and `REPORT.md`. API logs contain
only these synthetic prompts and model outputs, not credentials, headers, or
`.env` contents. Exception messages are sanitized.

To rebuild an existing report without API calls, substitute the saved run path:

```bash
.venv/bin/python -m policy_compiler.evaluate \
  --report-only results/policy_compiler/runs/YOUR_RUN_DIRECTORY
```

Offline replay checks the saved context/annotation hashes and revalidates accepted
policies. It uses the current scorer; source snapshots preserve the original
implementation if a future scoring change needs to be examined.

## Interpret results

The report distinguishes:

- Returned policies that pass the existing mechanical validator.
- Correct status (`ready`, clarification, or denial).
- Correct task scope: required reads retained; no extra reads; exact annotated
  operations, resource/recipient sets, and action counts.
- Evidence-label errors and missing clarification topics, reported separately.
- Validation/API failures, which remain in all-case denominators.
- Tokens and wall time reported for each API attempt and in aggregate.

Question-topic coverage is a keyword screen over question/field text. It does not
judge whether a clarification is actually useful or sufficient. Scope matches
are comparisons of declared policy fields; no action effects or task completion
are measured. In particular, an aggregate two-send permission does not prove a
future agent sends exactly once to each intended recipient.

Passing the validator is not an inference-accuracy result. An extra Project Y read
can satisfy broad access ceilings while violating a Project X-only task annotation.
Rejecting a malformed candidate is not a correct intentional denial. Missing API
usage is not silently estimated as an observed token count.

## Offline checks

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_policy*.py' -v
```

The evaluator tests use HTTP mock transport, not live API calls. Their synthetic
oracle responses verify scorer/runner behavior and cannot establish LLM accuracy.
