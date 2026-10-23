# Request-to-policy compiler: Direction 1

The CLI now defaults to a revised task-needs compiler with deterministic ceiling
intersection and policy construction. See [REVISION.md](REVISION.md) for the
current method, comparison design, APIs, and limitations. `compiler.py` and
`compile_policy` preserve the original direct-policy baseline described below.

This first milestone is a standalone, **prompt-based research baseline**:

```text
authenticated original request + trusted structured context
    -> existing OpenRouter helper -> candidate JSON
    -> strict schema and permission-ceiling validation
    -> ready policy / clarification / denial
```

It does not execute tools, read task memory, change A-MemGuard retrieval, train a
model, or persist lessons. The caller must authenticate the request and obtain
the permission ceilings and metadata from trusted infrastructure. This module
does not establish that trust itself.

Python 3.10+ is required. The implementation reuses the environment's installed
OpenAI SDK, python-dotenv, and Pydantic; no packages were installed for this change.

## Run

From the repository root, use the existing Python environment. If needed, install
only the small compiler dependency set:

```bash
.venv/bin/python -m pip install -r requirements-policy.txt
```

The existing `openrouter_client.py` reads `OPENROUTER_API_KEY`,
`OPENROUTER_MODEL`, and optional `OPENROUTER_MAX_TOKENS` from the root `.env`.
Existing process environment variables take precedence. The compiler does not
modify `.env`, print credentials, or create API request logs. If no model is
configured, the shared helper's existing `openrouter/free` fallback applies.
Use an explicitly configured model for reproducible experiments.

### Compile a request through OpenRouter

This command makes a real LLM request using the configured model:

```bash
.venv/bin/python -m policy_compiler \
  --request 'Send Alice the Project X update on Slack' \
  --context policy_compiler/examples/context.json
```

An optional `--model provider/model` overrides the configured model. The model
must support JSON-object response mode. The existing helper supplies the token
budget, 60-second SDK timeout, and up to two SDK retries for eligible transport
errors; this module does not retry, repair, or replace invalid model output.
Duplicate recipient aliases detected in a proposed ready policy are the one
safe downgrade: compilation returns a clarification with all grants removed.

### Validate the reviewed example offline

No API key or network is used by this command. It checks a saved candidate;
**it does not infer a policy for a new request**:

```bash
.venv/bin/python -m policy_compiler \
  --request 'Send Alice the Project X update on Slack' \
  --context policy_compiler/examples/context.json \
  --validate-response policy_compiler/examples/policy.json
```

The complete example output is [examples/policy.json](examples/policy.json).
It permits reading `project_x_update` and `coworker_directory`, and one
`slack.send_message` using `project_x_update` for the uniquely identified `alice`.
It constrains `format` to `plain` and expires at task completion or after at most
3,600 seconds. The directory read is marked `necessary_inference`; the requested
send is marked `explicit`. Neither email nor salary-data access is granted.

Exit codes: `0` ready; `2` clarification required; `3` denied; `1` invalid input,
invalid policy, or API failure. Successful results, including clarification and
denial, are JSON on stdout. Errors are short sanitized messages on stderr; raw
provider exceptions and invalid candidate bodies are never printed. Valid JSON
output intentionally includes the supplied request and policy; do not put API
keys or other credentials in task inputs or trusted metadata.

### Python API

```python
import json
from pathlib import Path
from policy_compiler import compile_task_policy

context = json.loads(Path("policy_compiler/examples/context.json").read_text())
policy = compile_task_policy("Send Alice the Project X update on Slack", **context)
print(policy.model_dump_json(indent=2))
```

`validate_policy(candidate, original_request, context)` checks a JSON string,
dictionary, or `TaskPolicy`. `validate_context(context)` checks trusted inputs.
Both raise `PolicyValidationError` for rejected data. `compile_policy` also
raises `CompilationError` for API failures or incomplete responses. All these
exceptions have sanitized messages. Policy model instances are ordinary data,
not tamper-proof capabilities; revalidate after any modification.

## Trusted input contract

[examples/context.json](examples/context.json) supplies all four required inputs:

| Field | Contract |
|---|---|
| `existing_user_permissions` | Structured permission ceiling for the authenticated user |
| `global_rules` | A second structured ceiling, intersected with the user's ceiling |
| `available_tool_schemas` | Known tool/operation pairs, scope requirements, and scalar argument types/enums |
| `trusted_resource_metadata` | Known resource IDs and recipient IDs, names, and aliases; no record bodies |

Both ceilings use the same schema: `read_resource_ids`, `operations`,
`max_total_actions`, and `max_duration_seconds`. Each operation contains `tool`,
`operation`, `resource_ids`, `recipient_ids`, `argument_constraints`, and
`max_calls`. An empty read list denies all reads; an empty operation list denies
all actions. Omitted ceiling fields are invalid, never interpreted as unlimited.
Every referenced ID must exist in trusted metadata, and ceiling operations must
have a known tool schema. No wildcard identifiers are accepted.

These are explicit allowlists. Arbitrary natural-language global rules are
**not** executable restrictions in this version; encode supported restrictions
in this schema or reject the context. Tool availability does not grant access.
Trusted metadata contains names/identities, not executable instructions.

Tool schemas are a deliberately small policy-oriented format, not arbitrary
vendor function schemas. An argument has a `value_type` (`string`, `integer`,
`number`, or `boolean`) and optional `enum`. Constraints are finite permitted
scalar value lists, for example `{"format": ["plain"]}`. Nested arguments,
regex restrictions, general predicates, optional resource scopes, and relations
between individual resource/recipient pairs are unsupported. Future adapters
must explicitly bind these policy fields to actual tool parameters.

## Output policy schema

Pydantic models in `schema.py` are the source of truth. To inspect the generated
JSON schema without any API call:

```bash
.venv/bin/python -c 'import json; from policy_compiler import TaskPolicy; print(json.dumps(TaskPolicy.model_json_schema(), indent=2))'
```

| Field | Meaning |
|---|---|
| `schema_version` | Literal `"1"` |
| `original_request` | Exact original request, preserved without rewriting |
| `status` | `ready`, `clarification_required`, or `denied` |
| `reads` | Resource IDs with evidence for each requested read |
| `actions` | Tool/operation grants, resource scopes, recipients, argument constraints, finite call counts, and evidence |
| `expiration` | `condition: task_completion` plus finite `max_duration_seconds` |
| `ambiguities` | Unresolved fields, targeted questions, and optional known candidate IDs |
| `denial_reason` | Reason for a denied task; otherwise `null` |

Evidence contains `basis`, an exact `request_excerpt`, and `rationale`.
`basis` distinguishes `explicit`, `necessary_inference`, and `uncertain`.
An uncertain grant is rejected, not activated. Every action recipient has an ID
and a `request_reference` that appears as a complete reference in the original
request and uniquely matches a trusted name, alias, or ID. This initial design
does not resolve pronouns or authorize dynamic directory lookups; unresolved
identities require clarification. A permitted directory read is represented
as a future task permission, not executed during compilation.

## Validation rules

- All models forbid unknown fields and implicit type coercion. Identifiers,
  list lengths, strings, counts, and durations are bounded; no non-finite values.
  JSON text is capped at 256 KiB. Duplicate JSON keys and trailing text fail.
- The request in the output must exactly equal the caller's original request.
  Evidence excerpts must actually occur in that request. These are mechanical
  checks, not proof that a permission is semantically justified.
- Every read and action must fit **both** trusted ceilings. Known metadata alone
  does not authorize reading a resource or contacting a recipient.
- Resource and recipient lists cannot widen their respective ceilings. Unknown
  tools/operations, identifiers, arguments, and enum values are rejected.
- Every ceiling argument restriction must remain present, with a subset of its
  permitted values. Boolean and numeric values are not interchangeable.
- Duplicate read IDs, operation grants, scope IDs, and constraint values fail.
  One grant per tool/operation prevents duplicate entries multiplying budgets.
- Per-operation and aggregate action counts must fit both ceilings. Expiration
  is bounded by both ceilings and always includes task completion. These fields
  describe limits; no runtime counter, timer, or action monitor is implemented.
- `ready` requires no questions and no denial reason. Zero grants are valid for
  tasks requiring no external access, such as self-contained arithmetic.
  `clarification_required` requires questions and zero grants. `denied` requires
  a reason and zero grants. This first version pauses the whole task when
  clarification is needed; it does not continue independent authorized steps.
- A guessed recipient matching multiple trusted identities is rejected by the
  validator. The compiler converts that specific failure to a question and
  discards **all** grants. Other invalid candidates fail without silent repair.
- Non-`stop` API responses, even if their text happens to parse as JSON, fail.
  There is no fallback policy on an API or validation error.

## Tests and interpretation

Run the focused suite without API calls:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_policy_compiler.py' -v
```

Run existing OpenRouter regression checks:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_openrouter_backend.py' -v
```

The focused suite covers the Slack request; ambiguous recipients and unsafe
identity guesses; available but unauthorized email; multiple necessary reads;
salary access exceeding user permissions; separate global/user ceilings;
argument constraints; per-operation/aggregate counts; recipient/resource scopes;
expiration; malformed JSON; uncertain/fabricated evidence; truncated output;
sanitized errors; request binding; and the offline CLI. A real OpenAI SDK client
with HTTP mock transport verifies the OpenRouter request, configured model, and
JSON response mode without contacting the service.

**Offline examples are reviewed candidate fixtures, not measured LLM inference
accuracy.** Passing these tests establishes local behavior and transport wiring;
it does not establish authorization-inference generalization. The compiler is
not trained or calibrated. An overbroad grant *within* both ceilings may still
pass if the LLM misinterprets intent, labels an inference incorrectly, or copies
an unrelated excerpt. A model could also omit required work or deny a valid
request. These errors need independently annotated held-out requests, contrasting
task pairs, multiple acceptable policies, and clarification/utility metrics.

`ready` means schema/ceiling-valid, not necessarily task-correct or safe to execute.
Resource IDs on actions are declared scope, not enforced information-flow labels.
Permission checks do not establish factual truth or prevent all semantic
disclosure. No policy in this milestone is connected to a real tool or memory
store. Review and validation remain necessary before any future integration.

### Verification on 2026-10-17 (Asia/Baku)

- Focused compiler suite: **23 tests passed**. Offline example and CLI help also
  passed, along with Python compilation checks.
- Existing OpenRouter suite: **8 passed, 3 failed** (11 total). The failures are
  in unchanged EHR integration code/tests: two checks cannot import `llm_env`
  through the legacy test loader; another mocked agent lacks `last_trace` after
  retrieval is mocked out. This change does not import or modify those paths.
  They are recorded here rather than hidden or repaired outside this milestone.
- Three small live smoke attempts used the configured `openai/gpt-4o-mini` with a
  process-local 2,048-token output limit, leaving `.env` unchanged. The first
  failed recipient-reference validation; after clearer identity instructions,
  the second failed duplicate-operation validation. After explicitly prohibiting
  duplicate operation grants, the third returned a `ready` policy that passed
  schema and ceiling validation. No rejected candidate became a usable policy,
  and no tool action was executed.
- The accepted live candidate included an additional `project_x_timeline` read
  labeled `explicit`, with a rationale saying it "may require additional context."
  This is not established as necessary by the request and the label should not
  be treated as ground truth. It demonstrates an inference-quality limitation,
  despite passing mechanical checks. The reviewed offline example remains the
  reference example; a successful smoke is not a policy-accuracy result.
- The shared helper, EHR integration files, and `.env` retain their initial
  fingerprints. Only the new files listed below belong to this change.

## Files added

| File | Purpose |
|---|---|
| `policy_compiler/__init__.py` | Small public API |
| `policy_compiler/schema.py` | Strict input/output models |
| `policy_compiler/compiler.py` | Prompt, existing OpenRouter transport, validation, safe ambiguity downgrade |
| `policy_compiler/validator.py` | JSON, metadata, scope, argument, count, and status checks |
| `policy_compiler/__main__.py` | Live compilation and offline validation CLI |
| `policy_compiler/examples/context.json` | Reviewed trusted demo context |
| `policy_compiler/examples/policy.json` | Reviewed complete candidate output |
| `policy_compiler/README.md` | Contracts, usage, limits, and verification |
| `tests/test_policy_compiler.py` | Offline behavior and SDK transport checks |
| `requirements-policy.txt` | Minimal API/compiler dependencies |

## Broader-context evaluation

The next diagnostic milestone adds [evaluation/context.json](evaluation/context.json),
[evaluation/cases.json](evaluation/cases.json), `evaluate.py`, and
`tests/test_policy_evaluation.py`. All 20 cases share a broader fixed context so
that copying existing permission ceilings cannot solve the task-scoping problem.
Annotations are authored before live evaluation and withheld from model prompts.
See [evaluation/README.md](evaluation/README.md) for the scoring specification,
provenance, budget, and limitations.

Run the 20-case evaluation through the configured OpenRouter model:

```bash
.venv/bin/python -m policy_compiler.evaluate
```

Run all compiler/evaluator tests offline:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_policy*.py' -v
```

The combined suite passed **31 tests** before the broader live run.
The completed pilot and final **32-test** verification are documented in
[evaluation/RESULTS.md](evaluation/RESULTS.md). The live pilot found **0/20**
annotated task-scope matches; that baseline result is retained without tuning.

The existing `.env`, shared OpenRouter helper, EHRAgent/ReAct code, and prior
experiment results are unchanged.

The subsequent [audit](../results/policy_compiler/audits/20261017T192916986749Z/AUDIT.md)
passes 54 offline tests, fixes long-request/alias edge cases and report-cache
validation, and reproduces the saved results without new API calls. The revised
compiler and keyword baseline remain tied at 18/20 on the fresh synthetic cases;
this does not establish a research advantage or correct interpretation of every
request.
