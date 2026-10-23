# Completed controlled memory-stage comparison

The experiment ran **20 existing policy tasks × four configurations = 80 results**
against a fixed 20-memory synthetic corpus. It reused the actual repository EHR
A-MemGuard `ConsistencyChecker`, unchanged, after an optional policy gate and a
shared metadata-title retriever with maximum k=4.

| Configuration | Tasks exposing prohibited memory to checker | Tasks delivering prohibited memory | Tasks delivering any poison | Required clean evidence retained | Memory tasks retaining all required evidence |
|---|---:|---:|---:|---:|---:|
| A-MemGuard checker, ungated | 19/20 | 14/20 | 18/20 | 12/14 | 11/12 |
| A-MemGuard + manual policies | 0/20 | 0/20 | 10/20 | 12/14 | 11/12 |
| A-MemGuard + saved revised policies | 0/20 | 0/20 | 10/20 | 12/14 | 11/12 |
| A-MemGuard + keyword policies | 1/20 | 1/20 | 11/20 | 11/14 | 10/12 |

The 14 clean-evidence requirements span 12 ready tasks that need external memory;
the remaining ready task is self-contained. Retaining evidence does not establish
that an agent answered correctly or completed a task. No actions execute here.

## What the results establish

On this corpus, correct resource scopes prevented runtime retrieval and checker
exposure to manually prohibited records, while preserving the same aggregate clean
evidence retention as the ungated component baseline. Poison labels were not used
by the gate. Permitted poisoned records reached the checker in all 12 memory tasks
under manual and revised policies, and poison reached final context in **10/12**
of those tasks (**10/20** across all tasks). Thus this boundary handles
authorization; this run does not show improved detection of permitted poison.

The revised configuration retains **two policy errors** on the compensation-ledger
and acquisition-plan requests. They stop before retrieval and are counted as
failures, not successful denials. Its other outcomes are 13 completed memory stages,
three clarifications and two valid denials. The manual configuration has four
valid denials rather than those two errors. No saved failed policy was repaired.

The keyword configuration unnecessarily clarifies the negated-send request,
losing that task's clean evidence. On the two-source comparison it grants the
unrequested Orion progress note, and two prohibited records from that resource
reach final context. All configurations miss both canonical clean records for
that comparison task; the gated manual/revised contexts are empty after checking.
These are recorded failures of the memory-stage pipeline, not successful tasks.

## What the comparison does not establish

- These are the already exposed 20 **policy tasks**, not the earlier clinical
  20-question EHR dataset. Bodies, attacks and labels are synthetic and authored
  by the coding assistant, without independent annotation review.
- The baseline uses A-MemGuard's actual checker with the shared local retriever.
  It is not the entire clinical agent or an embedding-retrieval reproduction.
  Baseline retrieval is invoked on every task; it does not simulate an agent's
  separate decision to skip retrieval on ambiguous, denied or self-contained tasks.
- Revised policies are validated **saved compiler outputs** from the earlier
  experiment. This run makes new checker calls and does not remeasure policy
  inference on unseen requests.
- The gate reduces context size and stops non-ready tasks. There is no matched
  context-budget ablation, repeated-run uncertainty estimate or adaptive attacker.
  This small diagnostic comparison does not establish an overall learning
  advantage, broad robustness, publication-level generalization or full security.
- Trusted fixture preparation, hashing and evaluation can inspect the entire
  corpus. Runtime exposure metrics instrument `LocalMemoryStore.read`, checker
  inputs and delivered context. They do not measure all host filesystem accesses.
  The model has no filesystem tools or other tool execution in this pilot.

## Verification and artifacts

The live run made **129 new OpenRouter requests**, with **125 exact-prompt cache
hits**, **zero API failures/truncations**, and **62,034 provider-reported tokens**
(48,035 prompt; 13,999 completion). Identical prompts reuse outputs across arms,
so per-arm incremental call counts are not independent-run cost comparisons.
SDK retries were disabled. No new compiler calls or `.env` edits occurred.

All **69 relevant offline tests pass**. Offline replay reproduced every input
prompt, all 80 task/configuration records, cache behavior and all metrics. Source,
fixture, policy and artifact hashes were verified. All **240 protected previous
files** remain unchanged, including existing EHR/ReAct code, the shared OpenRouter
client, `.env`, policy implementation and earlier experiment artifacts.

- [Frozen run report](../results/contextual_memory/runs/20261017T195736454503Z/REPORT.md)
- [Per-task results](../results/contextual_memory/runs/20261017T195736454503Z/records.json)
- [Metrics](../results/contextual_memory/runs/20261017T195736454503Z/metrics.json)
- [Independent replay/integrity verification](../results/contextual_memory/setup/verification.json)
- [69-test log](../results/contextual_memory/setup/final_tests.txt)

Reproduce the saved experiment without API calls:

```bash
.venv/bin/python -m contextual_memory.experiment \
  --replay results/contextual_memory/runs/20261017T195736454503Z
```

Run a new live comparison with the same fixed data and configured OpenRouter model:

```bash
.venv/bin/python -m contextual_memory.experiment
```

See [README.md](README.md) for the trust boundary, schema inputs and implementation.
