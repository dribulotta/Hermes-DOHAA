# Bounded prompt proposals from declared training data

`hermes_dohaa.learning.proposal` makes one prompt-proposal request through a
trusted adapter and binds an accepted response to a quarantined candidate.
It does not evaluate, adopt, promote or execute that candidate, change a runtime,
or infer that the proposal improved anything. The subsequent paired evaluation
and development-review lifecycle remains separate.

## Training boundary

The operator supplies independently authorized, audited training observations.
The strict `hermes-training-projection/1.0` object has only `schema_version`,
`baseline_sha256` and `records`. Each of one to sixteen records contains exactly:

- `input` and its UTF-8 `input_sha256`;
- `baseline_response` and its UTF-8 `response_sha256`;
- a nonempty unique list of `feedback_codes`.

Allowed codes are `result.correct`, `result.incorrect`,
`response.invalid_format`, `actions.proposed`, `runtime.timeout`,
`runtime.budget_exhausted`, `runtime.cancelled` and `runtime.failed`.
`result.correct` cannot be combined with other codes. An empty response requires
only runtime-failure codes. No freeform oracle reasons, expected-answer fields,
authority claims or arbitrary metadata are accepted. The module does not compute
these codes or establish that an observation came from an actual training run.

A separate `hermes-training-partition/1.0` object contains exactly
`schema_version`, `training_input_sha256` and `heldout_input_sha256`. Both hash
lists must be nonempty and unique. Training hashes must match all supplied
records, and exact overlap with the declared heldout inputs is rejected.
The generator receives only the baseline and projected input/response/code
triples, wrapped in the output contract. Neither partition declarations nor
source/evidence hashes are sent to it. Heldout inputs and answers are not API
arguments and must remain in a separate protected store.

These checks establish content/partition consistency, not provenance,
authorization or semantic isolation. They cannot recognize a protected answer
hidden inside a caller-labeled training string, semantic duplicates with
different bytes, inaccurate feedback, prior model exposure or an incomplete
heldout declaration. The operator must audit that boundary before planning.
Training and response strings remain untrusted text, never executable actions.

## Plan, one request and retained evidence

Create the adapter's wire policy with `create_collection_policy`, using its
source/runtime-policy hashes and result fields `artifact: string` and
`rationale: string`. `NativePromptAdapter` can consume this policy. Its wire and
receipt binding is reused; the separate proposal plan defines a single call,
not a paired evaluation schedule. Runtime model, parameters, token/time budget,
worker isolation and extra-request blocking remain enforced by the trusted
adapter and pinned runtime policy.

Call `create_prompt_proposal_plan(baseline_bytes=..., training_bytes=...,
split_bytes=..., wire_policy_bytes=...)`. Retain its exact canonical bytes and
SHA-256 independently before execution. The plan commits all input/policy/source
hashes, exact generator input/prompt, one request, serial concurrency, zero
retries, output limits and quarantine/no-authority flags. Changing the inputs,
source, budgets or flags requires a new plan; a hash alone does not prove timing.

Pass those inputs plus `plan_bytes`, `expected_plan_sha256`, `adapter` and a new
private POSIX `output_dir` to `propose_prompt_candidate`. It validates everything
before adapter startup, retains the input files and journals the exact request
before dispatch. The generator can return only the exact raw JSON envelope
containing `result.artifact`, `result.rationale` and an empty top-level `actions`
array. It cannot choose the candidate kind, baseline, evidence hashes, state,
approval flags or evaluation verdict.

Confirmed terminal runtime failures and malformed/fenced/extra-field outputs
stay rejected, without repair or retry. Verified terminal work is followed by
owned-instance unload through the adapter. Exceptions, unproven completion or
oversized/incomplete receipts abort without blind unload. A bad start/finish
cannot publish a successful candidate. Serialized input/output bounds are not
CPU/RSS quotas, and the synchronous caller cannot enforce a deadline against an
arbitrary hung adapter; only use independently validated trusted adapters.

After verified cleanup, a valid artifact is stored in `candidate.json` with
quarantined state and evidence commitments to the exact training projection and
complete proposal recording. An `artifacts` directory retains baseline,
projection and recording bytes for `load_artifact_snapshot`. No artifact is
executed. Partial storage failure reports abortion; retain its evidence and do
not blindly retry, resume the directory or treat a partial candidate as success.

## Independent reconstruction and limits

`audit_prompt_proposal` takes the same planned inputs, recording bytes and their
external hash, plus the exact stored candidate bytes (or `None` for rejection).
It performs no writes or adapter calls. It requires all five ordered hash-linked
events, valid start/finish receipts, the exact logical request and its terminal
receipt. It reconstructs the candidate and compares exact bytes and evidence
bindings; a rejected recording cannot be supplied with a candidate.

Successful reports say `candidate_recorded`; they never say that learning or
evaluation succeeded. Reports omit private text, deny execution attestation and
activation authority, and keep the candidate quarantined. A fabricated but
self-consistent recording is not proof of real server execution; native wire
conformance and immutable trusted installations are separate requirements.

Deterministic tests use new synthetic observations/receipts and real private
files. They cover partition leakage checks, output rejection, dispatch ordering,
failure/unload behavior, source/policy drift, byte bounds, tampered recordings,
candidate bindings, artifact loading and aborted publication. They make no real
LLM requests. Prior negative studies remain unchanged; any new real evaluation
requires a distinct justified protocol and fixed cases/budgets before generation.
