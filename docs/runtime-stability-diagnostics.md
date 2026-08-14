# Public runtime-stability diagnostic

The `public-runtime-stability-v1` suite is a reproducible development workload
for diagnosing model-server availability before freezing a protected
evaluation. It exercises transport, per-request deadlines, model isolation,
usage telemetry, and sustained sequential inference. It is public and must not
be presented as a benchmark or as evidence of generalization.

The suite is independently authored from public synthetic records. It does not
contain, reproduce, transform, or reveal any protected suite, input, expected
result, oracle, checkpoint, or model response.

## Fixed workload

The suite contains 16 unique cases in four rounds. Every round uses the same
interleaved domain order so that domain-specific latency can be separated from
late-run degradation:

1. `evidence_synthesis`;
2. `quantitative_reconciliation`;
3. `structured_extraction`;
4. `temporal_reasoning`.

Each domain has four cases. Every contract has a recursive `result_spec`,
contract-visible semantic assertions, two attempts, no allowed actions, and a
canonical serialized size between 2,400 and 4,500 bytes. The expected results
pass both visible deterministic gates before any runtime is contacted.

The companion manifest fixes:

- a 300-second timeout for each model request;
- one repetition for the smoke phase;
- three repetitions for the soak phase;
- exact case and domain order;
- the suite canonical SHA-256;
- expected request-count bounds.

With one model call from DOHAA, the smoke phase performs 64 requests per model
and the soak phase performs 192. If every DOHAA case consumes its second model
attempt, the corresponding maxima are 80 and 240 requests.

## Offline validation

Run both deterministic checks before inference:

```bash
PYTHONPATH=src python tools/build_runtime_stability_suite_v1.py
PYTHONPATH=src python tools/validate_runtime_stability_suite_v1.py
```

The builder runs in check mode unless `--write` is supplied. The validator
checks the manifest commitment, exact domain cycle, counts, bounded contract
sizes, visible gates, semantic assertions, identifier separation and objective
similarity against the existing public example, and absence of protected-suite
markers. Neither command performs network requests or model inference.

## Execution phases

Run each model in isolation, starting and ending with zero resident model
instances. Preserve results outside the repository with mode `0600`. Capture
the native model-server log for the complete interval, with prompts and model
responses redacted.

Smoke phase:

```bash
HERMES_API_KEY="$(cat /path/to/runtime-api.key)" \
hermes-dohaa evaluate examples/runtime-stability-suite-v1.json \
  --hermes-url http://192.0.2.106:8642/v1 \
  --hermes-model PINNED_MODEL_ALIAS \
  --model-artifact-id PINNED_MODEL_ARTIFACT \
  --reasoning-effort none \
  --temperature 0 \
  --top-p 1 \
  --sampling-seed 20260815 \
  --hermes-timeout-seconds 300 \
  --seed 20260815 \
  --repetitions 1 \
  --output /private/runtime-stability-smoke.json
```

Only after the smoke phase passes, repeat the same command with
`--repetitions 3`, a new non-existing private output path, and the same frozen
runtime settings.

## Acceptance gate

A model/configuration pair is operationally ready only when both phases show:

- every condition outcome completed;
- zero `response.timeout` failures;
- zero `response.connection_failed` failures;
- complete usage telemetry for every observed runtime call;
- exactly one expected model resident during execution;
- zero resident models before loading and after directed unloading;
- no server-side error, cancellation, or unexpected reload in the native log.

Do not add silent retries or discard failed outcomes. If the smoke phase fails,
do not proceed to the soak phase. If either phase fails, change one server
variable at a time and create a new configuration identity before repeating the
public diagnostic. A protected confirmation may be authored only after its
complete runtime configuration has passed this gate.

## Interpretation limits

This workload is intentionally visible and may be used repeatedly for
engineering. Its results can establish operational stability only for the
tested model artifacts, server configuration, timeout, and request volume. They
cannot establish answer-quality improvement, statistical significance, or
generalization to an unpublished holdout.
