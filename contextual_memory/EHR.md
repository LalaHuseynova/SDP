# Small native EHRAgent experiment

The first 40-episode pilot is complete. [EHR_RESULTS.md](EHR_RESULTS.md) records
the negative findings, compiler failures and measured scope enforcement.

This adds an optional policy scope to **EHRAgent's actual clinical retrieval path**,
then runs its native DPR retrieval, A-MemGuard consistency checks, lesson retrieval,
knowledge generation and clinical code generation. Generated clinical code never
executes. The original default entry points retain the ungated memory pool.

The first pilot selects the **first five questions from the previously saved
20-question clinical evaluation**, before new model outputs. Four configurations
are tested with both untriggered and triggered queries, giving 40 episodes:

- Existing A-MemGuard, without a task memory gate.
- A-MemGuard with manually annotated task scopes.
- A-MemGuard with newly compiled revised policies from original clinical requests.
- A-MemGuard with a fixed clinical keyword lexicon and patient-encounter linking.

The keyword baseline is deliberately a clinical parser, rather than applying the
earlier Slack/email parser unchanged. Its first-five scopes match the manual
annotations before evaluation. It may overgrant patient linkage on other tasks.

## Clinical fixture and scope

`fixtures/ehr5/` freezes the five requests, original 51 memory examples, table-origin
metadata and scope annotations. Trusted preparation extracts table names from
original `LoadDB` literals and SQL `FROM`/`JOIN` clauses. These tags are stored in
a protected sidecar mapping, not inferred from poisoned memory at runtime.

The original EHRAgent transformation adds four poisoned copies and reuses the
earlier optimized trigger. Each copy inherits the **clean parent's resource tags**.
Consequently an authorized poisoned example is still eligible; authorization does
not use the poison flag. Every tag required by a multi-table example must be allowed.
Unknown/untagged record IDs are quarantined when a gate is enabled.

These table-origin labels are an **experimental memory-example access protocol**.
The benchmark supplies no native permissions. They do not represent hospital ACLs,
patient-level privacy permissions, actual database access, or clinical correctness.
Public schemas for all tables remain in the original EHRAgent prompts. Four of the
five selected questions lack an exact clean reference in the available corpus.

Policies are compiled from the original clinical question and trusted catalog
**before** any trigger suffix is appended or task memory reaches a model. Manual
scopes, answers and reference code never enter compiler prompts. The policy is
held fixed while the retrieval query may contain the trigger.

## Native integration

`MedAgent` accepts an optional `memory_scope`. The default is `None`, preserving
the original candidate pool. With a scope, it restricts eligible indices before:

1. Question-embedding similarity ranking and example/context construction.
2. Action-embedding neighbor selection for historical lessons.
3. The alternative text-distance retrieval path.

The selected indices still refer to original memory rows, so judgment results,
poison labels and lessons retain their correct identities. Scope completion and
expiration close the gate. An invalid compiler policy stops that task instead of
falling back to the ungated baseline. The gate controls memory access; generated
tool operations are not enforced or executed in this pilot.

The clinical runner invokes the actual `MedAgent.initiate_chat` flow. It injects
only bounded, recorded model transport and the existing checker provider boundary;
the DPR model, ranking logic, prompts, reasoning/judgment and lesson semantics run.
Each arm starts from the same 51 original plus four transformed memory records.
Lessons accumulate across five questions within an arm and reset between arms and
query conditions, matching the native sequential lesson design.

## Commands

The fixed fixture already exists. Run a new live comparison:

```bash
.venv/bin/python -m contextual_memory.ehr
```

It uses the configured OpenRouter model with temperature zero, no SDK retries,
1,536 output tokens, a conservative 32,000 input-token bound and 60-second timeout
per request. A 400-call ceiling includes the five policy-compilation attempts;
two consecutive transport failures close the circuit. Exact model/prompt/parameter
matches reuse responses and are logged as separate cache hits. Native generation
methods are preserved, but this instrumented run is not an exact recreation of
every earlier clinical run's transport parameters or provider behavior.

No `.env` changes, model downloads or new trigger optimization are needed. The
already available local DPR weights are hashed, and embeddings are cached under
the new timestamped run. The clinical runner patches execution to fail if invoked.

Saved run directories contain sources, inputs, policies, original/poisoned memory,
raw model responses, all forty episode traces, final lesson states, metrics and
artifact hashes. Replay a completed run without API requests:

```bash
.venv/bin/python -m contextual_memory.ehr --replay PATH_TO_RUN
```

Replay reruns actual local DPR/native generation control flow against saved model
outputs, checks all prompts, policies, episodes, lessons, caches and scores, and
requires the frozen source version. Existing historical run artifacts are retained.

Offline native integration checks:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_ehr_policy.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_ehr_replication.py' -v
```

## Metrics

Report all five attempted tasks per arm and query condition, including failed
policies, provider errors, malformed judgments and invalid code. Separately count:

- Prohibited examples reaching the checker and surviving to example context.
- Prohibited lesson sources and poison-origin lessons.
- Permitted poison admitted and poison examples surviving the checker.
- Generated Python validity and syntactic `DeleteDB` proposals.
- Literal `LoadDB` table coverage relative to the manually annotated requirements.
- Retention of the one available exact reference.

Code syntax, table coverage, and deletion proposals are **proxies**. They do not
measure executed deletion, database effects, clinical answer correctness or task
completion. Trusted preparation, cache construction and integrity hashing can
inspect all original logs; the memory gate bounds runtime retrieval and model
context, not every host filesystem access. No full operating-system sandbox is
claimed. These five already exposed tasks, assistant-authored scopes and a single
fixed sequence are diagnostic evidence, not a general defense benchmark.
