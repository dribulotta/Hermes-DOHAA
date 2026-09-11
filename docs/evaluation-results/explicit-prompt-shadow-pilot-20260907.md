# Explicit-contract prompt-shadow pilot: no complete-task improvement

After fixing the response contract and verifying two new native format controls,
a separately preregistered pilot generated one new prompt candidate from four
new training tasks and compared it with its baseline on twelve new held-out
synthetic ledger tasks. The previous negative pilot, its candidate, cases and
recordings were not reused or rescored.

Both arms received the same explicit response envelope. The training evidence
contained structural admission feedback, while held-out feedback and answers
were not supplied to the candidate generator. The candidate was quarantined
before the held-out plan was fixed. Each generation used a fresh restricted
native Hermes AIAgent with tools, memory, user profiles, MCP and background
review disabled. Proposed actions were never executed.

The pilot used `qwen3.6-27b-mtp`, reasoning disabled, generation seed 9074007,
training seed 9074008 and held-out seed 9074009, temperature 0, top-p 1 and at most
8,192 output tokens per request. It completed 29 native requests in 154.26 seconds:
four training calls, one candidate proposal and 24 paired held-out calls. Calls
were serial, with alternating per-case arm order. No retries or replacements
were allowed. The preregistered criteria remained at least one paired improvement,
12/12 candidate successes, zero regressions, zero candidate failures and zero actions.

| Preregistered measure | Baseline | Candidate |
|---|---:|---:|
| Admitted outputs | 12/12 | 12/12 |
| Completely correct tasks | 0/12 | 0/12 |
| Admission/runtime failures | 0 | 0 |
| Proposed actions | 0 | 0 |

There were zero paired improvements and zero paired regressions. The verdict
is **`does_not_meet_predeclared_criteria`**. All 25 recording, identity, parameter,
profile, budget, chronology, retention and cleanup audit checks passed. Independent
scoring reproduced the retained result. The model was unloaded and its absence
verified after each phase; the final read-only check found the normal services
and deployed revision unchanged.

A post-hoc field diagnosis, without changing any grades, found:

| Field equal to the reference | Baseline | Candidate |
|---|---:|---:|
| `net_cents` | 0/12 | 0/12 |
| `included_count` | 7/12 | 12/12 |
| `included_ids` | 8/12 | 12/12 |

The candidate's selected IDs and counts matched every held-out reference, but its
monetary totals did not. These component counts were not preregistered success
criteria, so they do not replace the negative complete-task verdict or establish
statistical significance. The result demonstrates that the explicit envelope
was admitted in this run; it does not demonstrate useful complete-task prompt
adaptation, persistent autonomous learning or generalization. It also does not
evaluate the full DOHAA controller with deterministic arithmetic repair: these
were native baseline-versus-candidate prompt generations scored independently.

This single model, reasoning mode, task family and candidate are a limited
diagnostic. There is no claim that every candidate or every reasoning mode must
fail. There is no approval to activate this candidate. Its state remains
`quarantined`. The result will not be rerun or reinterpreted to obtain a pass.

The fixed implementation corresponds to public tree
`a58ac80484c0a739b5e81fe749c81efc8b701619` in PR #49, combined in an isolated
development copy with the other pending corrections. Before this pilot, that
integration passed 303 tests and five public checks; its harness passed 21 tests
and five actual-native-agent checks with synthetic transport and zero live model
requests. The original candidate prompt, instances, responses and oracle answers
remain private. The adjacent JSON contains safe aggregate results.
