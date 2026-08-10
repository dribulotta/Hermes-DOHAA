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
- per-dimension verdicts for result specification, policy semantics, exact
  equality, action policy, and evidence;
- initial and final pass counts and rates;
- paired wins, losses, and ties between conditions;
- improvements and regressions;
- runtime calls and elapsed seconds;
- API token usage when Hermes reports an OpenAI-compatible `usage` object;
- DOHAA terminal state, attempt count, reason code, and ledger-chain verdict.

Runtime failures are outcomes, not silently discarded samples. A completed
experiment may therefore contain failed conditions while the command itself
returns successfully.

## Initial private pilot

The first meaningful pilot should be created outside the public repository and
frozen before execution:

1. prepare 10 unpublished evidence-synthesis cases;
2. prepare 10 unpublished policy-decision cases;
3. use exactly two attempts in every contract;
4. pin the model alias, model artifact, context, reasoning policy, timeout, and
   server configuration;
5. choose and record order and sampling seeds before viewing outputs;
6. choose the repetition count before viewing outputs;
7. execute all 20 paired cases in one result artifact;
8. preserve the suite, result, model identity, runtime version, and external
   SHA-256 hashes together;
9. report every case, repetition, failure, and regression.

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

A 20-case pilot is diagnostic rather than conclusive. Formal validation needs
larger protected sets, repeated runs, confidence intervals, blind human grading
for open-ended quality, and comparison against additional baselines. The JSON
result is not signed or hash-chained; archive it with trusted external hashes.
