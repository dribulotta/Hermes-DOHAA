# Isolated development prompt selection and reversal

`hermes_dohaa.learning.development` persists a choice between one verified
baseline prompt and one quarantined prompt candidate. A new private store starts
on the baseline. Adoption requires an independently reconstructed positive paired
collection and a current `recommend_for_development` review. Reversion closes the
store on the exact retained baseline bytes.

This is a library for an explicitly separate development consumer. It never
writes a Hermes profile, alters a deployed runtime, invokes an adapter/model,
executes candidate text, applies patches or performs real actions. A returned
selection is not permission for production execution. Candidates remain
quarantined, with `execution_attested=false` and `activation_authorized=false`.

## Use

1. Obtain a `CandidateSnapshot` with `load_artifact_snapshot`, using a candidate
   identity retained independently. Only bounded UTF-8 prompt candidates and
   baseline text are supported. Evidence and all snapshot hashes are rechecked.
2. Call `initialize_development_selection(snapshot, expected_candidate_id=...,
   output_dir=Path(...), reason_sha256=...)` in a **new** directory under a
   private operator-controlled parent outside runtime paths. Retain the returned
   `report()['head_sha256']` independently before subsequent operations.
3. Prepare the arguments for `audit_prompt_collection` over exact retained
   recording, plan, inputs, suite, policy and snapshot bytes. Retain the expected
   collection-report hash and the current review-chain head externally.
4. Call `adopt_development_prompt(directory, expected_head_sha256=...,
   audit_arguments=..., expected_collection_sha256=..., review_directory=...,
   expected_review_head_sha256=..., reason_sha256=...)`. It re-audits the recording
   without invoking the adapter, rejects a negative result or absent/rejected/
   revoked recommendation, checks stored prompt bytes and appends one adoption.
5. A dev consumer calls `read_development_selection` with the latest selection
   head. Reading a selected candidate also requires the same audit/review
   arguments and current review pin. Each read checks them again, so an appended
   revocation, missing review, altered recording or stale pin cannot silently
   return candidate bytes. The returned `.prompt` contains the retained bytes;
   `.report()` returns fresh metadata without prompt/evidence contents.
6. Call `revert_development_prompt(directory, expected_head_sha256=...,
   reason_sha256=...)` to return to the exact baseline. It does not require an
   intact candidate file, a positive review or an available collection recording.
   The state chain and retained baseline must still match their commitments.
   Preserve the new head; a reverted store cannot be adopted again. A distinct
   experiment requires a new store and independently justified evidence.

Reason hashes refer to independently retained operator intentions. They are not
signatures or proof of authorization. Public dataclass instances and metadata
are not trust tokens; callers must use verified bytes and trusted installations.

## Persistence and concurrency

POSIX storage uses mode 0700 directories and 0600 files owned by the effective
operator. Lookups are anchored to a directory descriptor. Symbolic links,
nonregular files, multiple hard links and changed reads are rejected. Only the
fixed baseline/candidate files, lock and three sequential state records are
allowed. Unexpected entries, gaps, truncation, changed identities, forged
authority flags and incorrect external heads fail closed.

A nonblocking file lock serializes cooperating readers and writers across
threads/processes. Complete files are synchronized before exclusive publication;
state records are appended and never replaced. Concurrent adoption attempts
cannot both succeed. A stale revision fails without overwriting a newer state.
This is application-level protection, not immutability against the file owner,
an administrator or an attacker controlling the parent directory/installation.

An I/O failure after publication may mean a transition became visible even
though the API raised. Do not blindly retry or infer that nothing happened.
Inspect the retained state externally and establish a new trusted head before
continuing. Partial stores or crash debris fail closed; the library does not
delete evidence or provide an automatic repair path.

The review chain and selection store are separate. The operator must serialize
review changes with adoption/consumption; there is no cross-store transaction or
global revocation monitor. Returned bytes are a point-in-time snapshot. A caller
must not cache them as permanent approval. Reversion changes this development
selection only and cannot retract a snapshot already returned to another caller.

## Validation and limits

Deterministic tests exercise actual private files, artifact loading, synthetic
paired collection, audit, review, adoption, candidate reads and exact-byte
reversion. They include a fresh consumer process, cross-process locking,
concurrent writers, revocation, stale/tampered state, nonregular files and
publication failures. They make zero real LLM requests and cannot demonstrate
useful learning. Positive fixtures test the mechanism; the completed negative
real prompt pilots remain negative and cannot be adopted on that evidence.

No production promotion, automatic live rollback, actuator or deployment is
implemented here. Any new real study must declare its aim, candidate, cases,
budgets and success criteria before generation; existing studies are not rerun
or rescored to obtain a favorable verdict.
