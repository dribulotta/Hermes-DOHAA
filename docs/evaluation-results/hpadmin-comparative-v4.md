# HPADMIN Comparative V4: preregistered public result

**Preregistered assessment: `NOT_PASSED`**

HPADMIN Comparative V4 completed all 96 cases and four conditions; the frozen
evaluator returned `NOT_PASSED`. The primary comparison strongly favored
rule-aware DOHAA over the deterministic-policy-only baseline, but the
preregistered rule required all 15 clauses to pass and two did not. Thirteen
clauses passed.

## Evaluation record

| Field | Value |
| --- | --- |
| Completion date | September 3, 2026 |
| Suite | `hpadmin-comparative-protected-v4` |
| Cases | 96 synthetic cases across six administrative domains |
| Conditions | Frozen initial, deterministic policy only, generic self-repair, and rule-aware DOHAA |
| Model | `qwen3.6-27b-mtp-p1` |
| Model artifact | `sha256:5a3c61033581754d507ffdcbf0629214cbfbd58a2edbec80d93f6ec2af44d227` (operator-declared, not independently attested) |
| Upstream commit | `3b81dc1bce20861f643be13c7801090e72a81b53` |
| Historical pre-inference GitHub record | `88de7f7aa690aaec68ba6b4f860d20b8e874aa3e` (not consumed by the sanitized verifier) |
| Canonical suite SHA-256 | `b9911d35fbdbea17911857db508ba281a42b69ab2353fbeaa7bf741c797070ce` |
| Public artifact | Post-score privacy-sanitized full-evidence derivative |
| Sanitized bundle SHA-256 | `dbf88cfea9fb98c2aa4d882c4ab89660a7f760384a0bd193415e8ba0f0b194ba` |

The original V4 package commitment remains public for provenance, but its
launcher stopped during preflight before protected inference. The corrected R1
record is the historical commitment associated with the restricted execution;
the sanitized verifier does not consume or independently validate it.

## Primary result

| Condition | Strict passes | Rate | Logical model calls | Runtime failures | Unsafe finals |
| --- | ---: | ---: | ---: | ---: | ---: |
| Frozen initial | 6/96 | 6.25% | 0 | 0 | 0 |
| Deterministic policy only | 30/96 | 31.25% | 0 | 0 | 0 |
| Generic self-repair | 78/96 | 81.25% | 96 | 0 | 9 |
| Rule-aware DOHAA | **78/96** | **81.25%** | **54** | **0** | **0** |

Against deterministic policy only, rule-aware DOHAA produced **48 wins, zero
losses, 30 both-pass ties, and 18 both-fail ties**. The pass-rate difference
was **+50.00 percentage points**. The exact two-sided sign-test p-value was
`7.105427357601002e-15`, exactly `1/140737488355328`. The preregistered paired
95% interval for the difference was approximately **+33.776 to +61.762
percentage points**.

Every clause attached to the primary superiority comparison passed.

## Why the overall assessment is `NOT_PASSED`

The protocol combined all required clauses with `ALL_REQUIRED_AND`. Two of 15
clauses failed, and no secondary result may override that decision.

1. **Global zero-unsafe clause.** The generic self-repair comparator produced
   nine protocol-defined unsafe final proposals, all involving changes to
   immutable paths. Rule-aware DOHAA, deterministic policy only, and the frozen
   initial condition each produced zero unsafe finals. This is an observed
   comparator result, not a DOHAA safety failure, but the frozen clause required
   zero across every condition.
2. **Frozen non-reuse version mismatch.** The restricted non-reuse artifact declared
   `schema_version: "1.0"`, while the frozen renderer required
   `hpadmin-nonreuse-audit/4.0`. That artifact reported coverage of 40 prior
   sources, zero exact collisions in case IDs, visible contracts, initial
   proposals, or expected results, a passing semantic review, and zero
   unresolved collisions. Its private prior-art inventory is withheld, so
   those substantive audit claims are restricted-record attestations. The
   version mismatch still had to count as one formal violation under the
   preregistered evaluator.

The second failure is an evaluator-contract defect, but it cannot be repaired
or reinterpreted after seeing the result. V4 therefore remains
`NOT_PASSED`.

## Generic-repair comparison

Rule-aware DOHAA and generic self-repair both passed 78/96 cases. Their paired
comparison was 10 wins and 10 losses with an exact p-value of `1.0`, so V4 does
not establish strict-pass superiority over generic repair.

Descriptively, DOHAA reached the same aggregate pass count with 54 logical
model calls rather than 96, a reduction of 42 calls (43.75%), and produced zero
unsafe finals rather than nine. Generic repair made 97 physical requests due
to one recovered transport retry; DOHAA made 54. These compute and safety
figures are descriptive and do not replace the preregistered decision.

## Integrity and scope

All 384 intention-to-test case-condition outcomes are present. The sanitized
offline verifier re-scores all 384 final proposals and 738 trace proposals and
recomputes the gate scores, oracle scores, aggregate statistics, and paired
tests from the published proposals and retired oracle. The restricted execution
record reports:

- zero unrecovered runtime failures;
- zero oracle-feedback or oracle-content leakage events;
- zero accepted scope or immutable-path violations in the rule-aware controller;
- zero visible-input, frozen-initial, call-budget, branch-isolation, or
  randomization-balance violations;
- zero commitment violations and zero observed post-freeze mutations.

The public verifier establishes the proposal-dependent results above. It does
not independently reopen the original package-manifest chain, endpoint binding,
prescore chronology, or remote GitHub timestamp; the recorded zero commitment
violations remain a restricted-record attestation. That limitation cannot
change `NOT_PASSED`, because the nine unsafe generic-comparator finals and the
frozen non-reuse mismatch independently force the negative decision.

This is bounded evidence from one operator-declared local model artifact, one
synthetic repair-focused suite, and one execution. The artifact identity was
not independently digest-attested. Hardware, latency, token, and cost
measurements are descriptive or unavailable. The result does not establish
universal model quality, production safety, or generalization beyond these six
schemas.

## Privacy-sanitized evidence and permanent retirement

- [Browser-readable sanitized report](evidence/hpadmin-comparative-v4/PUBLIC-HPADMIN-COMPARATIVE-REPORT-v4.sanitized.md)
- [Machine-readable sanitized evidence](evidence/hpadmin-comparative-v4/PUBLIC-HPADMIN-COMPARATIVE-EVIDENCE-v4.sanitized.json)
- [Sanitized protocol projection](evidence/hpadmin-comparative-v4/HPADMIN-COMPARATIVE-PROTOCOL-v4.sanitized.json)
- [Sanitized non-reuse projection](evidence/hpadmin-comparative-v4/NONREUSE-AUDIT-v4.sanitized.json)
- [Sanitization manifest](evidence/hpadmin-comparative-v4/SANITIZATION-MANIFEST-v4.json)
- [Privacy-sanitized outcome-recomputable bundle](evidence/hpadmin-comparative-v4/hpadmin-comparative-v4-public-sanitized-v1.tar.gz)
- [Bundle SHA-256](evidence/hpadmin-comparative-v4/hpadmin-comparative-v4-public-sanitized-v1.tar.gz.sha256)

The sanitized bundle publishes the full synthetic outcome evidence: retired
suite and expected results, frozen initial proposals, final, intermediate, and
rejected proposals, controller and deterministic traces, request parameters,
scores, and the offline verifier. It excludes credentials, raw transport
responses, endpoint coordinates, endpoint-dependent historical commitments,
operational host artifacts, and the private non-reuse source inventory. It is
a publication derivative, not a byte-identical copy of the restricted
execution package.

Because the suite itself is retained byte for byte, its two non-endpoint
commitments to the omitted non-reuse audit and prior-art inventory remain in
suite metadata. They do not bind the endpoint, but may confirm a correct guess
of that withheld inventory. This residual boundary is explicit in the
sanitization manifest.

Because the oracle and proposals are now public,
`hpadmin-comparative-protected-v4` is retired permanently and must never be
reused as confirmatory data. Any follow-up confirmation requires a newly frozen
V5 suite and a non-reuse schema contract corrected before freeze.

To verify the archive offline:

```bash
sha256sum -c hpadmin-comparative-v4-public-sanitized-v1.tar.gz.sha256
mkdir hpadmin-comparative-v4-public-sanitized-v1
tar -xzf hpadmin-comparative-v4-public-sanitized-v1.tar.gz \
  -C hpadmin-comparative-v4-public-sanitized-v1 --strip-components=1
cd hpadmin-comparative-v4-public-sanitized-v1
bash verify-sanitized-public-evidence-v4.sh
```
