# Native Hermes contract matrix — 7 September 2026

The new development integration passed the preregistered local matrix criteria: **DOHAA 144/144, direct 104/144, self-reflection 106/144**. There were no incorrect DOHAA acceptances, accepted actions or DOHAA runtime failures. This supports bounded verification/repair under the declared contracts. It does not establish persistent learning or general model superiority.

| Model | Reasoning | Direct | Self-reflection | DOHAA |
|---|---|---:|---:|---:|
| qwen3.6-27b-mtp | none | 12/24 | 11/24 | 24/24 |
| qwen3.6-27b-mtp | medium | 24/24 | 24/24 | 24/24 |
| qwen/qwen3.6-35b-a3b | none | 10/24 | 13/24 | 24/24 |
| qwen/qwen3.6-35b-a3b | medium | 24/24 | 23/24 | 24/24 |
| google/gemma-4-26b-a4b-qat | none | 10/24 | 12/24 | 24/24 |
| google/gemma-4-26b-a4b-qat | medium | 24/24 | 23/24 | 24/24 |

## What the comparison establishes

Without reasoning, direct scored 32/72, reflection 36/72 and DOHAA 72/72. With reasoning, direct already scored 72/72, reflection 70/72 and DOHAA 72/72. The overall gain over reflection was 38/144, or 26.39 percentage points; all three models improved overall and no cell regressed.

The controller recorded **44 deterministic repair events across 44 trials**, with only two additional DOHAA model requests. Repair events are not a count of otherwise-wrong answers: mathematical numeric scoring and stricter product representations can differ. A separate null-skeleton baseline using only the visible contract derived all 18 formula-case answers without any model call. Six source-binding cases require evidence/claims that this baseline does not construct.

Two native generations exhausted 8192 tokens without a usable final answer, both in reflection. They remain failed outcomes. The prospective harness blocked the native iteration-summary fallback before constructing an extra request. No generation was retried or converted into a success, and no sent request violated the fixed parameters.

## Design and audit

The 24 new synthetic cases span 12 templates: reconciliation, invoice, weighted average, business calendar, UTC-offset duration, filtered relational join, source selection, stable deduplication, missing inputs, scalar source binding, escaped source pointers and untrusted source instructions. Eighteen use declared derivation formulas; six use source/evidence bindings. Source flags are data, not cryptographic authentication.

Each case was evaluated with three installed models and reasoning none/medium. One initial native Hermes proposal is shared across direct, reflection and DOHAA. Reflection gets another generation if admission succeeds; DOHAA can apply deterministic repair and has at most one additional generation. All receive the same contract, with temperature 0, top_p 1, seed 9072703 and 8192 tokens per generation. Actual compute is not equalized. References are held outside prompts/controller feedback.

The 22-file local manifest, cases, schedule, protocol and separate auditor were fixed before the first new inference. This was local preregistration, not an externally timestamped registry. The automated auditor independently recounted retained proposals and checked response hashes, identities, parameters, budgets and read-only ledgers. All 17 validity checks and 144 ledger checks passed. The campaign used 290 actual generation requests over 4610.64 seconds, plus six excluded capability probes.

Concurrency was 1. Native agents had no tools, memory, user profile, MCP, title generation or background review. Every model block finished before exact-instance unload and catalog verification; final cleanup passed. No GPU/system-RAM telemetry was collected, and model file bytes were not independently attested.

## Source and limits

The tested source is an **unreleased development integration** of PR [#42](https://github.com/dribulotta/Hermes-DOHAA/pull/42), [#44](https://github.com/dribulotta/Hermes-DOHAA/pull/44), [#46](https://github.com/dribulotta/Hermes-DOHAA/pull/46) and [#47](https://github.com/dribulotta/Hermes-DOHAA/pull/47). Its 255 tests and five public checks passed before this matrix. Main/deployed code is not represented as having this behavior.

- Integrated commit: `1b848e9ded18c6a2f05229ed3b259ee379753cc1`; tree: `218d3c8f15be484e9c1948e4165b3df8afbeaded`.
- Native runtime commit: `f4e8eb156876a27687a9bc7db0e1b6343a9c35c0`.
- Protocol SHA256: `a6d3192056bdbea90879527e0be4ec28cea83b2c92e1c2092fdb30f6e5249435`.
- Suite SHA256: `52afef9deb3b380aefa3fa609907a600930177ae3a91d9532ba05958499b222c`.

This small authored diagnostic has dependent variants and shared proposals, not 432 independent samples. Explicit formulas can directly specify the derivation; acceptance is conditional on trustworthy inputs and correctly authored rules. Private model responses and ledgers are not included in this aggregate publication. The independent software recount is not an external replication.

The earlier r4 campaign retains its invalid verdict and 17 incorrect DOHAA acceptances. The new run uses new cases, coverage, seed and a larger common token cap; it is not a rescore or an isolated causal estimate of a single PR. Its former capacity test remains skipped. A new backend-only capacity protocol is separate from these accuracy scores.

Persistent learning, learned-candidate activation, shadow execution, automatic rollback and real actuators remain unproven/incomplete. This report neither publishes V5 nor authorizes deployment.

[Machine-readable aggregate](native-contract-matrix-20260907.json) · [Frozen protocol](native-contract-matrix-20260907.protocol.json)
