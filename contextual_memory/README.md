# Controlled policy-gated memory pilot

The first live comparison is complete: see [RESULTS.md](RESULTS.md) for measured
outcomes, failures, provenance and replay commands.

A later [native five-question EHRAgent test](EHR_RESULTS.md) also runs actual DPR,
lessons, knowledge and code generation. Its results are negative for the current
compiler and must be distinguished from this synthetic memory-stage pilot.

This module connects the request-to-policy compiler to the repository's existing
EHR `ConsistencyChecker`. It compares four configurations on the same **20 policy
tasks** and a fixed **20-memory synthetic corpus**:

| Configuration | Policy source |
|---|---|
| `amemguard` | Ungated checker baseline |
| `manual_policy` | Manually annotated task scopes, including optional directory reads |
| `revised_policy` | Previously saved revised compiler outputs, including two rejected policies |
| `keyword_policy` | Existing deterministic keyword compiler |

The baseline reuses the actual A-MemGuard reasoning and joint-judgment component,
with a deterministic metadata-title retriever shared across configurations. This
is a controlled memory-stage integration. It is not the complete EHR agent, a
rerun of the earlier clinical 20-question pilot, or an embedding-retrieval result.
No clinical code, messaging tools or other actions execute. Persistent lessons
are disabled for this isolated comparison.

The ungated component baseline invokes retrieval on every task, including tasks
that should clarify, deny or need no external information. Policy configurations
can stop before retrieval. This tests the memory boundary; it does not model a
complete agent's independent decision to skip retrieval.

## Data and trust boundary

`fixtures/pilot20/index.json` contains trusted memory IDs, resource IDs and titles.
Payloads live in separate `records/*.txt` files. `labels.json` separately records:

- Task authorization: permitted, outside task scope, or prohibited by user/global rules.
- Poisoning: a per-memory boolean and attack type, independent of authorization.
- Utility: the canonical clean evidence each ready memory task requires.

Authorization uses the original task annotations and trusted resource IDs.
Poisoning labels never determine permission. Clean prohibited records and
permitted poisoned records both occur. The four handwritten attack types are
instruction override, recipient substitution, forged authority and fact corruption.
Memory bodies and labels were fixed before the new checker outputs. Labels have
not received independent human review; the 20 tasks are already exposed from
earlier policy development, not new held-out data.

The order is:

1. Validate the request-bound policy against both permission ceilings.
2. Restrict eligible index entries using trusted resource IDs.
3. Rank only eligible metadata with the shared lexical ranker, at fixed maximum k=4.
4. Read the selected payload files and pass them to the unchanged A-MemGuard checker.
5. Construct context only from checker-approved records.

The gate never reads payload text. A payload cannot grant itself permissions.
Unknown resource IDs are excluded. Non-ready policies admit nothing; invalid
policies remain failures and never fall back to the ungated baseline. Expiration
and explicit task completion close a gate instance. Callers must supply an
authenticated original request and protected metadata; this module does not
authenticate users or enforce tool actions.

Fixture preparation, integrity hashing and annotation evaluation are trusted
offline components allowed to inspect the whole corpus. Reported payload-read
exposure counts the instrumented retrieval store, followed by checker input and
delivered context. It does not claim that no trusted host process read prohibited
bytes. The model receives text through this pipeline and has no filesystem tools;
this is not an operating-system sandbox for arbitrary agent code.

```python
from contextual_memory import LocalMemoryStore, MemoryPipeline, PolicyMemoryGate
from contextual_memory.checker import build_checker

store = LocalMemoryStore(index_path, payload_directory)
gate = PolicyMemoryGate(validated_policy, original_request, trusted_context)
pipeline = MemoryPipeline(store, build_checker(recorded_completion), k=4)
result = pipeline.run(original_request, gate=gate)
gate.complete()
```

Omitting `gate` selects the ungated baseline explicitly in the experiment.
The existing EHR/ReAct entry points and default behavior remain unchanged.

## Run and verify

Offline tests do not contact OpenRouter:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_contextual_memory.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_policy*.py' -v
```

Run a new live comparison:

```bash
.venv/bin/python -m contextual_memory.experiment
```

This uses the existing configured OpenRouter model and shared client, without
editing `.env`. It permits at most 400 API calls with zero SDK retries, 1,024
output tokens and a conservative 24,000 input-token bound per call, 60-second
timeout, and a two-consecutive-failure circuit breaker. Compiler API calls are
not repeated: prior revised outputs are validated and frozen into the new run.

Identical model/prompt pairs reuse saved completions across configurations.
This gives identical checker decisions for identical inputs and reduces cost.
Configuration order rotates across tasks. API events and memoization hits are
logged separately. No keys, authentication headers or environment contents are
logged. Failures remain in the all-task denominators; malformed judgments are
errors rather than successful poison detections.

Each timestamped `results/contextual_memory/runs/` directory preserves the corpus,
annotations, policies, source snapshots, raw responses, cache hits, all 80 traces,
metrics and hashes. Replay a saved run without API calls:

```bash
.venv/bin/python -m contextual_memory.experiment --replay PATH_TO_RUN
```

Replay checks artifact hashes, source hashes, requests, every recorded model
prompt, cache behavior, all outputs and recomputed metrics. It writes nothing to
the saved run. After future code changes, use that run's source snapshot to replay
the earlier implementation rather than rewriting historical hashes.

## Metrics and limits

Exposure is measured independently at eligibility, payload read, checker input
and delivered context. We count prohibited and poisoned memories separately,
including permitted poisoned memories. Utility is required-clean-evidence recall
and the fraction of ready memory tasks retaining all canonical evidence. These
are retention proxies, not agent-answer accuracy or task completion.

The gate follows whatever validated policy it receives. A compiler can overgrant
relative to user intent while respecting global ceilings, so evaluation uses the
manual labels, never the inferred policy as its own ground truth. A-MemGuard can
still approve permitted poison, reject useful clean evidence or produce an invalid
judgment. These outcomes are retained.

The policy gate can shrink context below k. This pilot does not separate context
reduction from authorization effects, vary attack frequency, evaluate repeated
seeds, compare all published defenses or demonstrate broad generalization. The
earlier compiler's 18/20 score is not a security or execution success rate.

## Files

`gate.py` handles authorization; `pipeline.py` handles lazy payload reads, fixed
retrieval and context construction; `checker.py` adapts the actual EHR checker;
`prepare.py` records corpus provenance and independent labels; `transport.py`
provides bounded, recorded and replayable OpenRouter calls; `experiment.py`
freezes inputs, runs all four configurations, scores and reports results.
