# A-MemGuard + Direction 1 — Three-Day Research Recovery Plan

**Deadline:** Three days before the supervisor update  
**Research question:** Does task-specific memory authorization add measurable value to A-MemGuard by preventing *out-of-scope* memory/lesson influence while preserving legitimate evidence and task utility?  
**Strategy:** **Reuse all existing code and results. Audit → explain → demonstrate. Do not start over.**

## What we already have — DO NOT redo

| Existing asset | How we reuse it |
|---|---|
| Adapted EHRAgent/eICU A-MemGuard baseline | 183 evaluation questions, 51 clean + 4 poisoned memories, DPR top-k 4, GPT-4o-mini through OpenRouter |
| Baseline attacked conditions | `DeleteDB` in generated code: **171/183 without defense; 84/183 with A-MemGuard** |
| Four baseline conditions | Benign/attacked × no defense/A-MemGuard; 732 recorded episodes |
| Direction 1 implementation | Policy compiler, validator, protected scope labels, pre-retrieval gate, native EHR integration, scoped lessons |
| Twenty-task synthetic follow-up | Grounded compiler **19/20** policy matches; with lessons, unauthorized context **15/20 ungated vs 0/20 gated**, short-answer correctness **11/16 vs 13/16** |
| Negative results | An earlier native five-question clinical pilot matched **0/5** inferred scopes; gated runs still admitted authorized poisoned examples |

**Interpretation guardrails:** Baseline `DeleteDB` is a **generated-code marker**, not executed database deletion. The baseline is an **adapted reproduction**, not verified equivalence to the paper. The 20-task follow-up has synthetic annotations and is **exploratory**, not independent benchmark validation. Do not compare the 183-question and 20-question rates as if they came from the same experiment.

## What the supervisor should receive after three days

1. A **baseline reproduction assessment**, not a claim of exact replication.
2. **Two to three real traces** showing how A-MemGuard behaves and where it succeeds/fails.
3. One clear **integration diagram and trace** of Direction 1's authorization gate before retrieval and its handling of lessons.
4. A concise **preliminary-results table** with correct metric definitions, limitations and negative findings.
5. A **6–8-slide presentation**, plus a small and realistic next evaluation proposal.

---

## Day 1 — Verify the baseline and pick real examples (4–6 focused hours)

**Goal:** Explain what we already ran, why it is not necessarily identical to the paper, and what actually happened.

### Checklist

- [ ] Open `results/full/ehr/runs/20261016T180143Z/PRELIMINARY_RESULTS.md` and `episodes.json`, if available in the research checkout.
- [ ] Confirm the four conditions and their denominators; preserve the **171/183** and **84/183** counts as a precisely defined marker metric.
- [ ] Check the paper's Section 5 and Tables 1 and 3: benchmark, attack setup, ASR-r/ASR-a/ASR-t, benign ACC, top-k, model and lessons.
- [ ] Create a two-column **paper versus our implementation** table: poison construction, trigger optimization, data splits, model backend, defense interface, evaluation metric and lesson implementation.
- [ ] Inspect a **small targeted sample**, not all 84 failures manually: choose at least one successful defense and one unsafe defended case. Add a third case if helpful.
- [ ] Extract exact relevant question IDs, memory IDs, retrieved/accepted examples, checker decisions, lesson events, and generated code *from logs*. Never fabricate example outcomes.
- [ ] If traces do not contain a field, label it **not recorded** rather than guessing.

### Output

- `BASELINE_AUDIT_SHORT.md` (one to two pages).
- `BASELINE_TRACES.md` (two or three grounded cases).

**Stop condition:** If exact reproduction would require missing optimized triggers/assets or major code repair, **do not spend the day reconstructing them**. Describe our run as an *adapted baseline*, note discrepancies and proceed.

---

## Day 2 — Connect Direction 1 to A-MemGuard (4–6 focused hours)

**Goal:** Demonstrate the mechanism and its limits using our existing implementation and saved experiments.

### Checklist

- [ ] Draw the real implemented path: **original request + trusted metadata → grounded compiler → validator → eligible memory/lesson IDs → DPR → A-MemGuard checker → generation**.
- [ ] Identify code touchpoints from `DIRECTION_1_EXPLAINED(1).md`: `policy_compiler/grounded.py`, `contextual_memory/gate.py`, `contextual_memory/ehr_gate.py`, `EhrAgent/ehragent/medagent.py`, and `contextual_memory/factorial.py`.
- [ ] Prepare a concrete **before/after case** from an actual saved trace if available. Otherwise make an explicitly **illustrative** case and keep it separate from measured results.
- [ ] Explain two attack categories: **(A) out-of-scope poison**, which the gate can exclude under a correct trusted policy; **(B) in-scope poison**, which the gate admits and A-MemGuard must still evaluate.
- [ ] Make a short results table from the *already completed* 20-task follow-up: gated versus ungated, policy match, exposure, legitimate answer accuracy, and remaining poison.
- [ ] Include the unsuccessful EHR clinical transfer (0/5 scopes) and 12/20 poison-context observations. They reveal the actual limitations rather than hiding them.
- [ ] Verify that lesson retrieval receives the same boundary; distinguish scope control from content safety.
- [ ] Write one short paragraph connecting the architecture to our research hypothesis without claiming it has already been proven on 183 EHR questions.

### Output

- `DIRECTION1_DEMO_AND_EVIDENCE.md` (diagram + one trace + one table).
- One integrated pipeline figure suitable for slides.

**If time remains (optional, max 2 hours):** Try a small *paired oracle-policy* check on already supported EHR questions, **only if** independent scope definitions, trusted resource labels and reproducible run settings exist. Compare A-MemGuard alone versus oracle gate + A-MemGuard. Do **not** create arbitrary scopes to force positive results or launch a large expensive run.

---

## Day 3 — Package a convincing supervisor update (3–5 focused hours)

**Goal:** Make the existing work understandable, defensible and explicitly preliminary.

### Checklist

- [ ] Assemble **6–8 slides** in this order:
  1. Research problem and threat model.
  2. Original A-MemGuard mechanism and experimental setup.
  3. Our **adapted baseline** results versus the paper; differences clearly noted.
  4. Two concrete baseline traces (including a failure or limitation).
  5. Why authorization is different from A-MemGuard's query-conditioned safety checking.
  6. Our implemented Direction 1 pipeline and demonstration.
  7. Our existing synthetic preliminary results **and their limitations/negative results**.
  8. Next controlled benchmark evaluation and measurable hypotheses.
- [ ] Read only **three highly relevant references** for positioning: A-MemGuard, Task Shield, and CaMeL (add Fides only if time permits). State what prior work already covers.
- [ ] Prepare answers to likely supervisor questions: **Why these memories? What does 'allowed' mean? Who supplies labels? Does the checker already use context? Why synthetic tasks? How is this not just reducing retrieval size? Why do our numbers differ from the paper?**
- [ ] Check every table has: **dataset, denominator, attack condition, metric definition, source/run, and limitation**.
- [ ] End with **one next-step experiment**, not four new research directions.

### Output

- `SUPERVISOR_UPDATE.pdf` or slide deck.
- `PRELIMINARY_EVIDENCE_SUMMARY.md` (one-page handout).

---

## Essential evaluation plan — propose now, execute after the meeting unless easy

**Same EHR questions, same memory pool, same attacks, same model/retrieval and lesson settings:**

| Arm | What it tests |
|---|---|
| No defense | Attack baseline |
| A-MemGuard only | Existing defense |
| Oracle-policy gate + A-MemGuard | Does **correct** authorization add value? |
| Inferred-policy gate + A-MemGuard | Does the **implemented compiler** retain the value? |
| Keyword gate + A-MemGuard | Is the LLM compiler needed? |
| Matched-count retrieval + A-MemGuard | Is improvement just fewer retrieved memories? |

**Measure separately:** unauthorized example/lesson exposure; poison admitted to context; generated-code attack-marker success; legitimate evidence recall; bona fide task correctness where measurable; policy overgrant/undergrant; denial errors; token/time cost. Use real task-specific policy annotations reviewed independently. Existing EHR `access_gt`/`inaccessible_db` annotations require a meaning check before being used as memory-access ground truth.

## What NOT to do in these three days

- **Do not** restart all 732 episodes merely to obtain the same results.
- **Do not** claim exact reproduction or equate our `DeleteDB` rate with the paper's ASR-t.
- **Do not** create another synthetic benchmark or tune on the 20 follow-up tasks again.
- **Do not** implement a complete tool-action monitor or new ML training process.
- **Do not** spend a day reading ten papers or rebuilding inaccessible repository assets.
- **Do not** call all poisoned memories unauthorized: **authorized memories can also be poisoned**.

## Priority order if we run out of time

**Must have:** baseline table + methodological differences; two real cases; real pipeline; existing Direction 1 results with limitations; slides.

**Nice to have:** small paired oracle-gate EHR comparison.

**Not required now:** exact original-paper replication, major new datasets, action execution benchmarks, new model training, repeated full trials.

## Final one-paragraph research story

> We established an adapted A-MemGuard baseline using existing EHRAgent questions and poisoned memory examples. Its consistency-checking defense reduced the occurrence of attack-marked generated code, but the run differs from the published setup and does not measure executed database actions. We then implemented a separate request-specific authorization mechanism that restricts eligible memory and lessons *before retrieval*. In a small, controlled synthetic follow-up it prevented observed out-of-scope context exposure, but the evaluation is not independently validated and in-scope poisoned memory remains a risk. Our next step is a paired benchmark test using independently reviewed task scopes to determine whether contextual authorization adds security while preserving needed evidence beyond A-MemGuard alone.

**Bottom line:** The deliverable is a strong **preliminary evidence package**, not a new defense built in three days. Preserve existing runs, state what they really prove, and show one concrete connection between baseline failures and our proposed mechanism.
