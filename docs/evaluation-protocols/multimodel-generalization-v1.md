# Multi-model generalization protocol v1

Status: preregistered; no protected suite has been authored or executed.

This protocol tests whether the bounded quality improvement observed with one
runtime artifact persists across three preselected model slots. It fixes the
design before model outputs or new holdout cases are observed. The canonical
machine-readable declaration is
[`examples/multimodel-evaluation-protocol.json`](../../examples/multimodel-evaluation-protocol.json).

The protected semantic holdout v3 is closed development evidence. Its cases,
results, and oracle must not be reused for this protocol.

## Sequence and authority

The following order is mandatory:

1. merge this protocol and its validator;
2. implement and merge the multi-model executor and aggregate analysis;
3. select and freeze the exact three model artifacts and server policies;
4. only then author 48 new protected cases;
5. freeze and externally anchor the new suite commitment;
6. execute every model and condition without tuning between runs;
7. retain failures in all denominators and publish only sanitized aggregates.

No protected suite may be authored before steps 1–3 are complete. A model
artifact may not be substituted after the model manifest is frozen. A failed
or unavailable model remains a failed preregistered slot unless the entire
experiment is abandoned and a new protocol is published.

## Model slots

Exactly three slots are required:

| Slot | Selection rule |
| --- | --- |
| Primary Qwen 35B-A3B | Freeze the current primary artifact and server configuration |
| Qwen 27B comparator | Freeze an artifact that fits on the same inference host |
| Cross-family comparator | Freeze one available non-Qwen model in the 26B–35B class |

The exact artifact identifier, quantization, context limit, expert count when
applicable, backend version, and model-server configuration must be recorded
before suite authorship. Aliases alone are insufficient evidence.

## Protected suite

The new holdout contains exactly 48 unpublished cases:

| Domain | Cases |
| --- | ---: |
| Evidence synthesis | 12 |
| Quantitative reconciliation | 12 |
| Structured extraction | 12 |
| Temporal reasoning | 12 |

Every case is new and must not be copied, paraphrased, or parameter-swapped
from prior protected holdouts. The suite uses the existing hidden-oracle,
result-specification, and semantic-assertion safeguards. Every contract uses
two bounded attempts and the same visible inputs in all three conditions.

## Conditions and execution

Each model runs `direct`, `self_reflection`, and `dohaa` for every case. The
conditions use the same model artifact, proposal schema, inputs, sampling seed,
and deterministic final gates within a trial.

The fixed execution policy is:

| Parameter | Value |
| --- | ---: |
| Repetitions | 1 |
| Condition-order seed | 20260819 |
| Sampling seed | 20260819 |
| Temperature | 0.0 |
| Top-p | 1.0 |
| Reasoning effort | `none` |
| Timeout | 120 seconds |

Runtime failures count as failures. No prompt, gate, timeout, sampling policy,
or model configuration may be changed after the first model run begins.

## Primary analysis

The primary comparison is DOHAA versus direct response. The primary metric is
final strict passage of every deterministic gate.

The independent unit is the unique case. For the global comparison, each
case's pass indicator is averaged across the three frozen models for each
condition. The sign of the paired DOHAA-minus-direct difference is then used
in an exact two-sided sign test. This keeps the three model observations for
one case from being misreported as three independent cases.

Per-model exact sign tests and domain summaries are secondary or exploratory.
No confirmatory claim is made from uncorrected multiple comparisons. The
predeclared alpha is 0.05.

## Success criteria

All of the following must hold:

1. the global DOHAA final-pass rate is higher than direct;
2. global paired wins exceed losses;
3. the primary exact sign-test p-value is below 0.05;
4. at least two of three models have a positive DOHAA-minus-direct delta;
5. no model has a negative DOHAA-minus-direct delta;
6. DOHAA records zero pass-to-fail regressions;
7. DOHAA averages at most 1.5 runtime calls per model-case;
8. DOHAA reports at most 1.5 times the direct-response tokens.

If token usage is absent for any preregistered runtime call, the token
criterion is reported as unevaluable rather than silently passed.

## Validation

Validate and hash the canonical declaration without contacting a model:

    hermes-dohaa validate-evaluation-protocol \
      examples/multimodel-evaluation-protocol.json

The validator is strict: unknown fields fail, the three conditions are fixed,
model and suite safeguards are mandatory, counts must reconcile, and numeric
bounds must be valid. Its SHA-256 is computed over canonical JSON, independent
of whitespace or object-key ordering.

Schema validation is also available through
[`schemas/evaluation-protocol.schema.json`](../../schemas/evaluation-protocol.schema.json).
The schema covers structural constraints; the CLI additionally enforces
cross-field invariants such as reconciled model and domain counts.

## Interpretation limits

Passing would support a bounded claim across the three frozen artifacts and
48 protected cases. It would not establish universal model independence,
open-ended quality, production safety, or superiority outside tasks with
deterministic verification. Failure must be published with the same protocol
and denominators; it must not be converted into an informal development run.
