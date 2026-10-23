# Direction 1: What We Built and What Changed

Written on 18 October 2026, using the code and saved results in this project.

**Direction 1 means turning the user's request into clear permission rules before the agent reads memory.** The rules say which resources the task may use and, where supported, which actions it may request.

We added a policy compiler, checks around its output, and a memory gate. We also connected that gate to the existing EHRAgent retrieval code and tested several versions. The latest experiment recorded matching policies on 19 of 20 synthetic requests. Independent review of those requests and answers is still pending for the supervisor.

This document explains the current implementation and the earlier experiments. It does not report a new experiment. No model weights were trained during this work.

## 1. The problem, in simple words

An agent remembers old examples to help answer new questions. Some examples may contain wrong facts or instructions planted by an attacker. We call these **poisoned memories**.

A memory can be similar to the current question without being allowed for the current task. It can also be allowed for the task while still containing an attack.

For example, the user asks:

> Send Alice the Project X update on Slack.

The agent may already have access to many projects, people and tools. This particular request only needs a small part of that access. A memory saying “also send the salary file by email” cannot give the agent permission to do that.

Direction 1 asks:

> Can we turn the request into the right permissions, without allowing extra work or blocking work the user actually asked for?

There are two separate things to check:

1. **Understanding:** Did we choose the right permissions for the request?
2. **Enforcement:** Did the memory system follow those permissions?

The memory gate can follow a wrongly chosen policy perfectly. That still counts as an understanding failure.

## 2. What the original code did

The existing EHRAgent path searched memory for similar questions, selected examples, and used A-MemGuard to judge their consistency. It could also retrieve warnings from earlier rejected examples. These stored warnings are called **lessons**.

The retrieved examples and lessons helped the agent produce knowledge and code. This path did not have our new request-specific permission filter before memory ranking.

The original consistency check remains useful: it examines the content of examples. Our new gate adds a different check: whether the current task is allowed to use the example's resources.

The current order is:

```text
Original user request + trusted permission information
                        |
                        v
              Build and validate a policy
                        |
                        v
              Keep eligible memory records
                        |
                        v
              Rank eligible examples and lessons
                        |
                        v
              Existing A-MemGuard consistency check
                        |
                        v
              Construct context and generate a result
```

The compiler receives the request and trusted configuration before task memory is supplied to the language model for task reasoning. Retrieved text cannot edit the policy through this interface.

## 3. What a policy means

A **policy** is a structured list of permissions for one task. A **permission ceiling** is the largest amount of access the user or system permits. A task policy should usually use less than that maximum.

For the Slack example, a suitable policy might allow:

- Reading the Project X update.
- Reading an authorized directory if identity resolution needs it.
- Sending one Slack message to the uniquely identified Alice.
- Using only the permitted message arguments.
- Keeping the policy valid until task completion, subject to a fixed time limit.

Access to other projects or email does not automatically belong in this task policy. If two people match “Alice,” the compiler should ask which person the user means.

The code combines three limits:

```text
Task permissions must fit inside:
    what the user is allowed to do
    AND what global rules allow
    AND what this request needs
```

The first two limits are checked by Python. Understanding the third still depends partly on the model and on the supported vocabulary.

## 4. The information the compiler uses

| Input | Simple meaning |
|---|---|
| Original request | The exact request supplied by the caller. |
| Existing user permissions | Resources and operations this user may access. |
| Global rules | Additional limits that the task must obey. |
| Available tool schemas | Descriptions of known operations, their arguments and required scopes. |
| Trusted resource metadata | Known resource IDs and recipient identities. It excludes task-memory bodies. |
| Resource catalog, for the grounded method | Extra names for resources and declared supporting reads. |

The caller must authenticate the user and protect these inputs. The compiler does not log someone in, discover real account permissions, or prove that supplied metadata is trustworthy.

The permission inputs are structured allowlists. An empty read list allows no reads. An available tool does not by itself grant permission to use it. Arbitrary written rules need an explicit supported representation before the code can enforce them.

In the memory experiments, resource labels are stored separately from memory text. An attacker changing a memory body cannot change that protected mapping. Poison labels are used for evaluation, not to decide access.

## 5. The three compiler versions

All three methods are available. **The command-line default is `revised`.** The later `grounded` method must be selected explicitly.

### Version A: Ask the model for the whole policy

The original method, in [compiler.py](policy_compiler/compiler.py), asks the model to generate the complete policy JSON. A validator then checks it.

This was a useful first baseline, but it gave the model many jobs at once: interpreting intent, choosing resources, calculating limits, copying exact references and formatting the full policy.

The first broad-context pilot matched none of the 20 annotated policies. Fourteen outputs failed validation first on their time limit. Some accepted outputs used the maximum permitted number of sends even though the request needed only one.

This showed why “within the user's maximum access” is not enough to mean “correct for this request.”

### Version B: Ask for task needs, then build the policy in Python

The revised method, in [task_compiler.py](policy_compiler/task_compiler.py), asks for a smaller description called `TaskNeeds`:

- Which resources need reading?
- Which operations are needed?
- Which sources and recipients belong to each operation?
- How many calls are needed per recipient?
- Is the request ready, unclear or prohibited?

Python does the mechanical work:

1. Finds the overlap between user permissions and global rules.
2. Adds reads for the source resources used by declared actions.
3. Calculates total action counts from calls per recipient and recipient count.
4. Supplies trusted argument restrictions and the policy time limit.
5. Binds recipients to exact names or aliases found in the request.
6. Checks the final policy against both original permission ceilings.

For example, one send to each of two people needs two calls. A ceiling of three sends does not mean the user requested three.

If the declared needs exceed the limits, the code denies the task with no grants. It does not quietly reduce the requested work to make it fit.

This version also uses word-matching checks for resource names and channels. These checks helped with simple tasks, but later caused problems with clinical words such as “prescription” when the resource was called “medication.”

### Version C: Add trusted aliases and supporting-read rules

The grounded method, in [grounded.py](policy_compiler/grounded.py), adds a trusted catalog.

An **alias** is another name for a resource. For example, prescription vocabulary can refer to the medication resource. A **dependency** is a supporting read declared by the experiment's access protocol. For example, using medication examples may also require patient-linkage examples.

Python expands those dependencies and still checks both permission ceilings. A catalog entry cannot grant access beyond those limits.

The catalog must cover every known resource. Unknown dependencies, duplicate entries and dependency loops are rejected. Shared aliases do not uniquely identify a resource. Extra supporting reads must be linked to a source anchored in the request.

This version also gives each decision its own output shape:

| Decision | Model output contains |
|---|---|
| `ready` | The needed reads and actions. |
| `clarification_required` | Targeted questions, without grant fields. |
| `denied` | A reason, without grant fields. |

An invalid mixture, such as a denial containing proposed permissions, is rejected. The final policy format remains the same across methods.

These aliases and dependencies are curated configuration. They were not learned from memory or automatically discovered from account permissions.

## 6. What the final output contains

The exact definitions live in [schema.py](policy_compiler/schema.py).

| Field | Meaning |
|---|---|
| `original_request` | The complete original request, preserved exactly. |
| `status` | Ready, clarification required, or denied. |
| `reads` | Resource IDs the task may read. |
| `actions` | Allowed operations, resources, recipients, argument restrictions and call limits. |
| `expiration` | Task-completion condition and a finite maximum duration. |
| `ambiguities` | Questions needed to resolve an unclear request. |
| `denial_reason` | Why the requested task is prohibited. |
| `schema_version` | The policy-format version. |

Grants also carry evidence labels. `explicit` means directly requested; `necessary_inference` means required support. An uncertain need cannot become an active permission merely by being marked uncertain. The label itself can still be predicted incorrectly.

Clarification and denial policies have empty read and action lists. The current design pauses the whole task when clarification is needed. Continuing independent permitted parts is a future extension.

A self-contained question can be ready with no permissions. “What is nine minus four?” needs neither memory nor a tool grant.

The validator rejects unknown fields, unknown IDs, malformed JSON, duplicate grants, mismatched requests and permissions outside either ceiling. Invalid model output or an API failure produces no accepted policy. The experiment runners stop that task instead of falling back to unrestricted retrieval.

**A `ready` policy has passed these checks. It can still misunderstand the user's intent.**

## 7. The exact change to the existing agent

The existing tracked source file changed for this integration is [EhrAgent/ehragent/medagent.py](EhrAgent/ehragent/medagent.py). Most other implementation work is in new modules.

### Two optional settings

The constructor now accepts:

```python
memory_scope=None
lessons_enabled=True
```

`memory_scope` supplies the task's memory boundary. With the default `None`, existing callers keep the full memory candidate pool.

`lessons_enabled` lets experiments compare lesson use with lessons switched off. The default keeps the existing lesson behavior. When disabled, the agent neither retrieves old lessons nor stores new ones in this path.

### Filter before ranking

A new `_eligible_memory_indexes()` helper returns the original row numbers that may be used.

Both retrieval paths use it:

- Text-distance retrieval compares only eligible questions.
- Embedding retrieval ranks only eligible question vectors.

An **embedding** is a numeric representation used to find similar text. We reuse the existing local DPR embedding model; we did not train a new retriever.

After ranking the smaller candidate list, the code maps selections back to the original row numbers. This keeps examples, checker judgments, poison labels and stored lessons attached to the correct records.

### Apply the same boundary to lessons

The native lesson path searches for similar past actions. It now searches only eligible action vectors. Otherwise, an excluded record could still affect the answer through its lesson.

The consistency checker continues to run. When lessons are enabled, explanations for rejected examples can still be stored and later inserted as warnings. The new flag controls that storage and retrieval for the comparison.

### Record what happened

The retrieval trace now also records whether a policy scope was enabled and which memory IDs were eligible. Existing trace fields record retrieved examples, survivors and lesson activity.

This lets evaluation distinguish an example that was blocked before retrieval from one that reached the checker and was rejected later.

## 8. How the memory gate works

[gate.py](contextual_memory/gate.py) validates the request-bound policy and snapshots its allowed reads. It stops admitting records after its time limit or explicit task completion. Non-ready policies admit nothing.

[ehr_gate.py](contextual_memory/ehr_gate.py) connects those reads to native memory records using protected resource labels. If a record belongs to several resources, all its labels must be allowed. Unknown or untagged records are excluded when the scope is active.

The earlier synthetic store, in [pipeline.py](contextual_memory/pipeline.py), separates index metadata from payload files. It filters metadata, ranks eligible entries, and only then reads selected payloads for checking.

The native EHR experiments load and prepare the memory corpus and embeddings as trusted setup. Their gate limits runtime retrieval and the text supplied to models. It does not claim that no host process ever inspected excluded memory bytes.

The policy schema describes tool permissions, but these experiments do not provide a complete runtime action monitor. Tool-call counters, tool execution and general information-flow enforcement remain outside the demonstrated boundary. The memory gate's completion/time checks are implemented separately.

## 9. Where the new code lives

| File or group | What it does |
|---|---|
| [schema.py](policy_compiler/schema.py), [validator.py](policy_compiler/validator.py) | Define policies and check their structure, identities and permission limits. |
| [compiler.py](policy_compiler/compiler.py) | Preserve the original complete-policy model baseline. |
| [effective.py](policy_compiler/effective.py) | Calculate the overlap between user and global limits. |
| [task_compiler.py](policy_compiler/task_compiler.py), [references.py](policy_compiler/references.py) | Infer needs, build policies and bind names to request text. |
| [grounded.py](policy_compiler/grounded.py) | Add resource aliases, dependencies and separate decision shapes. |
| [__main__.py](policy_compiler/__main__.py), [__init__.py](policy_compiler/__init__.py) | Expose the command-line and Python interfaces. |
| [baselines.py](policy_compiler/baselines.py), [compare.py](policy_compiler/compare.py), [evaluate.py](policy_compiler/evaluate.py) | Run comparison methods and score policies against annotations. |
| [develop_grounded.py](policy_compiler/develop_grounded.py) | Record the clinical compiler development runs. |
| [gate.py](contextual_memory/gate.py), [ehr_gate.py](contextual_memory/ehr_gate.py) | Enforce memory eligibility from validated policies. |
| [pipeline.py](contextual_memory/pipeline.py), [checker.py](contextual_memory/checker.py) | Connect permitted retrieval to the existing EHR consistency checker. |
| [prepare.py](contextual_memory/prepare.py), [ehr_prepare.py](contextual_memory/ehr_prepare.py), [prepare_followup.py](contextual_memory/prepare_followup.py) | Build fixed experiment inputs and record their origin and labels. |
| [experiment.py](contextual_memory/experiment.py) | Run the first controlled synthetic memory comparison. |
| [ehr.py](contextual_memory/ehr.py) | Run the five-question native clinical generation comparison. |
| [factorial.py](contextual_memory/factorial.py) | Compare eight combinations of policy source, retrieval count and lesson use. |
| [transport.py](contextual_memory/transport.py), [ehr_transport.py](contextual_memory/ehr_transport.py) | Bound, record, cache and replay model calls; verify reused response prefixes on resume. |
| [report_factorial.py](scripts/ehragent/report_factorial.py) | Produce the follow-up summary and memory-task subset analysis. |
| [verify_factorial.py](scripts/ehragent/verify_factorial.py), [verify_policy_small.py](scripts/ehragent/verify_policy_small.py) | Replay and audit saved native experiments. |
| [requirements-policy.txt](requirements-policy.txt) | List the small standalone compiler dependency set. |

New tests cover the compiler methods, scoring, memory gate, native EHR integration, lesson comparisons and response reuse. The project also contains fixed example policies, trusted contexts, fixture data and saved reports.

The implementation reuses the shared OpenRouter client and existing A-MemGuard checker. Earlier reports describe their own implementation stage; a statement that the compiler was standalone in the first milestone does not describe the later optional memory integration.

## 10. What the earlier experiments found

These stages use different inputs. Their scores should be read separately.

| Stage | Recorded result | What we learned |
|---|---|---|
| Original broad-context compiler pilot | 0/20 policy matches. | A model can copy maximum limits or guess missing scope. Validation alone does not establish task understanding. |
| Fresh messaging-policy comparison | Revised compiler 18/20; keyword control 18/20; original compiler 0/20. | The revised implementation improved over the original, but tied the simple rules overall. |
| First synthetic memory comparison | 80 task/configuration records. Revised policies delivered prohibited memory in 0/20 cases; ungated checker in 14/20. | The resource boundary worked on this fixture, but permitted poison still reached context. Two saved revised policies remained errors. |
| Native five-question clinical pilot | 40 episodes. Revised compiler matched 0/5 scopes and completed code generation on only 1/5 questions in each query condition. | The revised compiler transferred poorly to clinical vocabulary and dependencies. |
| Grounded clinical development | Five passes, 25 calls; final pass 5/5. The catalog-aware keyword control also matched 5/5. | The new catalog helped on already exposed development questions. This was not an independent test. |

The clinical pilot also produced a `DeleteDB` code proposal under manual and keyword scopes on a triggered question. Generated code never executed. That negative result shows that permitted poisoned examples can remain dangerous after access filtering.

The original clinical question was used for policy compilation before adding the attack trigger to the retrieval query. The trigger therefore did not become a new source of policy authority.

Details: [original pilot](policy_compiler/evaluation/RESULTS.md), [revised comparison](policy_compiler/evaluation/REVISION_RESULTS.md), [synthetic memory results](contextual_memory/RESULTS.md), and [clinical results](contextual_memory/EHR_RESULTS.md).

## 11. The latest Direction 1 follow-up

The latest experiment used 20 new synthetic read-only questions and 20 memory records. Ten records contained handwritten attacks. Sixteen questions expected answers; two expected clarification and two expected denial.

It used the native DPR retrieval and lesson path with the existing checker, followed by a short factual JSON answer stage. It did not run the complete clinical database agent or execute tools.

An **arm** means one configuration of the experiment. Twenty tasks across eight arms produced 160 episodes. `l0` means lessons off; `l1` means lessons on.

| Configuration | Tasks with unauthorized context / 20 | Correct answers / 16 | Tasks with poison examples in context / 20 |
|---|---:|---:|---:|
| Ungated, lessons off | 5 | 11 | 14 |
| Ungated, lessons on | 15 | 11 | 14 |
| Grounded policy, lessons off | 0 | 11 | 12 |
| Grounded policy, lessons on | 0 | 13 | 12 |
| Keyword policy, lessons on | 3 | 13 | 12 |
| Manual policy, lessons on | 0 | 14 | 12 |
| Matched retrieval count, lessons off | 2 | 7 | 9 |
| Matched retrieval count, lessons on | 10 | 8 | 9 |

“Unauthorized context” includes delivered examples and used lesson sources outside the annotated task scope. It counts affected tasks, not individual records. Answer correctness compares normalized short answers with saved acceptable answers.

The manual arm uses annotated scopes as a comparison target. The keyword control gets the same trusted alias/dependency catalog as the grounded compiler. The matched-count arms retrieve the number of records allowed by the grounded scope, up to four, but choose from the unrestricted pool.

Recorded policy scope/status matches were:

- Grounded compiler: **19/20**.
- Keyword control: **15/20**.
- Manual policies: **20/20**, by construction.

The grounded compiler incorrectly denied the arithmetic question that needed no memory. That failure stays in the policy and answer totals. All arms had zero recorded pipeline errors, but a valid, incorrect denial is still a task-understanding and utility failure.

Ungated arms retrieved even on questions that should ask for clarification, deny access or avoid memory. Some exposure reduction therefore comes from stopping retrieval. On the 15 questions annotated as needing memory, the ungated lessons-on arm had unauthorized context on 10 questions and 10 correct answers; grounded lessons-on had zero unauthorized-context cases and 13 correct answers.

Each arm began with the same memory and no lessons. Lessons accumulated within an enabled arm and reset between arms. This was one fixed sequence, with exact-prompt response reuse across arms, rather than repeated independent trials. Matched counts do not match final context length or lesson size.

The grounded arms retained 14 of 23 required clean-evidence items; ungated arms retained 13 of 23. Neither result shows that all needed evidence survived.

**Allowed poison remained:** grounded arms still delivered poisoned examples on 12 of 20 tasks. A lesson originating from a poisoned record may be a useful warning; its origin alone does not prove harm.

The completed saved run has 218 recorded unique responses, including 186 reused responses from the previous attempt and 32 new calls, plus 494 exact-prompt cache hits. Its successful response events recorded no API failures. An earlier interrupted attempt had answer-request formatting errors and remains saved separately.

See [FOLLOWUP_RESULTS.md](contextual_memory/FOLLOWUP_RESULTS.md), the [full metrics](results/direction1_followup/factorial/20261018T143223915841Z/metrics.json), and the [saved replay audit](results/direction1_followup/verification/20261018T143223915841Z.json).

## 12. What we can and cannot conclude

The code demonstrates a concrete way to compile request-specific policies, validate them, and restrict native example and lesson retrieval. The saved follow-up shows a useful result on its particular synthetic questions.

It does not establish broad security or general model accuracy:

- The coding assistant authored the synthetic questions, scopes and answers. Supervisor review is still pending.
- Catalog aliases and dependencies are manually supplied domain knowledge.
- No model was trained or calibrated, and no broad learning advantage has been established.
- There are no repeated trials, independent hidden benchmark or adaptive-attack evaluation in this follow-up.
- A correct permission policy does not prove that admitted text is true or harmless.
- A short correct answer or completed code generation does not prove complete task success.
- No harmful executed action, real database effect or actual clinical answer correctness was measured.
- The experimental clinical labels describe access to demonstration memories. They are not hospital access rules or patient privacy permissions.

The wider research proposal includes action enforcement, stronger selection and longer-term lesson controls. The implemented Direction 1 work supplies a prototype and experimental foundation for those extensions.

## 13. How the implementation was checked

The existing follow-up documentation records **100 passing scoped offline tests**: 61 policy tests, 20 EHR tests, 15 controlled-memory tests, three factorial tests and one transport-resume test.

They check permission intersections, malformed policies, request binding, aliases, dependency expansion, filtering before ranking, original row identities, lesson boundaries, lesson reset/off behavior, scoring failures and response-prefix reuse.

The broader legacy OpenRouter suite separately records eight passes and three failures: two old loader checks cannot import `llm_env`, and one mocked-retrieval test lacks `last_trace`. These are documented separately from the passing scoped suite.

Saved replay reran local native retrieval and lesson state using recorded model responses. The audit records matching prompts, policies, episode records and metrics, with historical artifacts preserved. File hashes are fingerprints used to detect changed inputs and sources; they do not prove that annotations are correct.

An interrupted run was resumed after adding exact embedding-result reuse. The resume path verifies the saved successful response prefix instead of asking the model again for different outputs. The preserved verification also covers a full native replay without that acceleration.

These are recorded checks from the implementation work. Writing this explanation did not rerun the live experiments or the full test suite.

## 14. How to use the code

Run commands from the project root using the existing environment.

### Validate a saved example without a network call

```bash
.venv/bin/python -m policy_compiler \
  --request 'Send Alice the Project X update on Slack' \
  --context policy_compiler/examples/context.json \
  --validate-response policy_compiler/examples/policy.json
```

This checks a saved policy. It does not infer a new one.

### Compile a new request

The next command makes a real call through the configured OpenRouter client:

```bash
.venv/bin/python -m policy_compiler \
  --request 'Send Alice the Project X update on Slack' \
  --context policy_compiler/examples/context.json
```

This uses `revised`. Add `--method original` for the baseline. To select the catalog-aware method explicitly:

```bash
.venv/bin/python -m policy_compiler --method grounded \
  --request 'When was the last prescription for this patient?' \
  --context contextual_memory/fixtures/ehr5/context.json \
  --resource-catalog policy_compiler/evaluation/clinical_catalog.json
```

The Python APIs are `compile_policy` for original, `compile_task_policy` for revised, and `compile_grounded_policy` for grounded.

Command exit codes are `0` for ready, `2` for clarification, `3` for denied, and `1` for invalid input, invalid output or a compilation failure.

### Run the offline checks

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_policy*.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_contextual_memory.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_ehr*.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_factorial.py' -v
```

### Replay the saved follow-up

```bash
.venv/bin/python -m contextual_memory.factorial \
  --replay results/direction1_followup/factorial/20261018T143223915841Z
```

Replay uses saved model responses and local DPR weights. It requires matching source/input fingerprints. After later code changes, use the saved run's source snapshot to reproduce that version. Native CPU embedding replay can take time.

New live evaluations make additional model calls. Normal follow-up evaluation checks that independent review is complete; the existing pre-review run was explicitly labeled diagnostic. Full commands and run conditions are in [FOLLOWUP.md](contextual_memory/FOLLOWUP.md).

## 15. What still needs to be done for Direction 1

1. Have the supervisor independently review the frozen task scopes, dependency assumptions and acceptable answers. The [review packet](contextual_memory/fixtures/followup20/REVIEW.md) is ready; [review.json](contextual_memory/fixtures/followup20/review.json) remains pending.
2. Test on independently written requests, more domains and genuinely unseen task families. Use strong deterministic controls with the same metadata.
3. Measure extra grants, missing grants, unnecessary clarification and incorrect denial separately. Do not count a failed policy as a successful refusal.
4. Evaluate trained or calibrated inference if pursuing the original learning contribution. The current methods use prompting and trusted code.
5. Extend the supported policy language for finer requested argument/time restrictions, conditional workflows and continuing independent permitted steps.
6. Add an external tool-action monitor and measure observed task outcomes before claiming protection from executed harmful actions.

The original Direction 1 proposal is in [CONTEXTUAL_DEFENSE_RESEARCH_README.md](CONTEXTUAL_DEFENSE_RESEARCH_README.md). The compiler design history is in [policy_compiler/REVISION.md](policy_compiler/REVISION.md), and the current follow-up method is in [contextual_memory/FOLLOWUP.md](contextual_memory/FOLLOWUP.md).
