# Native controller ablation: no incremental utility over the strong repair baseline

The frozen experiment completed all 288 model/mode configurations on 48 shared cases. The rule-aware DOHAA controller produced 250 correct accepted completions, versus 277 for a small verifier-and-retry baseline with the same gates and deterministic repair helper. The shared initial native Hermes proposal was correct in 201 configurations. The strong baseline succeeded alone in 27 configurations; DOHAA never succeeded alone.

The predeclared incremental-utility criterion was not met. DOHAA had 43 stable cases against the baseline's 48, with zero improving cases and five regressing cases. This is a negative result for the tested incremental-utility claim. It does not establish a universal result about every controller, task distribution or future implementation.

## Design and controls

This was a new architecture ablation following the separately closed learning study documented in issue #57 and PR #58. No earlier campaign was replayed, rescored or extended.

The 48 fresh synthetic cases covered two explicit-contract domains: 24 financial/quantity tasks and 24 source-bound registry tasks, across eight templates. Each case was tested on three locally served models with `none` and `medium` reasoning. The 288 configurations are repeated measurements on 48 shared cases, not 288 independent experimental units.

Each configuration shared one actual initial generation through native Hermes. The direct arm describes that initial proposal. Both verifier arms received the same contract and gates, including visible semantic formulas and committed evidence requirements. Both had a maximum of two native generations, including the shared initial generation.

- The **simple baseline** used the existing deterministic semantic repair helper without result-path restrictions. If necessary, it received all failed verifier feedback and one unrestricted full-proposal correction.
- **DOHAA** used the existing rule-aware controller, dependency-closed repair scope, preservation checks, rollback and evidence ledger. It received at most one native continuation under the same generation cap.

Continuation order was counterbalanced before inference. No artificial mistakes were inserted into real model responses. Private reference answers were used only for scoring. Correctness checked result values, required claims, committed evidence and absence of requested actions; numerically equal JSON numbers such as `42` and `42.0` were equivalent, while booleans were distinct from numbers.

A separate program using only the visible rules solved **48/48 cases without an LLM**. Success on this suite therefore does not establish persistent learning or general intelligence. Sharing the deterministic helper with the strong baseline avoids crediting its arithmetic work solely to the full controller.

## Correct completions

Each model/mode row contains 48 configurations.

| Model | Reasoning | Direct initial | Strong baseline | DOHAA |
|---|---|---:|---:|---:|
| Qwen 3.6 27B | none | 24 | 46 | 41 |
| Qwen 3.6 27B | medium | 48 | 48 | 48 |
| Qwen 3.6 35B A3B | none | 22 | 46 | 36 |
| Qwen 3.6 35B A3B | medium | 47 | 47 | 47 |
| Gemma 4 26B A4B | none | 18 | 45 | 33 |
| Gemma 4 26B A4B | medium | 42 | 45 | 45 |
| **Total / 288** | | **201** | **277** | **250** |

DOHAA improved on the direct initial proposal by 49 completions, but finished 27 behind the strong baseline. Both verifier arms recorded zero incorrect or unsafe accepted proposals. The direct arm emitted 81 incorrect proposals and had six failed initial generations; emission in that descriptive arm is not verifier approval.

At configuration level, both verifier arms succeeded in 250 cases, only the baseline succeeded in 27, only DOHAA succeeded in zero, and neither succeeded in 11. These counts are descriptive repeated measurements, not an additional independent significance test.

## Predeclared case-level endpoint

A shared case was stable when it succeeded in at least four of its six model/mode configurations.

| Domain | Strong baseline stable cases | DOHAA stable cases |
|---|---:|---:|
| Financial/quantity | 24/24 | 19/24 |
| Source-bound registry | 24/24 | 24/24 |
| **Total** | **48/48** | **43/48** |

The positive criterion required at least five net additional stable cases, a one-sided exact paired superiority test at alpha 0.05, no negative domain delta, zero incorrect or unsafe DOHAA acceptances, and a generation-count ratio no greater than 1.25.

Observed stable-case improvements were zero and regressions five. The predeclared one-sided superiority p-value was 1.0. With zero improving cases out of 48, the declared independent-case model gives an upper one-sided 95% bound of `1 - 0.05 ** (1/48) = 0.0605034093` for improvement probability. Under those conditional assumptions, the planned 10-point minimum gain is ruled out. The recorded verdict is `minimum_gain_ruled_out_under_case_model`.

The inference is limited to the synthetic generator and its case-independence assumptions. Shared templates and limited domains restrict generalization. The paired-count procedure and exact-binomial interval interpretation follow the [NIST paired-test reference](https://itl.nist.gov/div898/software/dataplot/refman1/auxillar/mcnemar.htm) and [NIST exact-binomial interval reference](https://itl.nist.gov/div898/software/dataplot/refman2/auxillar/exacbici.htm). This report does not substitute a post-hoc test for the original superiority endpoint.

## Cost and failure accounting

The experiment used **340 actual native generations**: 288 shared initial generations, eight baseline continuations and 44 DOHAA continuations. Counterfactual arm totals count the shared initial generation once per arm: direct 288, baseline 296 and DOHAA 332. Those arm totals must not be summed as actual experiment consumption. DOHAA used 12.16% more generations than the strong baseline.

Execution took 7,643.393 seconds, about 2 hours 7 minutes. Recorded generation-time totals, including shared initial time per arm, were 7,277.411 seconds for the baseline and 7,515.134 for DOHAA. These are descriptive counterfactual totals, not isolated throughput benchmarks; model loading, ordering and block setup affect timing.

An aggregate description of the already audited terminal decisions recorded:

- DOHAA: 250 `run.succeeded`, 24 `budget.exhausted`, eight `repair.unsignaled_failure`, and six `runtime.failed`.
- Among the 27 configurations where only the baseline succeeded: 24 DOHAA controller-attempt exhaustion outcomes and three `repair.unsignaled_failure` outcomes.
- Four shared initial generations exhausted the native 8,192-token limit, and two had invalid responses. These were terminal measured failures, with no replacement calls.
- The strong baseline performed 73 deterministic repairs.

**Controller attempt exhaustion and native token exhaustion are separate events.** The former describes the controller's bounded attempt policy; it must not be reported as 24 native token-limit failures. These aggregate codes identify where the completion loss occurred; they do not prove that any particular proposed code change would fix it.

## Execution integrity and closure

The executable protocol and cases were frozen before inference. Complete manifest and protocol bytes were retained outside the server before dispatch. The experiment used an isolated integrated checkout, commit `85a813ce14828fd57825537160080cb01f55b99b`, tree `2acef0788e277aacf40488176b6c7032e40c3eca`, containing pending development changes. The native Hermes commit was `f4e8eb156876a27687a9bc7db0e1b6343a9c35c0`.

- Manifest SHA-256: `57bcc8dba9088df7feeba4deb9d874d4beead502256c1d8d63642b1817c6c564`.
- Protocol SHA-256: `bfcfe5a206990490aca847f4d0d7c18d4043eb967150d172f57dca28fbb854ce`.
- Serial client concurrency one; 18 blocks; maximum 864 generations; 8,192 tokens per generation; 12-hour wall cap with a 600-second no-new-call margin; a disk reserve before each dispatch.
- Sixteen offline harness tests passed, including a complete synthetic 288-configuration run and reconstruction. These fixtures are not real-model results.
- The separate supervisor retained 18 block commitments. Its one final internal audit reconstructed decisions from the pinned inputs and native receipts, verified wire requests and responses, worker isolation, ledger chains, ordering and accounting, and passed. It was not rerun after closure.
- All 18 blocks verified their owned-model unload. Two subsequent independent CLI listings found no models loaded. Installed services retained their original processes and installed source remained clean. No transport failure was recorded.
- All 195 `none` generations had no observed reasoning; all 145 `medium` generations had observed reasoning. The requested-mode distinction was verified.

The study closed on 2026-09-08 at 22:08:24 UTC, with executor and audit exit codes zero. Public summaries and hashes do not independently attest execution; `execution_attested` remains false. Private cases, prompts, responses, credentials, endpoints and worker logs are withheld. No live candidate was activated and no release or deployment was performed.

The accompanying JSON preserves the aggregate result and its limits. Documentation of this negative outcome does not fix the controller's completion gap or establish learning utility. Future changes require their own evidence; this closed campaign remains unchanged.
