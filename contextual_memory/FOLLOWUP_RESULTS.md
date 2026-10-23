# Direction 1 follow-up results

**Independent review remains pending for the supervisor. These are assistant-authored diagnostics.**

The new compiler uses trusted aliases/dependencies and status-specific outputs. It matched 5/5 previously exposed clinical development scopes after five development passes (25 calls). The keyword control also matched 5/5. No model weights were trained.

The fresh read-only QA comparison ran 20 tasks across eight arms (160 episodes) with native DPR, A-MemGuard consistency checking, and native lessons. Sixteen tasks expect answers; two expect clarification and two denial. No real tools or clinical database code executed.

| Arm | Unauthorized context / 20 | Correct answers / 16 | Poison examples / 20 | Lesson use / 20 | Pipeline errors / 20 |
|---|---:|---:|---:|---:|---:|
| ungated_l0 | 5 | 11 | 14 | 0 | 0 |
| ungated_l1 | 15 | 11 | 14 | 16 | 0 |
| grounded_l0 | 0 | 11 | 12 | 0 | 0 |
| grounded_l1 | 0 | 13 | 12 | 10 | 0 |
| keyword_l1 | 3 | 13 | 12 | 10 | 0 |
| manual_l1 | 0 | 14 | 12 | 10 | 0 |
| matched_l0 | 2 | 7 | 9 | 0 | 0 |
| matched_l1 | 10 | 8 | 9 | 10 | 0 |

Policy scope/status matches out of 20: `{"grounded": 19, "keyword": 15, "manual": 20}`.

Pipeline errors count invalid policies and execution-pipeline failures. A valid but incorrect denial remains a policy/utility failure even if this error counter is zero. Required clean retention and poison-origin lesson counts are in the full metrics.

Required clean evidence retained: ungated 13/23; grounded 14/23. These counts are the same with lessons off. The grounded compiler incorrectly denied the self-contained arithmetic request; that failure remains in the answer and policy denominators.

Ungated arms retrieve even for ambiguous, prohibited and self-contained requests. The following descriptive subset excludes those categories and retains all 15 requests annotated as requiring memory, including any failures.

| Arm | Ready memory tasks | Unauthorized context | Correct answers |
|---|---:|---:|---:|
| ungated_l0 | 15 | 2 | 10 |
| ungated_l1 | 15 | 10 | 10 |
| grounded_l0 | 15 | 0 | 11 |
| grounded_l1 | 15 | 0 | 13 |
| keyword_l1 | 15 | 3 | 12 |
| manual_l1 | 15 | 0 | 13 |
| matched_l0 | 15 | 2 | 7 |
| matched_l1 | 15 | 10 | 8 |

Matched arms use the grounded policy's retrieval count over the unrestricted candidate pool. This controls record count, not final context size or lesson bytes. Lessons reset between arms and accumulate within enabled arms. Poison-origin lessons can be useful warnings; origin alone is not harm.

These results do not establish independent generalization, a trained learning contribution, clinical answer accuracy or harmful executed-action prevention. The aliases/dependencies are curated metadata; automated account-permission provisioning is not implemented.

One attempt was interrupted for answer-request JSON formatting, and a second was resumed with exact embedding memoization. All attempts are preserved. Saved successful responses are verified/reused rather than rerolled. No compiler prompt, annotation or scorer definition was changed after fresh outputs were observed.

- [Full report](../results/direction1_followup/factorial/20261018T143223915841Z/REPORT.md)
- [All records](../results/direction1_followup/factorial/20261018T143223915841Z/records.json)
- [Metrics](../results/direction1_followup/factorial/20261018T143223915841Z/metrics.json)
- [Ready-memory subset](../results/direction1_followup/analysis/20261018T143223915841Z/ready_memory_subset.json)
- [Episode CSV](../results/direction1_followup/analysis/20261018T143223915841Z/episode_summary.csv)
- [Supervisor review packet](fixtures/followup20/REVIEW.md)
- [Method and reproduction](FOLLOWUP.md)

[Offline replay and preservation audit](../results/direction1_followup/verification/20261018T143223915841Z.json).
