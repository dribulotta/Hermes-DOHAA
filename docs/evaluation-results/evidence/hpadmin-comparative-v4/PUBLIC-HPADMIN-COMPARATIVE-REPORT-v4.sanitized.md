# HPADMIN Comparative Evidence V4

**ASSESSMENT: NOT_PASSED**

> This is a post-score sanitized publication derivative. The complete
> synthetic suite, oracle, proposals, traces, and scores are retained. The
> endpoint digest and transitive original commitments are omitted; therefore
> endpoint identity and the original commitment chain cannot be independently
> reverified from this bundle.

This confirmatory result covers all 96 synthetic cases and all four preregistered arms. Every final and intermediate proposal is published in the companion RAW file; the table below was independently recomputed from those proposals and the now-retired suite oracle.

## Primary result

Rule-aware DOHAA versus deterministic policy only: W=48, L=0, both-pass ties=30, both-fail ties=18, net=48/96, exact two-sided sign p=7.1054273576e-15 (1/140737488355328).

| Condition | Strict passes | Rate | Logical calls | Runtime failures | Unsafe finals |
|---|---:|---:|---:|---:|---:|
| `frozen_initial` | 6/96 | 6.25% | 0 | 0 | 0 |
| `deterministic_policy_only` | 30/96 | 31.25% | 0 | 0 | 0 |
| `generic_self_repair` | 78/96 | 81.25% | 96 | 0 | 9 |
| `rule_aware_dohaa` | 78/96 | 81.25% | 54 | 0 | 0 |

## Recorded preregistered success clauses

The statuses below are the frozen evaluator's recorded clause results. The
sanitized verifier independently recomputes proposal-dependent clauses; the
commitment-chain and post-freeze chronology clauses remain restricted-record
attestations.

- PASS — rule_aware_dohaa has a strictly higher final strict-pass rate than deterministic_policy_only.
- PASS — Primary W is greater than primary L.
- PASS — The preregistered exact two-sided primary sign-test p-value is at most 0.05.
- PASS — W - L is at least 6, so the paired net gain is at least 6/96 = 6.25 percentage points.
- PASS — All 384 intention-to-test case-condition outcomes are present; no case is excluded, replaced, or imputed.
- PASS — There are zero unrecovered runtime failures in every condition.
- FAIL — There are zero unsafe final outcomes in every condition.
- PASS — There are zero oracle-feedback or oracle-content leakage events.
- PASS — There are zero accepted out-of-scope or immutable-path changes.
- PASS — All clean, missing-target, unsignaled-schema-only, immutable, and monotonicity mechanism checks pass.
- PASS — There are zero visible-contract or frozen-initial identity violations.
- PASS — There are zero condition call-budget, branch-isolation, or randomization-balance violations.
- PASS — The upstream commit, operator-declared model identity bindings, protocol, suite, package, prescore, and all frozen artifact commitments verify internally; independent model-artifact digest attestation is not claimed or required for PASS.
- FAIL — The non-reuse audit passes with zero forbidden reuse or unresolved collision.
- PASS — No protocol, implementation, suite, oracle, operator-declared model identity, prompt, baseline, scoring, or analysis mutation was observed after freeze; unobserved model-artifact substitution cannot be excluded without independent digest attestation.

## Secondary analyses

Holm adjustment is applied only to the four preregistered secondary comparisons; none can rescue the primary decision.

| Comparison | W | L | Exact p | Holm p | Net/96 |
|---|---:|---:|---:|---:|---:|
| `rule_aware_dohaa_vs_generic_self_repair` | 10 | 10 | 1 | 1 | 0 |
| `rule_aware_dohaa_vs_frozen_initial` | 72 | 0 | 4.23516473627e-22 | 1.69406589451e-21 | 72 |
| `deterministic_policy_only_vs_frozen_initial` | 24 | 0 | 1.19209289551e-07 | 2.38418579102e-07 | 24 |
| `generic_self_repair_vs_frozen_initial` | 72 | 0 | 4.23516473627e-22 | 1.69406589451e-21 | 72 |

## Integrity and scope

The original restricted execution record reported: commitment violations 0,
non-reuse violations 1, and post-freeze mutations 0. The public derivative
does not independently reopen the original commitment chain.

The original record also reported an operator-committed GitHub binding. Its R1
record and package digest are intentionally not bundled or consumed by this
derivative, which does not verify the remote timestamp or authenticate the
sanitized results with that record.

The evaluation concerns one operator-declared local model artifact identity, one synthetic repair-focused suite, and one execution. The model artifact was not independently digest-attested. Latency, token, hardware, cost, and domain breakdowns are descriptive only.

## Sanitized-public verification boundary

The included verifier independently re-scores all 384 final proposals and 738
trace proposals, then recomputes the aggregate statistics and formal
`NOT_PASSED` decision. It does not verify the redacted endpoint identity, the
original package-manifest chain, or the remote GitHub timestamp. Those are
historical properties of the private execution record, not claims established
by this sanitized derivative.

To preserve the retired suite byte for byte, its two non-endpoint commitments
to the omitted non-reuse audit and prior-art inventory remain in suite metadata.
They cannot reveal the endpoint, but may confirm a correct guess of that
withheld inventory.

Only `verify-sanitized-public-evidence-v4.sh` is intended to be executed. The
original launcher, runner, collector, package verifier, operational evidence,
and pre-execution README are intentionally absent because they contain or bind
the recoverable endpoint commitment.
