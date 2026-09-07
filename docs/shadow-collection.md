# Paired prompt collection and development review

`hermes_dohaa.learning.collection` supplies a serial prompt collector and an
independent, read-only recording auditor. It invokes an operator-owned adapter
with the verified baseline and quarantined prompt candidate, persists dispatches
before calls, preserves responses/failures, and derives paired observations
through strict response admission. The existing shadow scorer supplies the
unchanged predeclared verdict.

This library does **not** yet include a production native-Hermes adapter. The
private experimental collector used for the completed pilots remains separate.
Before real model use, integrate and audit that adapter's wire observations,
isolated profiles, request budgets, terminal-completion detection and model
lifecycle. The existing `HermesApiRuntime` does not implement this interface.

## Preparation and invocation

Obtain a `CandidateSnapshot` from `load_artifact_snapshot`. Only `prompt`
candidates can enter collection; code patches and regression tests are rejected
before adapter calls. Choose a trusted adapter from an immutable installation.
Create a policy with `create_collection_policy(adapter_sha256=adapter_source_sha256(adapter),
runtime_policy_sha256=..., result_fields=...)`. The separate private runtime
policy must pin the model, reasoning mode, sampling seed, temperature, token and
deadline budgets, restricted profile, extra-request/tool blocking and unload
behavior. Its authorization and bytes stay with the operator.

Render each public task with `render_shadow_request`; commit the final message
hash in the oracle suite. Field declarations come from the public task, not its
answer. Prepare UTF-8 JSON with exactly `schema_version: hermes-shadow-inputs/1.0`
and `inputs`, a mapping from suite case IDs to exact rendered messages. All IDs
and input hashes must match. Make a shadow plan committing the exact collection
policy and oracle suite. Retain plan/policy hashes independently **before** either
arm runs. Source changes require a new plan; a hash alone does not prove timing.

Call `collect_prompt_shadow(snapshot, expected_candidate_id=..., plan_bytes=...,
expected_plan_sha256=..., suite_bytes=..., policy_bytes=..., inputs_bytes=...,
adapter=trusted_adapter, output_dir=Path(...))`. Use a new directory under an
existing private, operator-controlled POSIX parent outside runtime paths.
Neither its ancestry nor the installed source may be writable by the model or
another untrusted actor. The new directory uses mode 0700, files use 0600, and
publication synchronizes data and claims names exclusively. Native Windows
fails before creating the directory. Parent ownership is an operator assumption,
not an ACL/security audit performed by this library.

## Trusted adapter contract

The adapter exposes `runtime_policy_sha256`, `start()`, `generate(request_bytes)`
and `finish()`. All methods are synchronous and return bytes. `start` verifies
an idle backend without loaded models. `finish` unloads only the adapter's own
instances and verifies their absence and an idle backend. Both return exactly:

```json
{"schema_version":"hermes-shadow-lifecycle/1.0","idle":true,"models_unloaded":true}
```

Generation receives the exact JSON bytes recorded before dispatch. Fields are
`schema_version: hermes-shadow-request/1.0`, a unique `request_id`, `input`,
`prompt`, `input_sha256`, `prompt_sha256` and `execution_policy_sha256`. Use that
ID for a fresh isolated session. Requests contain no oracle answers, evidence,
case IDs, arm names, prior responses or feedback. The candidate is prompt text;
the collector never imports it or executes proposed code/actions.

A completed terminal receipt has exactly:

```json
{
  "schema_version": "hermes-shadow-terminal/1.0",
  "request_sha256": "SHA256_OF_THE_EXACT_REQUEST_BYTES",
  "server_finished": true,
  "status": "completed",
  "content": "the unmodified final response text"
}
```

A confirmed terminal failure replaces `content` with `error_code` and uses
`status: failed`. Allowed codes are `timeout`, `budget_exhausted`,
`invalid_response`, `runtime_error` and `cancelled`. A client timeout is terminal
only if the adapter independently establishes that server work finished.
Confirmed failures stay in the denominator. Shape errors stay failed without
removing fences or repairing values. Proposed actions are retained and counted.

The collector calls baseline then candidate on even case indices and reverses
order on odd indices. It dispatches one call at a time, exactly two per case,
with no retries. It cannot interrupt a hung Python adapter: deadlines,
extra-request blocking and actual backend concurrency must be enforced by the
adapter. Module hashes and the runtime-policy property detect declaration drift
under a trusted installation; they do not attest loaded code, dependencies,
network settings or hardware behavior.

Prompt, public input and final response text are each limited to 64 KiB of UTF-8.
Raw receipts are limited to 512 KiB including escaping; the full recording is
limited to 8 MiB with space reserved for abort metadata. These are serialized
data bounds, not process RSS limits. Invalid UTF-8/JSON receipts within the limit
are retained as base64. Oversized receipts cause an incomplete abort, never a
scored loss, and cannot be retained in full.

Exceptions, interruptions, invalid receipts, wrong request bindings and unproven
completion stop subsequent calls. Cleanup is **not** attempted while server
state is uncertain, since unloading could interrupt outstanding work. Resolve
that state externally before another run. A local failure while known idle
attempts final cleanup. Failed cleanup prevents a completed result. Successful
runs require an unload receipt at the end. This single-model collector does not
switch models inside a run.

## Evidence and independent audit

The directory retains plan, policy, public inputs and numbered hash-chained
events. Dispatches precede calls; terminal events capture raw receipts. These
files contain private prompts/responses. Base64 is not encryption; never publish
the recording or raw adapter exceptions.

Completion publishes `recording.json` and a safe `result.json` without overwrites.
Retain the recording SHA-256 and journal head independently.
`audit_prompt_collection` takes the same pinned inputs plus `recording_bytes` and
`expected_recording_sha256`. It checks the whole chain, manifest, lifecycle,
alternating requests and bindings, reconstructs observations from raw responses
and recomputes the original scorer. It makes no adapter calls or writes. Reports
omit prompts, answers, input text, case names, action text and raw errors.

Valid negative results remain completed studies. Incomplete/corrupt recordings
produce no scientific verdict. Interrupted directories cannot be resumed or
reused. A disk error may prevent an abort marker; a dispatch without its terminal
successor remains incomplete. Storage failure after publication may leave
complete artifacts: verify their bytes independently, preserve the failure and
never overwrite evidence to hide it.

Audit scope is `trusted-adapter-recording-consistency-only`. Lifecycle receipts
are trusted-adapter assertions. A fabricated, self-consistent transcript can
satisfy these checks; server execution requires independent wire evidence and
trusted infrastructure. Reports keep `execution_attested: false`,
`activation_authorized: false` and the quarantined state. Sharing a Python
process is not an OS sandbox: model execution must remain isolated from the
collector's oracle memory through trusted adapter infrastructure.

## Review and reversal

`record_collection_review` in `hermes_dohaa.learning.review` re-audits exact
collection bytes before appending a private disposition. Supply `audit_arguments`,
independently retained `expected_collection_sha256`, `expected_previous_sha256`,
`decision`, `reviewer_sha256` and `reason_sha256`. The collection digest hashes
the canonical audit report; the first previous digest is 64 zeroes. Reviewer and
reason hashes reference independently retained private authorization/review
records. They are not signatures or an identity provider. Restrict this API and
directory writes to authorized reviewers.

| Current state | Decision | Effect |
|---|---|---|
| Unreviewed, positive criteria | `recommend_for_development` | Recommend a separate development adoption review |
| Unreviewed, any completed verdict | `reject` | Retain evidence and end this review |
| Recommended | `revoke` | Withdraw the recommendation and preserve evidence |

Negative results cannot be recommended. Rejected/revoked decisions cannot be
reopened. Changed candidates, recordings, results or policies require a separately
pinned study and review. Each append requires the latest external head pin;
concurrent writers cannot both claim one sequence. Old heads and truncated or
extra review-chain entries fail. Retain returned heads outside the writable
directory to detect removal or replacement.

Revocation withdraws a development recommendation. It is not a runtime rollback:
this subsystem has no live activation path, and recommendations grant no
deployment permission. Removing this optional library leaves retained evidence
and the running runtime untouched. Native-adapter integration, authenticated
review, controlled adoption and deployment rollback remain separate work.
