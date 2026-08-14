# Protected multi-model holdout v1 Candidate 04: aggregate results

This report publishes only aggregate, sanitized evidence from a protected
evaluation. The suite, case identifiers, inputs, proposals, expected values,
private oracle, checkpoints, and individual outcomes remain unpublished. The
result is bounded to the frozen artifacts, suite, implementation, and execution
policy described here; it is not evidence of universal superiority.

Candidate 04 completed successfully as an execution, but its preregistered
success assessment was `not_passed`. DOHAA's aggregate final strict pass rate
was slightly higher than direct response, but only one model had a positive
difference and the primary paired result was not statistically significant.
Runtime availability was also poor for both comparator models. These failures
remain failed observations under the preregistered protocol and are not
discarded or imputed.

## Evaluation record

| Field | Value |
| --- | --- |
| Execution dates | August 13–14, 2026 |
| Suite | `protected-multimodel-holdout-v1-candidate-04-20260813` |
| Evaluation | `d13085bb-f069-4afb-adc3-472fed978580` |
| Protocol | [Multi-model generalization v1](../evaluation-protocols/multimodel-generalization-v1.md) |
| Protocol SHA-256 | `e798591ea38f6350b3c0a0975293c0978ae01cf83ad87f449cd9ff91f9558d7b` |
| Evaluated implementation | `0d05d5bacbda66d617007ec7d86d4ed490af4c16` |
| Canonical suite SHA-256 | `c1b7033d2a1a3ef9193885e9d675c5e4be50587b7700d5e5ba1ebf24de537fd8` |
| Canonical commitment SHA-256 | `3895df07d09892918d6ee1ee2eeca1ae4d1e2cbfd1ff6505a5b25b48b79fa3ba` |
| Canonical model-manifest SHA-256 | `b9dfec0edac20d67167fb249c47c1162fcfc82176834f4d144663019de46e73f` |
| Archived private result file SHA-256 | `b1daa57a8771907bcbfcc0b739b12db63243082fa7ce12163207d454eab112e2` |
| Private closure record SHA-256 | `e1e43de3a9af4dd246709d73bfceed0e499e179cbce52a0e181e4b4d5de3758a` |

The model manifest was frozen before suite authorship, and the suite commitment
was frozen before model execution. Each slot was executed separately under an
isolated just-in-time loading policy, and aggregation was performed offline in
the preregistered order. Aggregation made no model calls. Final integrity checks
recomputed the aggregate from the three checkpoints and verified zero resident
model instances after execution.

The experiment used 48 new unique cases, with 12 cases in each of four domains:
evidence synthesis, quantitative reconciliation, structured extraction, and
temporal reasoning. Each case ran once under direct response, uncontrolled
self-reflection, and DOHAA. Runtime failures remained failures in every
denominator.

The global unit was the unique case. For each condition, pass indicators were
averaged across the three models before assigning the paired sign. The
predeclared primary comparison was DOHAA versus direct response, using an exact
two-sided sign test at alpha 0.05. Domain analyses were exploratory and had no
multiple-comparison correction.

## Frozen model artifacts

All artifacts used a 64,000-token context.

- Primary slot: `qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive`, Q4_K_M.
  Artifact: `sha256:bbef58c37ce88820be9d98b6437f1cf4bac890c947bd55fc7b68e22098574231`.
- Qwen comparator: `qwen3.6-27b-mtp`, Q5_K_XL. Artifact:
  `sha256:5a3c61033581754d507ffdcbf0629214cbfbd58a2edbec80d93f6ec2af44d227`.
- Cross-family comparator:
  `gemma4-31b-qat-uncensored-hauhaucs-balanced-mtp`, Q4_K_M. Artifact:
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
are intentionally not published. Operational power, memory, model-loading, and
wall-time observations were not preregistered or normalized and are not
reported as comparative performance measurements.

## Aggregate condition results

| Condition | Mean final strict pass rate |
| --- | ---: |
| Direct | 51.39% |
| Self-reflection | 45.83% |
| DOHAA | **52.78%** |

DOHAA exceeded direct response by 1.39 percentage points. The self-reflection
figure is descriptive; it was not the primary comparison. All rates retain
runtime failures as failed outcomes.

## Primary paired comparison

| Comparison | Wins | Losses | Ties | Difference | Exact p-value |
| --- | ---: | ---: | ---: | ---: | ---: |
| DOHAA vs direct | 3 | 1 | 44 | +1.39 pp | `0.625` |

The 48 cases, rather than 144 model-case observations, are the independent
global units. A win means that the mean final pass indicator across frozen
models was higher for DOHAA on that case. The primary result did not meet the
predeclared alpha of 0.05.

## Results by model

| Slot | Direct | DOHAA | Difference | Wins | Losses | Ties | Exact p-value |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen 35B A3B primary | 95.83% | 100.00% | +4.17 pp | 2 | 0 | 46 | `0.5` |
| Qwen 27B comparator | 35.42% | 35.42% | 0.00 pp | 1 | 1 | 46 | `1.0` |
| Gemma 31B comparator | 22.92% | 22.92% | 0.00 pp | 0 | 0 | 48 | Not applicable |

Only the primary model had a positive DOHAA-minus-direct pass-rate difference.
The comparator rates were dominated by runtime failures and must not be read as
a clean quality comparison among successfully returned responses.

## Runtime availability

The following counts are failed condition outcomes, not discarded trials:

| Slot | Direct failures | Self-reflection failures | DOHAA failures |
| --- | ---: | ---: | ---: |
| Qwen 35B A3B primary | 1 | 2 | 0 |
| Qwen 27B comparator | 31 | 34 | 31 |
| Gemma 31B comparator | 37 | 42 | 37 |
| **Total** | **69** | **78** | **68** |

Across all models and conditions, 215 outcomes ended in a runtime failure:

| Stable failure code | Count |
| --- | ---: |
| `response.timeout` | 185 |
| `response.connection_failed` | 29 |
| `proposal.schema_invalid` | 1 |

Transport availability accounted for 214 of the 215 failures. The Qwen 27B
and Gemma comparators contributed 212 failures, while the primary model
contributed three. The failures were not retried outside the frozen execution
policy, removed from denominators, or replaced with post hoc observations.

## Exploratory results by domain

| Domain | Direct | Self-reflection | DOHAA | Difference vs direct |
| --- | ---: | ---: | ---: | ---: |
| Evidence synthesis | 97.22% | 77.78% | 94.44% | -2.78 pp |
| Quantitative reconciliation | 41.67% | 36.11% | 44.44% | +2.78 pp |
| Structured extraction | 38.89% | 38.89% | 38.89% | 0.00 pp |
| Temporal reasoning | 27.78% | 30.56% | 33.33% | +5.56 pp |

Each domain contains only 12 cases. These figures are exploratory, no
multiple-comparison correction was applied, and availability failures were
unevenly distributed across domains. They are not independent confirmatory
claims.

## Predeclared success assessment

| Criterion | Observed | Threshold | Status |
| --- | --- | --- | --- |
| Models with positive delta | 1 | At least 2 | Failed |
| Models with negative delta | 0 | At most 0 | Passed |
| Global positive delta | +1.39 pp | Greater than 0 | Passed |
| Global wins exceed losses | 3 wins, 1 loss | Wins greater than losses | Passed |
| Primary p-value | `0.625` | Less than 0.05 | Failed |
| Repair regressions | 0 | At most 0 | Passed |
| DOHAA average runtime calls | 1.0 | At most 1.5 | Passed |
| DOHAA/direct token ratio | Not available | At most 1.5 | Unevaluable |

Five criteria passed, two failed, and one was unevaluable. The token criterion
was unevaluable because valid usage telemetry was available for only 75 of 144
direct calls and 76 of 144 DOHAA calls. Partial reported totals must not be used
to compute the preregistered ratio.

The machine-readable assessment correctly records `passed: false` and
`status: "not_passed"`. The failed criteria cannot be overridden by the small
positive aggregate difference, and the unavailable token ratio cannot be
silently passed or imputed.

## Interpretation

Candidate 04 did not demonstrate the preregistered multi-model generalization
claim. The primary model produced a positive result, but the two comparator
models did not have positive pass-rate differences, and the global paired test
was not statistically significant.

The run was also severely availability-limited. Most comparator observations
failed before a valid proposal was available, principally through the frozen
120-second timeout. Consequently, Candidate 04 does not cleanly distinguish a
quality limitation from an inference-service availability limitation for those
models. This qualification does not change the protocol outcome: runtime
failures were predeclared as failures, so the valid result remains
`not_passed`.

Candidate 04 also does not erase the previously archived Candidate 03 result.
Together, the two runs show that the earlier positive result was not reproduced
under Candidate 04's frozen execution. Claims must therefore present both
results rather than selecting only the more favorable run.

## Limitations

- The evaluation used three local, quantized model artifacts under one frozen
  sampling policy and one repetition.
- The protected suite contained 48 synthetic cases with deterministic scoring;
  it did not test open-ended writing, research, creativity, or production
  traffic.
- Runtime availability was poor for both comparator models, particularly
  outside evidence synthesis.
- The suite authoring and execution were not independently performed or
  peer-reviewed.
- Per-domain samples were small, and domain results were exploratory.
- Token usage remained incomplete because failed calls had unavailable usage,
  preventing evaluation of the predeclared token-ratio guardrail.
- Power, memory, latency, and model-loading observations were operational, not
  normalized experimental measurements.
- This holdout must not be reused after infrastructure or policy changes and
  then presented as a new independent confirmation.

## Next steps

- Preserve Candidate 04 as a completed `not_passed` result without rerunning or
  relabelling it.
- Diagnose inference timeouts and connection failures outside this protected
  holdout.
- Validate runtime availability with public or newly created development cases.
- Preregister any revised infrastructure policy before another protected run.
- Author and freeze a new holdout for Candidate 05 rather than reusing this
  observed suite.
- Add blind human evaluation for open-ended tasks and seek independent
  replication across additional inference providers.
