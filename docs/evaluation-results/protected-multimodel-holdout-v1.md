# Protected multi-model holdout v1: aggregate results

This report publishes only aggregate, sanitized evidence from a protected
evaluation. The suite, case identifiers, inputs, proposals, expected values,
private oracle, checkpoints, and individual outcomes remain unpublished. The
result is bounded to the frozen artifacts, suite, implementation, and execution
policy described here; it is not evidence of universal superiority.

The preregistered quality comparison was strongly positive. The complete
success assessment nevertheless remained `not_passed` because incomplete
runtime token reporting made one cost criterion unevaluable. This distinction
is part of the result and is not overridden in this report.

## Evaluation record

| Field | Value |
| --- | --- |
| Execution dates | August 12–13, 2026 |
| Suite | `protected-multimodel-holdout-v1-candidate-03-20260812` |
| Evaluation | `4aaf3940-1532-44b1-a331-940b0e6b555e` |
| Protocol | [Multi-model generalization v1](../evaluation-protocols/multimodel-generalization-v1.md) |
| Protocol SHA-256 | `e798591ea38f6350b3c0a0975293c0978ae01cf83ad87f449cd9ff91f9558d7b` |
| Evaluated implementation | `2f5d0cd994e70828db7293281e9c9b7fb70d8561` |
| Canonical suite SHA-256 | `e965d0e0c191940585fe2256798a4360f70f1b493aba755f86608b5ca2e191c6` |
| Canonical commitment SHA-256 | `2857f6a85f1cd5e5909281a3618741a1282aacdf98c421384f42204ece2cfe6f` |
| Canonical model-manifest SHA-256 | `b9dfec0edac20d67167fb249c47c1162fcfc82176834f4d144663019de46e73f` |
| Archived private result SHA-256 | `3e67368945a409a6b72abb51fdf7b99deb5319f9fa8b939ec149ebccff3d40f8` |
| Aggregate command SHA-256 | `e5fce1e7d6b8b2b75ef295eeb992266758cbe8a8fe293c9d3faf824d4701117b` |

The model manifest was frozen before suite authorship, and the suite commitment
was frozen before model execution. The three slots were executed separately to
enforce model-residency isolation, then aggregated offline in the exact
preregistered order. Aggregation performed no runtime calls.

The experiment used 48 new unique cases, with 12 cases in each of four domains:
evidence synthesis, quantitative reconciliation, structured extraction, and
temporal reasoning. Each case ran once under direct response, uncontrolled
self-reflection, and DOHAA. Runtime failures, if present, remained failed
observations.

The global unit was the unique case. For each condition, pass indicators were
averaged across the three models before the paired sign was assigned. The
predeclared primary comparison was DOHAA versus direct response, using an exact
two-sided sign test at alpha 0.05. Domain analyses were exploratory and had no
multiple-comparison correction.

## Frozen model artifacts

All artifacts used a 64,000-token context.

- Primary slot: `qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive`,
  Q4_K_M. Artifact:
  `sha256:bbef58c37ce88820be9d98b6437f1cf4bac890c947bd55fc7b68e22098574231`.
- Qwen comparator: `qwen3.6-27b-mtp`, Q5_K_XL. Artifact:
  `sha256:5a3c61033581754d507ffdcbf0629214cbfbd58a2edbec80d93f6ec2af44d227`.
- Cross-family comparator:
  `gemma4-31b-qat-uncensored-hauhaucs-balanced-mtp`, Q4_K_M.
  Artifact:
  `sha256:71667f9e601a4b914a98425c59150b731f6e15d260d661dbd1f1ee07469fc7db`.

## Execution policy

| Parameter | Value |
| --- | ---: |
| Repetitions | 1 |
| Condition-order seed | `20260819` |
| Sampling seed | `20260819` |
| Temperature | `0.0` |
| Top-p | `1.0` |
| Reasoning effort | `none` |
| Timeout | 120 seconds |

The runtime URL, credentials, deployment paths, and server-local identifiers
are intentionally not published. Operational observations of power, memory,
and elapsed wall time were not preregistered, normalized, or included as
experimental performance results.

## Aggregate condition results

| Condition | Mean final strict pass rate |
| --- | ---: |
| Direct | 60.42% |
| Self-reflection | 72.92% |
| DOHAA | **94.44%** |

DOHAA improved on direct response by 34.03 percentage points. The
self-reflection figure is descriptive; it was not the primary comparison.

## Primary paired comparison

| Comparison | Wins | Losses | Ties | Difference | Exact p-value |
| --- | ---: | ---: | ---: | ---: | ---: |
| DOHAA vs direct | 28 | 0 | 20 | +34.03 pp | `7.451e-09` |

The 48 cases, rather than 144 model-case observations, are the independent
global units. A win means that the mean final pass indicator across frozen
models was higher for DOHAA on that case.

## Results by model

| Slot | Direct | DOHAA | Difference | Wins | Losses | Ties | Exact p-value |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen 35B A3B primary | 43.75% | 97.92% | +54.17 pp | 27 | 1 | 20 | `2.16067e-07` |
| Qwen 27B comparator | 68.75% | 97.92% | +29.17 pp | 14 | 0 | 34 | `0.000122070312` |
| Gemma 31B comparator | 68.75% | 87.50% | +18.75 pp | 9 | 0 | 39 | `0.00390625` |

All three frozen models had a positive final-pass-rate difference. The single
Qwen 35B paired loss is distinct from a repair regression; the predeclared
repair-regression count was zero.

## Exploratory results by domain

| Domain | Direct | Self-reflection | DOHAA | Difference vs direct |
| --- | ---: | ---: | ---: | ---: |
| Evidence synthesis | 66.67% | 77.78% | 97.22% | +30.56 pp |
| Quantitative reconciliation | 61.11% | 75.00% | 91.67% | +30.56 pp |
| Structured extraction | 80.56% | 94.44% | 97.22% | +16.67 pp |
| Temporal reasoning | 33.33% | 44.44% | 91.67% | +58.33 pp |

Each domain contains only 12 cases. These results are exploratory, no
multiple-comparison correction was applied, and the domain p-values are not
used as independent confirmatory claims.

## Predeclared success assessment

| Criterion | Observed | Threshold | Status |
| --- | --- | --- | --- |
| Models with positive delta | 3 | At least 2 | Passed |
| Models with negative delta | 0 | At most 0 | Passed |
| Global positive delta | +34.03 pp | Greater than 0 | Passed |
| Global wins exceed losses | 28 wins, 0 losses | Wins greater than losses | Passed |
| Primary p-value | `7.451e-09` | Less than 0.05 | Passed |
| Repair regressions | 0 | At most 0 | Passed |
| DOHAA average runtime calls | 1.0 | At most 1.5 | Passed |
| DOHAA/direct token ratio | Not available | At most 1.5 | Unevaluable |

Seven of eight criteria passed and none failed. The token-usage completeness
check was false, so the cost ratio could not be computed. Under the
preregistered fail-closed rule, an unevaluable criterion prevents an overall
pass. The machine-readable assessment therefore correctly records
`passed: false` and `status: "not_passed"`.

The missing token measurement must not be imputed after the fact, and the
quality evidence must not be relabelled as a complete protocol pass.

## Interpretation

Within this protected suite and frozen execution, DOHAA produced a large,
consistent improvement over direct response across three distinct model
artifacts. The primary result was positive, paired wins exceeded losses, the
exact p-value was below the predeclared alpha, every model had a positive
difference, and no repair regressions were recorded.

This supports the bounded claim that deterministic verification and repair can
improve final strict correctness across the tested artifacts when tasks expose
a trustworthy machine-checkable relation. It does not establish universal
model or task generalization, complete safety, or semantic truth without an
independent verifier.

The composite protocol outcome is not a full pass. It combines strong quality
evidence with an unresolved cost-measurement requirement.

## Limitations

- The evaluation used three local, quantized model artifacts under one frozen
  sampling policy and one repetition.
- The protected suite contained 48 synthetic cases with deterministic scoring;
  it did not test open-ended writing, research, creativity, or production
  traffic.
- The suite authoring and execution were not independently performed or
  peer-reviewed.
- Per-domain samples were small, and domain results were exploratory.
- Token usage reporting was incomplete, preventing evaluation of the
  predeclared token-ratio guardrail.
- Power, memory, latency, and model-loading observations were operational, not
  normalized experimental measurements.
- This holdout must not be reused to guide changes and then presented as an
  independent confirmation.

## Next steps

- Diagnose token-usage completeness without altering this archived result.
- Correct and test cost telemetry before preregistering another evaluation.
- Use a newly authored and frozen holdout for any subsequent confirmation.
- Add blind human evaluation for open-ended tasks.
- Seek independent replication across additional model families and inference
  providers.
