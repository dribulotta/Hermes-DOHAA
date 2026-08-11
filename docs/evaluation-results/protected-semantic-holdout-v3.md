# Protected semantic holdout v3: aggregate results

This report publishes only aggregate, sanitized evidence from a protected evaluation. The suite, individual
results, case identifiers, inputs, proposals, expected values, and private oracle remain unpublished. The report
is a bounded confirmation for the evaluated suite and runtime policy, not evidence of universal superiority.

## Evaluation record

| Field | Value |
| --- | --- |
| Execution date | August 11, 2026 |
| Suite | `protected-semantic-holdout-v3-20260811` |
| Evaluation | `7430c98d-ae8a-4956-ba84-33e6e9ed553e` |
| Protocol and evaluated implementation commit | `d90e29ffb76f57108642081bc694718cb21d2a1c` |
| Canonical suite SHA-256 | `f851f0f1c529a65e95179cfa8cab5f0c280e0ef1ba24de7a9d6c879c00808b75` |
| Canonical commitment SHA-256 | `53f6eb0252edecfd14d1680253e688f5eb58aed8060f4263375a99b710f22589` |
| Archived private result SHA-256 | `d4cbcf44555b65f94a236a2e2d03d83f26e5e007f2dd809e3af815fa18f60ade` |

The commitment was created before the evaluation was run. The experiment consisted of one execution with one
repetition over 40 novel, unique cases. It covered four domains with 10 cases each: evidence synthesis,
quantitative reconciliation, structured extraction, and temporal reasoning. Thirty-two cases contained relations
potentially eligible for deterministic repair; eight were deliberately non-repairable controls.

The paired conditions were direct response, uncontrolled self-reflection, and DOHAA. The statistical unit was the
unique case. The primary metric was final strict passage of every gate, and the predeclared primary comparison was
DOHAA versus direct response. The primary test was an exact two-sided sign test, and pass-rate intervals are 95%
Wilson intervals. Per-domain statistics are exploratory, without correction for multiple comparisons. Runtime
failures, had any occurred, were required to remain in the denominator as failures.

## Runtime policy

| Parameter | Value |
| --- | --- |
| Adapter | `hermes_api` |
| Hermes-DOHAA | `0.1.0a1` |
| Model alias | `dohaa-runtime` |
| Model artifact | `lmstudio:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:Q4_K_M:ctx64000:experts8` |
| Reasoning effort | `none` |
| Temperature | `0.0` |
| Top-p | `1.0` |
| Sampling seed | `20260811` |
| Condition-order seed | `20260811` |
| Timeout | 120 seconds |
| Repetitions | 1 |

The runtime URL is intentionally not published.

## Primary results

| Condition | Strict passes | Final pass rate | Wilson 95% CI | Runtime failures |
| --- | ---: | ---: | ---: | ---: |
| Direct | 33/40 | 82.5% | 68.05%–91.25% | 0 |
| Self-reflection | 34/40 | 85.0% | 70.93%–92.94% | 0 |
| DOHAA | 40/40 | 100.0% | 91.24%–100.0% | 0 |

DOHAA produced seven improvements, zero regressions, 40 final passes, and zero runtime failures. Its absolute
improvement over direct response was 17.5 percentage points.

## Paired comparisons

| Comparison | Wins | Losses | Ties | Difference | Exact p-value |
| --- | ---: | ---: | ---: | ---: | ---: |
| DOHAA vs direct | 7 | 0 | 33 | +17.5 pp | 0.015625 |
| DOHAA vs self-reflection | 6 | 0 | 34 | +15.0 pp | 0.03125 |
| Self-reflection vs direct | 2 | 1 | 37 | +2.5 pp | 1.0 |

DOHAA versus direct was the predeclared primary comparison; the other comparisons are secondary. The seven cases
that initially failed under DOHAA were the same seven cases that initially failed in direct mode. This alignment
does not mean that their complete initial proposals were identical.

## Repair-mechanism audit

Six of the seven improvements were deterministic semantic repairs. Each required one model call, completed on the
controller's first attempt, replaced only authorized result pointers, was revalidated through every gate, and
finished at zero distance from the private oracle.

The seventh improvement was a non-repairable control. DOHAA did not apply deterministic repair; it used a normal
second cognitive attempt, whose second proposal passed every gate. Among all eight non-repairable controls, seven
passed unchanged, one needed the normal retry, and none was modified improperly by deterministic repair. This
report intentionally does not describe the control's content or the specific cause of its initial error.

DOHAA made 41 model calls for 40 cases, an average of 1.025 calls per case.

## Cost and latency

| Condition | Average calls | Average elapsed seconds | Total reported tokens | Tokens per case |
| --- | ---: | ---: | ---: | ---: |
| Direct | 1.000 | 4.805 | 58,546 | 1,463.65 |
| Self-reflection | 2.000 | 10.425 | 160,696 | 4,017.40 |
| DOHAA | 1.025 | 4.527 | 59,315 | 1,482.88 |

DOHAA used approximately 1.31% more tokens than direct response and approximately 63% fewer tokens than
self-reflection. Its observed average latency was lower than direct response and approximately 57% lower than
self-reflection. These latency differences are descriptive and must not be interpreted as an independent
performance benchmark.

## Exploratory results by domain

| Domain | Direct | Self-reflection | DOHAA |
| --- | ---: | ---: | ---: |
| Evidence synthesis | 90% | 100% | 100% |
| Quantitative reconciliation | 80% | 90% | 100% |
| Structured extraction | 100% | 100% | 100% |
| Temporal reasoning | 60% | 50% | 100% |

Each domain contains only 10 cases. These results are exploratory, no correction for multiple comparisons was
applied, and they must not be presented as definitive independent conclusions.

## Self-reflection result

Self-reflection repaired one case and introduced a regression in another. It finished at 34/40, only one case
above direct response, while using 80 calls and 160,696 tokens. The regressed case is intentionally not described.

## Predeclared criteria

All three predeclared primary criteria were met:

1. DOHAA's final strict pass rate was higher than direct response.
2. DOHAA's paired wins exceeded its paired losses against direct response.
3. DOHAA had no pass-to-fail regressions.

No additional criteria were created after observing the results.

## Interpretation

The results provide protected evidence that, on this suite and with this runtime policy, DOHAA improved the final
quality of the same cognitive runtime through conservative deterministic repair and bounded retries, without
regressions and at a cost close to direct mode.

They do not establish that DOHAA is universally superior, demonstrate complete safety, generalize to every model
or domain, or prove semantic truth outside the deterministic relations evaluated. This was not an independent or
peer-reviewed trial.

## Limitations

- The evaluation used one model and artifact, one sampling policy, and a single execution.
- The sample comprised 40 synthetic cases in a suite oriented toward deterministic relations.
- The suite was created after the evaluated mechanism was fixed and integrated, but not by an independent third
  party.
- Samples by domain were small, and the domain results are exploratory.
- There was no human evaluation of open-ended tasks and no real production traffic.
- This holdout must not be reused to guide new modifications and then presented as independent confirmation.

## Next steps

- Create new independent, frozen holdouts.
- Evaluate multiple models and quantizations.
- Test multiple sampling policies.
- Add open-ended tasks with blind human evaluation.
- Exercise operational scenarios with approvals and simulated actions.
- Seek external validation or third-party reproduction.
- Publicly preregister future protocols when confidentiality permits.
