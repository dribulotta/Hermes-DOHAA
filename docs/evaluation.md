# Comparative quality evaluation

Hermes-DOHAA must not claim that governance layers improve answer quality
without a controlled comparison. The comparative runner measures bounded,
exact-result tasks under three conditions while keeping the model and task
inputs fixed.

## Conditions

| Condition | Calls | Repair signal |
|---|---:|---|
| `direct` | 1 | None |
| `self_reflection` | 2 | The first proposal plus a generic request to review it |
| `dohaa` | At most 2 | Only deterministic failed-gate feedback |

All conditions use the same proposal schema and final deterministic gates. A
fresh opaque Hermes session isolates every case and condition. Their execution
order is shuffled per case using the recorded seed.

## Hidden oracle

Every evaluation case stores `expected_result` next to, but outside, its task
contract. The runtime factory receives only the contract and an opaque session
identifier. The direct and reflection conditions never receive the
oracle. DOHAA receives only a stable `result.mismatch` verdict when the result
is wrong, not the expected value itself.

Exact output vocabularies are not secret oracles. Cases declare a
contract-visible `inputs.result_spec` with required keys, JSON types, and enum
values. This prevents scoring a model against an identifier it could not have
known. Policy-decision cases additionally use deterministic gates that derive
the required decision and stable reason code from the supplied policy and
hypothetical request. Their field-specific feedback reveals no information
beyond those contract inputs.

The suite loader rejects a contract whose inputs contain an `expected_result`
field. Suite and contract authors must also review inputs for indirect leakage.

## Contract-visible semantic assertions

`result_spec` validates shape, types, and visible enums. A contract may also
declare `inputs.semantic_assertions` to validate deterministic relations between
the proposal result and ordinary visible inputs. The same gate is available to
normal `run` commands and comparative evaluations.

For example, this assertion requires the proposed available budget to equal the
visible total minus visible spent and committed amounts:

    {
      "assertion_id": "available-budget",
      "operator": "equals",
      "left": {
        "op": "ref",
        "source": "result",
        "pointer": "/available_budget"
      },
      "right": {
        "op": "subtract",
        "args": [
          {
            "op": "ref",
            "source": "inputs",
            "pointer": "/budget/total"
          },
          {
            "op": "add",
            "args": [
              {
                "op": "ref",
                "source": "inputs",
                "pointer": "/budget/spent"
              },
              {
                "op": "ref",
                "source": "inputs",
                "pointer": "/budget/committed"
              }
            ]
          }
        ]
      }
    }

References use RFC 6901 JSON Pointers and may read only `inputs` or `result`.
Input references cannot read the whole input object or the reserved
`result_spec`, `semantic_assertions`, and `expected_result` paths. The language
has no literal expression, arbitrary code, JSONPath, filesystem, network, or
runtime-call operator. Consequently, every compared value comes from data
already visible to the cognitive runtime or from its proposal.
Every assertion must contain at least one `result` reference so a tautology
between inputs cannot masquerade as proposal validation.

Supported assertion operators are equality, inequality, ordered numeric or
string comparisons, and array set equality. Expressions support:

- numeric `add`, `subtract`, `multiply`, `divide`, `abs`, and `round`;
- `length`, numeric `sum`, `min`, and `max`;
- bounded array `filter`, `project`, `sort_by`, `at`, and `unique`;
- timezone-aware `duration_minutes`, plus `add_days` and
  `add_business_days`.

Parsing is strict and bounded to 64 assertions, 512 expression nodes, depth 16,
64 variadic arguments, and collections of 10,000 items. At most 100 violations
are reported. Numeric operations reject absolute magnitudes greater than
10<sup>100</sup>. Invalid declarations are rejected before an evaluation
contacts the runtime. Evaluation errors fail closed.

The stable failure codes are `semantic.spec_invalid`,
`semantic.assertion_failed`, and `semantic.evaluation_error`. Diagnostics may
identify the assertion, operator, reference path, type class, and configured
bound, but never contain computed values or an oracle difference. The hidden
`ResultEqualsGate` remains a separate exact scorer and still reveals only
`result.mismatch`.

Suites and task contracts without `semantic_assertions` retain their prior gate
set and behavior. Adding assertions is a protocol change and requires a newly
frozen protected suite for independent confirmation.

### Deterministic semantic repair

After a proposal fails, the controller may correct a narrowly defined class of
contract-visible semantic equalities before spending another runtime call. A
repair is eligible only when one side of an `equals` assertion is a direct
`result` reference and the other side is an expression derived exclusively
from ordinary visible `inputs`. The controller evaluates that expression,
replaces only the existing referenced result leaf in a cloned proposal, and
runs every configured gate again. The candidate is accepted only if the entire
gate set passes; action, evidence, policy, exact-result, and human-approval
boundaries retain their authority.

The repair path rejects non-equality assertions, result-dependent expressions,
missing result leaves, overlapping pointers, conflicting values for one
pointer, invalid expressions, and evaluation errors. It never mutates the
runtime proposal, calls tools, performs actions, or reads `expected_result`.
The ledger preserves the original proposal fingerprint and records repair
events using assertion IDs and result pointers only, never computed values.

This mechanism is deterministic normalization, not model learning or general
reasoning. It can save a bounded retry when the contract already declares how
to compute the correct field. A protected suite used to design or inspect this
behavior becomes development evidence and must not be reused as fresh
confirmation after the implementation changes.

## Run an evaluation

The public example exercises the runner but is not a protected benchmark:

    HERMES_API_KEY="$(cat /path/to/runtime-api.key)" \
    hermes-dohaa evaluate examples/evaluation-suite.json \
      --hermes-url http://192.0.2.106:8642/v1 \
      --hermes-model dohaa-runtime \
      --model-artifact-id qwen3.6-27b-q6_k-PINNED_DIGEST \
      --reasoning-effort none \
      --temperature 0 \
      --top-p 1 \
      --sampling-seed 17 \
      --hermes-timeout-seconds 120 \
      --seed 20260810 \
      --repetitions 3 \
      --output /var/lib/hermes-dohaa/evaluation-20260810.json

The output path must not already exist. The runner creates the result with mode
`0600` and never overwrites an earlier evaluation.

`--model-artifact-id` is recorded evidence supplied by the operator; the
runner cannot prove that the model alias actually resolves to that artifact.
Verify and preserve the model-server configuration independently.

`--seed` controls randomized condition order. `--sampling-seed` is a recorded
base from which the runner deterministically derives one model seed for each
case and repetition; the three paired conditions share that trial seed.
`--temperature` and `--top-p` are also sent to the OpenAI-compatible model API.
Reproducibility still depends on the model server and backend. `--repetitions`
runs every case and condition between 1 and 100 times; the default is one.

## Result metrics

The result records:

- the suite SHA-256 digest and execution seed;
- the randomized order for every case;
- initial and final proposals for each condition;
- each deterministic gate verdict;
- per-dimension verdicts for result specification, visible semantic assertions,
  policy semantics, exact equality, action policy, and evidence;
- initial and final pass counts and rates;
- paired wins, losses, and ties between conditions;
- improvements and regressions;
- runtime calls and elapsed seconds;
- API token usage when Hermes reports an OpenAI-compatible `usage` object;
- DOHAA terminal state, attempt count, reason code, and ledger-chain verdict.

The result also contains `statistical_analysis`. Repetitions are nested within
their original case, so this section treats the unique case as the independent
unit. It reports strict case-level pass rates with Wilson 95% intervals and
paired exact two-sided sign tests. Trial counts remain useful operational
metrics, but must not be presented as independent sample size.

Runtime failures are outcomes, not silently discarded samples. A completed
experiment may therefore contain failed conditions while the command itself
returns successfully.

## Protected aggregate result

The [protected semantic holdout v3 aggregate report](evaluation-results/protected-semantic-holdout-v3.md)
documents a bounded confirmation after the deterministic repair implementation
was integrated. The protected suite and all individual results remain private;
the report contains aggregate evidence only and does not publish the suite.

This holdout must not be reused to guide subsequent changes and then presented
as independent confirmation of those changes. A new confirmation requires a
new independently frozen holdout.

## Multi-model preregistration

The public
[multi-model generalization protocol v1](evaluation-protocols/multimodel-generalization-v1.md)
fixes the model-selection sequence, new-suite shape, execution policy,
primary analysis, success criteria, and cost guardrails before a new protected
suite is authored. Validate its canonical JSON without contacting Hermes:

    hermes-dohaa validate-evaluation-protocol \
      examples/multimodel-evaluation-protocol.json

The protocol requires three model artifacts to be pinned before suite
authorship and forbids post-freeze substitution. The existing protected
holdout v3 is explicitly excluded. The executor enforces the frozen order and
computes the preregistered unique-case aggregate, but execution remains
unauthorized until its implementation commit and the exact model manifest are
frozen.

Adapt the sanitized manifest example outside the repository, then freeze it:

    hermes-dohaa freeze-model-manifest \
      /protected/model-manifest-draft.json \
      --protocol examples/multimodel-evaluation-protocol.json \
      --output /protected/model-manifest.json

After the manifest is frozen, author and freeze the new 48-case suite. Run the
complete experiment without policy overrides:

    HERMES_API_KEY="$(cat /path/to/runtime-api.key)" \
    hermes-dohaa evaluate-multimodel /protected/holdout.json \
      --suite-commitment /protected/holdout.commitment.json \
      --protocol examples/multimodel-evaluation-protocol.json \
      --model-manifest /protected/model-manifest.json \
      --hermes-url http://192.0.2.106:8642/v1 \
      --output /protected/multimodel-result.json

The executor validates all frozen identities and suite counts before any model
call. It creates one private non-overwriting artifact, retains runtime failures
as failed observations, averages paired pass indicators across models before
the global sign test, and evaluates every success criterion. Missing usage for
any direct or DOHAA call makes the token criterion `unevaluable`; an
unevaluable criterion cannot produce an overall pass.

### Isolated, resumable model slots

The monolithic `evaluate-multimodel` command remains compatible, but the
infrastructure must independently guarantee that only the selected model is
resident. Multiple resident artifacts can contaminate latency measurements
and cause paging when their combined working sets exceed RAM. An automatic
eviction setting is not a verifiable isolation barrier.

When the artifacts cannot safely be resident together, run one process per
slot and record the implementation commit that is executing it:

    hermes-dohaa evaluate-model-slot /private/holdout.json \
      --suite-commitment /private/holdout.commitment.json \
      --protocol examples/multimodel-evaluation-protocol.json \
      --model-manifest /private/model-manifest.json \
      --slot-id MODEL_SLOT \
      --execution-code-commit FULL_40_CHARACTER_GIT_SHA \
      --output /private/MODEL_SLOT.checkpoint.json

After every declared slot has completed, aggregate them offline in the exact
protocol order:

    hermes-dohaa aggregate-multimodel /private/holdout.json \
      /private/slot-1.checkpoint.json \
      /private/slot-2.checkpoint.json \
      /private/slot-3.checkpoint.json \
      --suite-commitment /private/holdout.commitment.json \
      --protocol examples/multimodel-evaluation-protocol.json \
      --model-manifest /private/model-manifest.json \
      --execution-code-commit FULL_40_CHARACTER_GIT_SHA \
      --output /private/multimodel-result.json

Both checkpoint and aggregate files are private, non-overwriting artifacts
created with mode `0600`; do not publish them. Aggregation verifies their
embedded identities and policies and performs no runtime calls. This execution
mode changes orchestration only: it preserves the frozen sampling, conditions,
repetitions, analysis, and acceptance logic.

## Freeze a protected pilot

Create the first meaningful pilot outside the public repository. It must have
30 to 50 unpublished cases, at least three domains, and at least five cases in
every domain. Do not place the suite, manifest, or results in Git.

After the evaluation implementation is merged, record that merge commit and
freeze the suite before viewing model outputs:

    hermes-dohaa freeze-suite /protected/holdout.json \
      --protocol-commit MERGED_IMPLEMENTATION_COMMIT \
      --output /protected/holdout.commitment.json

The command validates the suite, writes a private non-overwriting commitment,
and records:

- the exact suite SHA-256 digest;
- case and per-domain counts;
- the protocol implementation commit;
- a commitment identifier and freeze timestamp.

The timestamp is operator evidence, not a trusted timestamp. Publish or store
the commitment digest in an independent append-only or access-controlled
system before starting the evaluation if stronger proof of prior commitment is
required.

Run the pilot only with the matching commitment:

    hermes-dohaa evaluate /protected/holdout.json \
      --suite-commitment /protected/holdout.commitment.json \
      --output /protected/holdout-result.json \
      --hermes-url http://192.0.2.106:8642/v1 \
      --hermes-model dohaa-runtime \
      --model-artifact-id PINNED_MODEL_ARTIFACT \
      --reasoning-effort none \
      --temperature 0 \
      --top-p 1 \
      --sampling-seed 17 \
      --seed 20260810 \
      --repetitions 1

The evaluator rejects a suite whose ID, digest, case count, or domain counts no
longer match the frozen commitment.

## Protected pilot procedure

The operator should follow this sequence without tuning between steps:

1. prepare 30 to 50 unique unpublished cases across at least three domains;
2. keep expected results outside every runtime-visible task contract;
3. use exactly two attempts in every contract;
4. merge and freeze the evaluation implementation before authoring or scoring
   the holdout;
5. freeze and externally anchor the suite commitment before any model run;
6. pin the model alias, model artifact, context, reasoning policy, timeout, and
   server configuration;
7. choose and record order and sampling seeds before viewing outputs;
8. choose the repetition count before viewing outputs;
9. execute all paired cases in one result artifact;
10. preserve the suite, commitment, result, model identity, runtime version,
   and external
   SHA-256 hashes together;
11. report every case, repetition, failure, and regression;
12. analyze paired outcomes by unique case rather than treating repetitions as
    independent observations.

The public development suite is visible to models and developers and cannot be
used as evidence of generalization. Do not tune prompts, gates, or cases after
viewing pilot results and then report the same run as a holdout evaluation.

## Interpretation limits

This initial harness measures exact-result tasks for which an external
deterministic oracle exists. It can show whether structured feedback improves
final correctness under those conditions. It cannot establish that DOHAA
improves open-ended writing, research, creativity, or semantic truth when no
independent verifier is available.

DOHAA has an informational advantage only in receiving a trustworthy mismatch
signal. That is the architecture being tested: the model does not know the
answer, while the deterministic control plane can reject an incorrect result.
Results must not be generalized to tasks where no equivalent verifier exists.

A 30- to 50-case pilot remains diagnostic rather than conclusive. Statistical
tests quantify the observed protected sample; they do not prove generalization
to other tasks. Formal validation needs larger protected sets, independent
replication, blind human grading for open-ended quality, and comparison against
additional baselines. The JSON result is not signed or hash-chained; archive it
with trusted external hashes.

## Recursive result contracts and repair diagnostics

The legacy flat `result_spec` remains supported. A recursive contract uses
`spec_version: "2.0"` and JSON-shaped `object`, `array`, and scalar nodes. Object
nodes declare `properties`, `required`, and `additional_properties`; array nodes
declare `items`. Recursive diagnostics (including escaped JSON Pointer paths)
are derived exclusively from this visible contract. They are bounded to 100
reported violations and never contain `expected_result` or a difference against
it.

The evaluator separately computes an aggregate structural `oracle_distance`
after each response. This private scoring metric records only mismatch kinds and
counts; oracle data is never placed in verifier feedback or sent to the runtime.
`repair_transition` distinguishes complete repairs, partial improvements,
unchanged failures, and `worsened_failure`—the latter covers deterioration that
the compatibility boolean `regressed` cannot detect because both responses
failed. Per-domain statistics are explicitly exploratory: samples are small and
no multiple-comparison correction is applied.

Running a previously protected holdout with changed evaluator code is a
development/regression test, not a new independent confirmation. A new
confirmation requires a newly protected, independently frozen suite.

## Safe runtime failure diagnostics

Runtime failures remain failed outcomes and stay in every denominator. Outcomes
carry additive `error_type`, `error_code`, `error`, and `error_details` fields;
the summary reports deterministic counts by stable code, condition, domain, and
condition/code. Stable adapter codes are `response.timeout`,
`response.connection_failed`, `response.http_error`, `response.json_invalid`,
`response.shape_invalid`, `proposal.content_non_json`, and
`proposal.schema_invalid`. Automation must use these codes rather than messages.

Diagnostics may contain only safe metadata such as response length, SHA-256,
normalized media type, HTTP status, processing stage, and broad content class.
Raw responses, fragments, prompts, contracts, visible inputs, oracles,
credentials, sensitive headers, and sensitive URLs are never retained. Hashes
are calculated over received bytes without recording those bytes.

Parsing remains strict and fail-closed: pure JSON and the already-supported
whole fenced JSON form are accepted, while prose containing JSON is rejected.
There are no silent retries, repairs, or heuristic extraction. Any future
format retry must have an explicit budget, be recorded, and be applied equally
to every compared condition.
