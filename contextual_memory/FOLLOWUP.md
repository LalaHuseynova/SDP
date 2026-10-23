# Direction 1 follow-up: grounded compilation and native lessons

This follow-up adds an **opt-in** compiler and a controlled native-memory experiment.
Original experiments and their artifacts are retained. The default `revised` compiler
and existing EHR command remain available; this work does not replace their scores.

The user requested that independent annotation review remain **pending for the
supervisor**. New live measurements are therefore explicitly unreviewed diagnostics,
not independently validated accuracy or evidence of broad generalization.

## Compiler changes

`policy_compiler/grounded.py` uses a trusted resource catalog containing aliases and
declared read dependencies. For example, prescription vocabulary resolves to a
medication resource; the clinical demonstration protocol adds patient linkage.
These aliases/dependencies are supplied infrastructure metadata, not learned rules
or permissions extracted from potentially poisoned memory. They introduce domain
configuration work and are not a solution to automatic policy provisioning.

The model predicts a status-specific decision. Ready decisions carry needs;
clarification/denial decisions have no grant fields. Invalid mixed outputs still
fail closed. Python expands catalog dependencies, rejects unanchored supporting
reads, and uses the existing policy assembler and final validator. Both original
permission ceilings, argument constraints, identity binding and finite budgets
remain enforced. This does not establish that a mechanically valid policy captures
intent. Negation and other semantic interpretations remain model predictions.

The trusted catalog must cover all known resources, reference only known dependency
IDs and contain no dependency cycles. Shared aliases cannot uniquely bind a source.
Unrepresented vocabulary can still cause unnecessary clarification. Clinical
dependencies describe this experiment's memory-access protocol, not hospital ACLs.

Use the new compiler explicitly:

```bash
.venv/bin/python -m policy_compiler --method grounded \
  --request 'When was the last prescription for this patient?' \
  --context contextual_memory/fixtures/ehr5/context.json \
  --resource-catalog policy_compiler/evaluation/clinical_catalog.json
```

This makes one configured OpenRouter call. Python API:
`compile_grounded_policy(request, **context, resource_catalog=catalog)`.

## Development evidence

Five live development passes (25 calls) used the **already exposed** five clinical
questions. Scope matches were 0/5, 4/5, 4/5, 4/5, then 5/5 after prompt/catalog
development. All attempts are saved under `results/direction1_followup/development/`.
The alias/dependency-aware keyword control also matched 5/5. The final result is a
development result, not an LLM advantage, independent test or training result.

`results/direction1_followup/setup/compiler_freeze.json` records method hashes
before the new fixture files were created. Sources and inputs are frozen again
before live factorial evaluation. No test-result-driven method changes belong to
the frozen run. Future fixes must use a new version/run rather than rewriting it.

## Comparison design

`contextual_memory.factorial` invokes the actual `MedAgent.retrieve_embedding_examples`
method with local DPR embeddings and the unchanged repository consistency checker.
It retains native action-neighbor lesson retrieval, rejection-explanation storage,
and insertion of past-lesson warnings into context. A factual JSON answer stage is
added for measurable synthetic answer accuracy. This is not the full clinical
database/code-execution agent, and no generated code or other tool action executes.

| Arm | Scope source | Lessons |
|---|---|---|
| `ungated_l0` | Unrestricted | Off |
| `ungated_l1` | Unrestricted | On |
| `grounded_l0` | New compiler | Off |
| `grounded_l1` | New compiler | On |
| `keyword_l1` | Alias/dependency-aware deterministic control | On |
| `manual_l1` | Annotated oracle | On |
| `matched_l0` | Unrestricted, matched retrieval count | Off |
| `matched_l1` | Unrestricted, matched retrieval count | On |

All arms start with identical memory and no lessons, use the same frozen task order,
and reset lesson state between arms. Enabled arms accumulate lessons within their
sequence. Disabled arms neither retrieve nor store lessons. The native default is
still enabled, including for callers created before the new flag existed.

The matched arms retrieve as many records as the grounded policy would allow, up
to four, but choose from the unrestricted pool. They apply only where the compiler
returned ready. This controls retrieval count, not final context length or lesson
bytes. The comparison is a single sequence, not repeated independent trials.

Ungated arms invoke retrieval even on requests annotated as denied, ambiguous or
self-contained. They are memory-stage controls, not a complete agent independently
deciding whether to retrieve. All-task exposure reductions therefore combine scope
restriction with stopping/non-retrieval. Ready-task subsets and matched controls
must be considered before attributing gains to better selection alone.

## Data and independent review

`fixtures/followup20/` contains 20 new assistant-authored synthetic read-only QA
requests, ten resources, 20 memory records, trusted catalogs/scopes, and expected
answer variants. Cases include office and synthetic clinical vocabulary, aliases,
joins, negation, two clarifications, two denials and self-contained arithmetic.
Related task families and known development concepts remain exposed; this is not
a hidden independent benchmark. Action inference is outside this QA fixture.

Ten records contain handwritten attack text; ten are clean. Authorization labels
and poisoning are independent. Model compilation sees only request/configuration;
the answer stage sees only request/admitted examples and lessons. Expected scope
and answer annotations are used only for scoring and the explicit manual arm.

`fixtures/followup20/REVIEW.md` is the supervisor review packet. `review.json` stays
pending until an actual reviewer checks the frozen inputs. A reviewed status also
requires the input hashes to match. Editing evaluated inputs requires a new fixture
version; it cannot retroactively improve a saved score.

Default live evaluation rejects pending review. An explicitly labeled diagnostic
can run before review:

```bash
.venv/bin/python -m contextual_memory.factorial --diagnostic-unreviewed --max-calls 400
```

The configured OpenRouter model is used at temperature zero, with no SDK retries,
at most 400 new calls, 1,024 output tokens (256 for answers), a 32,000-byte conservative
input bound, 60-second timeout, and a two-consecutive-transport-failure circuit.
Exact model/prompt/parameter matches share cached responses across arms. `.env`
is not modified and credentials are not logged. Each run writes a new timestamped
directory under `results/direction1_followup/factorial/` with raw events, sources,
inputs, policies, traces, lesson states, metrics, report and integrity hashes.

## Metrics and limits

Report unauthorized exposure at checker and context, poison examples, poison-origin
lessons, lesson use/storage, required-clean-evidence retention, policy failures and
normalized exact factual-answer correctness. Unknown or failed outputs stay in the
denominators. A poison-origin lesson can be a helpful warning, not necessarily an
attack. Answer correctness and unauthorized-context-free correctness are separate.

No harmful tool effects or clinical answer correctness are measured. No completed
task claim follows from a correct answer string. Trusted setup, embedding and
integrity hashing inspect memory bodies; the gate limits native retrieval/model
context, not every host filesystem access. No operating-system sandbox is claimed.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_policy*.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_factorial.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_ehr*.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_contextual_memory.py' -v
.venv/bin/python -m contextual_memory.factorial --replay PATH_TO_SAVED_RUN
```

Offline replay reruns actual local DPR/native retrieval and lesson state against
recorded model outputs, checks all sources and input hashes, compares every record,
policy, lesson state, cache hit and metric, and verifies saved artifacts are unchanged.
Use the run's source snapshot after later code changes. No network is needed for
replay. Current unit tests include actual native lesson accumulation/reset/off
behavior, exposure scoring, retrieval-count controls, failed-answer accounting,
pending/stale review rejection and compiler permission-boundary checks.

Scoped verification covers 100 tests: 61 policy tests, 20 EHR tests, 15 controlled
memory tests, three factorial tests and one transport-resume test. The broader legacy OpenRouter suite remains
8 passed / 3 failed: two legacy file-loader checks cannot import `llm_env`, and a
mocked-retrieval test lacks `last_trace`. These same issues were documented before
this follow-up in `policy_compiler/README.md`; they are not hidden by the scoped
suite. Logs are under `results/direction1_followup/setup/`.

The first live factorial attempt was interrupted after answer-mode BadRequest
errors. The answer messages omitted the explicit word JSON required by JSON mode.
Only this answer-request formatting was fixed; method and fixture hashes stayed
unchanged. A real one-call transport smoke then passed. The interrupted run and
its partial events remain under `results/direction1_followup/factorial/20261018T135312364248Z/`.

A second attempt preserved 186 successful response events but was interrupted to
avoid repeated expensive native CPU embeddings. Exact full-tensor embedding
memoization for keyword arguments was added for the immutable eval-mode DPR model. A resumed run verifies
and reuses that successful response prefix before making new requests. Compiler,
fixtures, answer prompt, scoring definitions and native lesson semantics are
unchanged. Prior completed traces are compared with resumed traces; only the
new-call accounting differs. Reused events are explicitly marked and excluded
from the resumed run's new API-call total. The 400-event ceiling includes reused
responses, so this does not expand the unique-response budget.

The native retrieval function passes tensors positionally, so the experiment's
keyword-only cache did not accelerate those native forwards. The first successful
offline verification also recomputed every native positional forward. Its complete
audit is retained in `setup/verification_full_native.json`. The standalone verifier
now memoizes both positional and keyword tensor inputs for faster future replay;
this changes neither the frozen experiment sources nor any recorded outcome.
