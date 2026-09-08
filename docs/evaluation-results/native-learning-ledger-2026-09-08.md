# Prospective native prompt-learning study: useful learning not demonstrated

This new study closed on 2026-09-08 after 270 real model requests and 7953.561 seconds. The internal independent reconstruction passed all ten integrity checks. Its utility verdict is **`useful_learning_not_demonstrated`**. Five paired holdout comparisons completed; the sixth proposal was rejected before evaluation. An integrity pass does not imply a positive utility result or a complete six-comparison matrix.

This is an agent-generated documentation contribution. It records an isolated development experiment using the changes in PRs #54, #55 and #56. It does not deploy those changes, activate a candidate, publish V5, or change existing evidence.

## Protocol and results

Four training cases feed one prompt proposal for each of three models under `none` and `medium` reasoning. An accepted proposal is compared against the unchanged original on 24 heldout cases, using 48 serial calls per combination. Inputs are new relative to the prior closed campaigns. The full protocol and manifest were retained outside the execution host before the first dispatch. Model, prompt and input policy bindings are checked by the private audit.

Each candidate must gain at least three paired cases, reach at least 18/24 correct, and have no regressions, candidate failures or proposed actions. The global criterion additionally requires all six evaluations, valid evidence and observed mode distinctions, a macro accuracy gain of at least 0.10, no negative per-combination delta, and at least one qualifying candidate. These thresholds were frozen prospectively.

| Model | Reasoning | Original correct | Candidate correct | Improvements / regressions |
|---|---|---:|---:|---:|
| Qwen 3.6 27B | none | 4/24 | 4/24 | 0 / 0 |
| Qwen 3.6 27B | medium | 24/24 | 24/24 | 0 / 0 |
| Qwen 3.6 35B A3B | none | 1/24 | 0/24 | 0 / 1 |
| Qwen 3.6 35B A3B | medium | 24/24 | 24/24 | 0 / 0 |
| Gemma 4 26B A4B | none | 2/24 | 4/24 | 2 / 0 |
| Gemma 4 26B A4B | medium | Not evaluated | Proposal rejected | Not evaluated |

No evaluated candidate met its criteria. The original Gemma `none` arm had one `invalid_response`, already counted as a failure; evaluated candidates had no admission failures or proposed actions. All requested reasoning modes were observably distinguished in their recorded calls.

Gemma `medium` completed four training calls, then its single proposal generation ended with provider finish reason `length` under the configured 8192-token cap. The adapter returned the terminal failure `budget_exhausted`; continuation attempts were blocked and no additional generation occurred. The server's completion was known and cleanup succeeded. The 48 evaluation calls for that combination were never dispatched. Its missing score is not represented as zero, and the six-comparison macro gain remains null.

## Accounting and provenance

The 270 real requests comprise 24 training, six proposal and 240 paired-evaluation calls, below the preregistered maximum of 318. A single client request ran at a time. All 17 completed phases verified exact owned-model unload, and a separate post-closure check found no loaded models. No native transport failure occurred. Installed services and source were unchanged.

The final auditor reconstructed all six states, checked 270 native traces against terminal receipts, verified frozen sources and cases, and verified that 17 phase commitments preceded their generations. It generated no model requests. Both the executor and auditor exited successfully. The deterministic zero-LLM reference passed all 28 cases, including training.

The machine-readable [summary](native-learning-ledger-2026-09-08.json) includes all ten integrity checks, request accounting, nulls for unexecuted scores, source hashes, and the frozen manifest/protocol digests. Private requests, responses, candidate prompts, identifiers, credentials, endpoints and oracle answers are not published. Public summaries and hashes alone do not independently attest execution: `execution_attested` remains false.

## Interpretation and preserved limits

This procedure did not demonstrate useful prompt learning on these cases within its fixed budget. Qwen originals with reasoning already reached 24/24; Gemma's two improvements without reasoning did not reach the required gain or absolute accuracy. One Qwen combination regressed. The sixth comparison is unavailable because proposal generation exhausted its budget.

The 24 shared cases are not 120 or 144 independent observations. No significance claim, general superiority, persistent learning, or production readiness follows. Earlier contract-compliance results answer a different question and are not pooled here. A closed earlier interrupted learning study is also preserved separately; this report does not assign it a retrospectively inferred failure cause.

The study is closed. Do not replay it, extend its budget, repair responses, or rescore outcomes to obtain a pass. Further research requires a distinct question, fresh heldout cases and a prospectively fixed protocol. No live candidate adoption is justified by this result. The documentation can be reverted without changing software behavior or deleting private evidence.
