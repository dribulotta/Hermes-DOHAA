# Corrected DOHAA at a fixed 2048-token operating budget

The prospective [issue #70](https://github.com/dribulotta/Hermes-DOHAA/issues/70)
study completed all **192 configurations** and passed its supervisor's single final
reconstruction audit. Corrected DOHAA improved the shared direct model responses,
but **did not demonstrate incremental quality over the strong simple baseline**:
160 versus 162 correct completions. At the preregistered stable-case endpoint,
DOHAA had zero gains and two regressions against simple. This is an observed
conditional negative result, not proof that every DOHAA implementation is useless.

| Arm | Correct configurations | Rate | Stable cases | Counterfactual calls |
|---|---:|---:|---:|---:|
| Direct shared initial response | 111/192 | 57.81% | Not the primary paired endpoint | 192 |
| Strong simple verifier/repair | 162/192 | 84.38% | 30/32 | 198 |
| Corrected DOHAA | 160/192 | 83.33% | 28/32 | 192 |

Simple and DOHAA each accepted **zero incorrect or unsafe proposals**. Direct emitted
55 incorrect proposals without verifier authority; its `incorrect_accepted` field
must not be interpreted as a verifier accepting them. No arm emitted an accepted
action. Direct totals therefore describe emitted response quality, while the two
verification arms require both correctness and gate-authorized acceptance.

## Model and reasoning observations

| Model | Reasoning effort | Direct | Simple | DOHAA |
|---|---|---:|---:|---:|
| Qwen 27B | none | 16/32 | 32/32 | 32/32 |
| Qwen 27B | medium | 32/32 | 32/32 | 32/32 |
| Qwen 35B A3B | none | 13/32 | 29/32 | 29/32 |
| Qwen 35B A3B | medium | 19/32 | 19/32 | 19/32 |
| Gemma 26B A4B | none | 13/32 | 31/32 | 29/32 |
| Gemma 26B A4B | medium | 18/32 | 19/32 | 19/32 |

The reasoning distinction was verified: every none cell had zero observed reasoning;
all 32 calls in each medium cell showed reasoning. There were **26 verified terminal
token-budget failures**: 13 in Qwen35 medium and 13 in Gemma medium. They remained
measured failures, with no replacement call or widened budget. This illustrates
the selected operating limit, not a general ranking of model reasoning ability.

## Paired endpoint and cost

The primary unit was one of 32 cases, stable when at least four of its six model/mode
configurations succeeded. Stable financial cases were 16/16 for both systems;
registry cases were 14/16 for simple and 12/16 for DOHAA. The fixed favorable criterion
required at least five net additional stable cases, one-sided exact paired p<=.05,
no negative domain delta, no incorrect/unsafe DOHAA acceptance and call ratio<=1.25.
It was not met: zero gains, two regressions, favorable p=1.0. Two observed regressions
alone are not presented as statistically significant general inferiority.

With zero improving cases, the declared independent-case model gives a one-sided 95%
upper bound `1 - 0.05 ** (1/32) = 0.0893682` on improvement probability. The frozen
verdict is `minimum_gain_ruled_out_under_case_model`: a ten-point minimum gain is
ruled out under those assumptions. Fixed templates, synthetic domains and repeated
model/mode measurements constrain generalization; 192 configurations are not 192
independent cases. Pair-count and interval interpretation follow
[NIST McNemar/sign-test guidance](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mcnemar.htm)
and [exact binomial intervals](https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/exacbici.htm).

The study made **198 actual verified generation calls** in 5785.649 seconds (96.43 minutes).
The shared initial response is counted once overall. Counterfactual per-arm costs
count it once for each arm; these columns must not be added. DOHAA used 192 versus
simple 198 calls, a 3.03% reduction, while completing two fewer configurations and two
fewer stable cases. That quality/cost tradeoff does not satisfy the positive criterion.

Counterfactual generation time was 5690.108 seconds for direct and DOHAA, 5738.306 seconds
for simple. Medians per configuration were 14.774, 14.774 and 15.357 seconds respectively.
These reuse the same initial-call measurements and exclude controller CPU and model
lifecycle overhead; they are not independent end-to-end latency or energy benchmarks.

## Scope, integrity and closure

The 32 fresh cases comprised 16 financial/quantity and 16 source-bound registry tasks,
using reused templates and new exact instances. A separately implemented visible-rule
solver without an LLM solved 32/32. The experiment therefore tests incremental
architecture utility on explicitly calculable contracts, not persistent learning,
broad intelligence or correctness of open-ended real-world tasks. The simple arm
had the same gates, visible feedback and deterministic repair helper; both repair
arms could use at most one extra generation after their shared initial proposal.

All calls had the same 2048 output-token cap, model seed 908171701, temperature 0, top_p 1,
HTTP 180s and worker 210s limits. Execution was serial, with a 576-call/six-hour ceiling
and 24 blocks of 8 cases. The reduced cap was fixed before observations as a new operating
condition; no historical 8192-token scores are paired with these results and no
historical timeout cause is claimed. Preparation passed 20 Linux harness tests,
including a full synthetic 192-configuration executor/witness/audit, and 15 Windows
tests. The fixed integrated source had already passed 461 project tests.

The single final audit reconstructed every request and decision from frozen inputs,
terminal receipts and worker wire records, checked evidence chains and exact model
lifecycle accounting, and returned 0; the executor also returned 0. All 24 blocks
verified exact owned-instance unloading and an empty catalog. Both owned processes
had exited at the reporting check; frozen/installed source and services were unchanged.
The audit was not rerun for reporting. Closure: 2026-09-09T10:49:12.478090+00:00.

Before launch, a separate process retained all 72 frozen files plus the tracked source
archive in a separate private directory on the SAME CT105. Per-block commitments
were also retained by the supervisor before dispatch. This is same-host retention,
not off-host or cryptographic execution attestation (`execution_attested:false`).
Only finite aggregate metadata was exported; no private cases, responses, credentials
or logs were published. No deployment, activation, service restart, merge or V5 release
occurred. Earlier S2B, original ablation, incomplete corrected-controller trial and
canary remain closed and unchanged. No replacement campaign was started at closure.

Preregistration: [fixed hashes published before dispatch](https://github.com/dribulotta/Hermes-DOHAA/issues/70#issuecomment-5599403410).
Manifest: `d98e0e43b763b2d3ecea801948f384626aa29a83913be2d1cd2a28d356da5a3b`.
Protocol: `2bec424db19074bada37dbbb64e2b057ebbc6d169fb9007911f96f3edda34cd5`.
Source commit: `8af03ac6aa3b6338c2521ade9448d4a631ac877a`.
Source tree: `6b3398bc4fb57181a1574526d5bfc8c4dc0348e6`.
Native commit: `f4e8eb156876a27687a9bc7db0e1b6343a9c35c0`.
The source was an isolated integration including #62, #66, #69, not deployed main.
The companion JSON includes full finite aggregate audit tables, timing and identities.
