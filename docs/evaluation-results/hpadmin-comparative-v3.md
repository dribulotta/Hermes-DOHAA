# HPADMIN Comparative V3: preregistered public result

**Preregistered assessment: `NOT_PASSED`**

HPADMIN Comparative V3 completed successfully as an execution. Its public
evidence chain, commitments, aggregates, paired statistics, input identity,
shared initial-proposal identity, local-only transport policy, and oracle
isolation checks all verified. The preregistered superiority claim did not
pass because the primary paired comparison contained only one discordant win.

## Evaluation record

| Field | Value |
| --- | --- |
| Execution date | September 1, 2026 |
| Suite | `hpadmin-comparative-protected-v3` |
| Cases | 40 synthetic cases across five administrative domains |
| Model | `qwen3.6-27b-mtp-p1` |
| Model artifact | `sha256:5a3c61033581754d507ffdcbf0629214cbfbd58a2edbec80d93f6ec2af44d227` (operator-declared, not independently attested) |
| Context | 64,000 tokens |
| Canonical suite SHA-256 | `d7d97c6ac19b096b7c51fccfb67a8b269a7b5605615043ce3ac394399bd51953` |
| Execution package manifest SHA-256 | `d1175e2b4a411363489617b5039bf34320c2ca25a2e4fc4cef4b99eb131a45d0` |
| Public bundle SHA-256 | `cfb36ccaa1dbec09a6b5f9e15b860423abc34e083bb94df96089c5df2387a50d` |

The four conditions received byte-identical visible inputs. Direct,
self-reflection, and DOHAA began from the same shared initial proposal;
the requested-high-reasoning condition received the same visible input and
sampling configuration but required its own inference request. The model's
internal reasoning state was requested/configured, not independently attested.

Expected results and `result_equals` remained outside inference. The pre-score
output was committed before the oracle was loaded, `oracle_feedback_events=0`,
and all 26 DOHAA feedback events came from the exact production allowlist.

## Primary results

| Condition | Strict passes | Pass rate | Runtime failures | Mean attributed calls |
| --- | ---: | ---: | ---: | ---: |
| Direct | 15/40 | 37.50% | 0 | 1.000 |
| Requested high reasoning (not attested) | 15/40 | 37.50% | 0 | 1.000 |
| Self-reflection | 15/40 | 37.50% | 0 | 2.000 |
| DOHAA | **16/40** | **40.00%** | 0 | 1.625 |

DOHAA versus Direct produced **1 win, 0 losses, and 39 ties**. The exact
two-sided sign-test p-value was `1.0`, above the preregistered alpha of `0.05`.
Seven of the eight required assessment checks passed; the only failed check was
`dohaa_vs_direct_p_lte_0_05`. Because the decision rule requires every check to
pass, the correct assessment is `NOT_PASSED`.

With zero losses, at least six discordant wins would have been needed for the
same exact two-sided test to cross the `0.05` threshold. The observed run had
one.

## What changed case by case

The sole DOHAA-over-Direct win was `payment-schedule-v3-04`. Direct and the
requested-high-reasoning condition failed the post-inference production-policy
and semantic scoring gates; both DOHAA and generic self-reflection passed after
a second inference. The observed improvement therefore was not unique to
DOHAA.

DOHAA did distinguish itself from self-reflection on
`procurement-selection-v3-01`: Direct, requested-high-reasoning, and DOHAA
passed, while self-reflection regressed from the shared passing proposal to a
failure. Across the full suite, DOHAA recorded one improvement and no
regressions.

## Exploratory domain results

| Domain | Direct | Requested high reasoning | Self-reflection | DOHAA |
| --- | ---: | ---: | ---: | ---: |
| Budget reconciliation | 0/8 | 0/8 | 0/8 | 0/8 |
| Exception triage | 5/8 | 5/8 | 5/8 | 5/8 |
| Invoice reconciliation | 7/8 | 7/8 | 7/8 | 7/8 |
| Payment schedule | 0/8 | 0/8 | 1/8 | 1/8 |
| Procurement selection | 3/8 | 3/8 | 2/8 | 3/8 |

These domain slices are exploratory and too small for independent claims.

## Compute disclosure

| Condition | Attributed logical calls | Reported tokens | Mean elapsed seconds |
| --- | ---: | ---: | ---: |
| Direct | 40 | 228,323 | 203.35 |
| Requested high reasoning | 40 | 227,899 | 220.09 |
| Self-reflection | 80 | 482,977 | 397.78 |
| DOHAA | 65 | 413,575 | 399.67 |

The complete experiment used 146 physical requests and 225 attributed logical
calls. One transport retry was recovered. These measurements are descriptive;
the conditions did not have equal compute budgets and the bundle contains no
sanitized hardware attestation.

## Interpretation

This is a valid negative result for the preregistered superiority claim. It
shows a small descriptive signal—`+2.5` percentage points, one win, and no
losses—but insufficient paired evidence. It does not demonstrate that DOHAA is
superior on this suite, nor does it establish universal quality, causal
attribution to one component, production safety, or generalization beyond the
five frozen administrative schemas.

The evaluation record also classifies its V2 predecessor as invalidated
development evidence for the primary claim: six of its eight DOHAA wins relied
on oracle-exclusive feedback. V2 is not included in the V3 result.

The now-public V3 suite is retired permanently and must never be reused as a
confirmatory holdout.

## Public evidence and verification boundary

- [Official public report (Spanish)](evidence/hpadmin-comparative-v3/PUBLIC-HPADMIN-COMPARATIVE-REPORT-v3.md)
- [Machine-readable public evidence](evidence/hpadmin-comparative-v3/PUBLIC-HPADMIN-COMPARATIVE-EVIDENCE-v3.json)
- [Preregistered protocol](evidence/hpadmin-comparative-v3/HPADMIN-COMPARATIVE-PROTOCOL-v3.json)
- [Complete recomputable public bundle](evidence/hpadmin-comparative-v3/hpadmin-comparative-public-v3-20260901-213831.tar.gz)
- [Bundle SHA-256](evidence/hpadmin-comparative-v3/hpadmin-comparative-public-v3-20260901-213831.tar.gz.sha256)

The public verifier recomputes the aggregate statistics and verifies source,
suite, pre-score, and execution commitments. It cannot independently re-score
the model proposals because raw proposals and the private pre-score artifact
are intentionally omitted. It also does not independently attest model
identity, internal reasoning state, hardware, or physical execution.

The matrix field `failed_production_gates` contains post-inference scoring-gate
failures. In particular, appearances of `semantic_assertions` in that field do
not mean those assertions were supplied as controller feedback. The controller
feedback audit is separate and records zero prohibited or oracle-derived
events.

To verify the archived bundle offline:

```bash
sha256sum -c hpadmin-comparative-public-v3-20260901-213831.tar.gz.sha256
tar -xzf hpadmin-comparative-public-v3-20260901-213831.tar.gz
bash verify-public-evidence-v3.sh
```
