# Governed learning loop

Hermes-DOHAA distinguishes bounded repair during a run from durable learning
across runs. Neither process permits the cognitive runtime to promote its own
output.

## Bounded repair is not persistent learning

The current controller can reject a proposal, return deterministic verifier
feedback, and request another proposal within a fixed attempt budget.

This is bounded repair:

1. the runtime proposes;
2. deterministic gates evaluate;
3. failed gate reasons become feedback;
4. the runtime may submit a revised proposal;
5. success, no progress, runtime failure, or budget exhaustion terminates the
   run.

The runtime does not modify controller code, policies, prompts, tests, memory,
or deployment configuration during this loop.

Persistent learning would change future behavior across runs. It therefore
requires a separate lifecycle and stronger authority boundaries.

## Structured repair feedback

A failed deterministic gate produces machine-readable feedback for the next
bounded proposal attempt. Each feedback item has this shape:

    {
      "gate": "required_evidence",
      "code": "evidence.required_missing",
      "reason": "Required evidence is missing: ['source-1']",
      "evidence_ids": ["source-1"]
    }

`code` is the stable value for automation. `reason` is explanatory text for
operators and models and must not be parsed as a control-plane identifier.
`evidence_ids` identifies evidence associated with the verdict when available.

Built-in gate failure codes are:

| Code | Meaning |
|---|---|
| `result.mismatch` | The proposal result differs from the expected value |
| `semantic.spec_invalid` | Contract-visible semantic assertions are invalid |
| `semantic.assertion_failed` | A visible deterministic relation did not hold |
| `semantic.evaluation_error` | A visible expression could not be evaluated safely |
| `action.forbidden` | The proposal requests an explicitly forbidden action |
| `action.not_allowlisted` | The proposal requests an undeclared action |
| `evidence.duplicate_id` | Evidence identifiers are not unique |
| `evidence.reference_missing` | A claim references unavailable evidence |
| `evidence.claim_unsupported` | A claim has no evidence reference |
| `evidence.required_missing` | Contract-required evidence is absent |

The controller records `failure_code` with each gate verdict and sends only
failed verdicts to the next proposal attempt. The retry event records the same
structured objects in the evidence ledger.

When the visible contract opts into `repair_policy.mode=rule_aware`, the
controller replaces ordinary failure prose with one bounded, value-free repair
directive. It includes rule IDs, one dependency-closed editable unit, atomic
groups, and visible contract source pointers. Independent failed units are
handled on later attempts. The prior proposal is supplied as an isolated deep
copy so the runtime can preserve unlisted fields; the controller verifies that
preservation against its own snapshot.

Rule-aware contracts require the runtime's optional scoped `repair` capability.
If it is absent, the controller fails closed instead of treating a fresh
`propose` call as a repair. Comparative evaluation classifies that outcome as
`runtime_failed`, so infrastructure/configuration failures cannot be counted as
ordinary completed losses.

The controller retains a candidate only when it resolves every failed rule in
the selected unit and introduces no new non-oracle failure. Oracle-only verdicts
are re-evaluated for terminal success but never influence intermediate
retention. Out-of-scope edits, immutable-field edits, incomplete repairs, and
regressions are recorded with fingerprints and pointers, then rolled back.
Repair events do not record proposal values.

Terminal controller outcomes also expose a stable `reason_code`:

| Code | Meaning |
|---|---|
| `run.succeeded` | All deterministic gates and approval requirements passed |
| `runtime.failed` | The cognitive runtime failed before producing a proposal |
| `repair.no_progress` | A previous proposal fingerprint was repeated |
| `repair.unsignaled_failure` | A failing gate supplied no complete authorized repair scope |
| `repair.runtime_unavailable` | The selected runtime does not implement scoped repair |
| `budget.exhausted` | The bounded attempt budget was consumed |
| `approval.required` | Deterministic gates passed but human approval is pending |
| `control_plane.identity_failed` | The controller could not identify its code or gate configuration |

The human-readable terminal `reason` remains descriptive. Automation should
branch on `reason_code`, never on the wording of `reason`.

## Resumable approval checkpoints

When every deterministic gate passes but the contract still requires human
approval, the controller records a versioned `run.checkpointed` event before
terminating with `approval.required`. The checkpoint and terminal event commit
as one SQLite transaction. The checkpoint contains:

- the original `run_id` and attempt number;
- the SHA-256 digest of the canonical task contract;
- the complete proposal and its fingerprint;
- the passing deterministic gate results;
- a manifest identifying the control-plane source and gate configuration;
- the terminal reason code that made the run eligible for resumption.

After an authorized operator approves the transition, resume the run against
the same ledger and exact task contract:

    hermes-dohaa run /path/to/task-contract.json \
      --ledger /path/to/evidence.sqlite3 \
      --resume-run-id RUN_ID \
      --human-approved

Before appending anything, the controller verifies the complete ledger hash
chain, the checkpoint schema, the proposal fingerprint, the terminal event,
the contract digest, and the control-plane manifest. The manifest hashes the
source of the controller, contracts, proposal types, gates, and ledger, plus
the concrete gate classes, order, and JSON configuration. A valid resume
preserves the original `run_id`, records `run.resumed`, and appends a new
`run.finished` event with `run.succeeded`. It does not contact Hermes or rerun
gates because it consumes the immutable proposal and deterministic verdicts
already bound into the verified checkpoint. The eligibility check and both
appended events execute in one immediate SQLite transaction so concurrent
resume attempts cannot both succeed.

The initial resume surface is deliberately narrow. Only the latest
`approval.required` terminal state is eligible. Runtime failures, exhausted
attempt budgets, repeated proposals, successful runs, missing checkpoints,
and a second resume all fail closed.

Resume failures expose stable codes:

| Code | Meaning |
|---|---|
| `resume.not_found` | The ledger or requested run does not exist |
| `resume.not_eligible` | The latest terminal state cannot be resumed |
| `resume.contract_mismatch` | The supplied contract differs from the checkpoint |
| `resume.approval_missing` | Explicit approval was not supplied |
| `resume.checkpoint_invalid` | The ledger or checkpoint failed validation |
| `resume.control_plane_mismatch` | Controller source or gate configuration changed |

Custom gates used in an approval checkpoint must expose inspectable Python
source and JSON-serializable state through dataclass fields or `__dict__`.
Otherwise checkpoint creation terminates with
`control_plane.identity_failed`.

Checkpoint schema `1.1` introduced this manifest. Earlier `1.0` checkpoints
cannot prove the active control-plane identity and therefore fail closed; the
original contract must be run again to produce a resumable checkpoint.

The manifest detects drift between checkpoint creation and resumption. It is
not a software signature, proof of code provenance, or substitute for a pinned
and reviewed deployment artifact. Controller source must remain immutable for
the lifetime of a process; restart the service after every approved update.

`--human-approved` is a control-plane assertion, not an identity provider or
approval workflow. Operators must restrict command execution and ledger write
access, and should record approver identity in an external authorization
system until a signed approval record is implemented.

## Quarantined candidate packages

The first persistent-learning primitive stores untrusted candidates outside the
active runtime. It supports only the `quarantined` state. It never executes an
artifact, applies a patch, edits a prompt, evaluates evidence or approves a
promotion. A successful storage or verification report is not an assurance
verdict about the proposed change.

Create a draft JSON document with exactly these fields:

```json
{
  "schema_version": "hermes-learning-draft/1.0",
  "kind": "regression_test",
  "baseline_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "artifact": "assert 1 + 1 == 2\n",
  "rationale": "Reproduce a synthetic arithmetic defect.",
  "evidence_sha256": ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
}
```

The example digests are placeholders. Replace them with SHA-256 digests of the
actual immutable baseline artifact and the relevant evidence artifacts. A Git
SHA-1 commit ID is not a baseline SHA-256 digest. The storage step binds the
supplied digests; an independent evaluator must later resolve their artifacts
and verify their provenance. Do not expose protected evidence to the generator.

Supported kinds are `code_patch`, `prompt` and `regression_test`; all artifacts
are inert text. Evidence contains one to 32 distinct lowercase SHA-256 digests.
Rationales are limited to 8,192 characters, and complete input and stored JSON
documents are limited to 2 MiB of UTF-8. Unknown fields, duplicate JSON keys,
unsupported versions and authority claims such as `approved` are rejected.

Use an existing trusted directory outside all active runtime, prompt, plugin
and policy paths. Restrict directory access so the cognitive runtime cannot
write candidates or replace files. For example, an operator on Linux can use:

```sh
mkdir -m 700 /path/to/private-quarantine
python -m hermes_dohaa.learning.quarantine freeze /path/to/private-draft.json \
  --output /path/to/private-quarantine/candidate.json
```

Store the returned `candidate_id` independently, for example in the reviewed
development issue or an external evidence record, without publishing the
candidate contents. Verify later using that retained ID:

```sh
python -m hermes_dohaa.learning.quarantine verify \
  /path/to/private-quarantine/candidate.json --candidate-id RETAINED_SHA256
```

The ID commits to the canonical candidate JSON, including its artifact,
baseline, rationale, ordered evidence list, version and quarantined state.
Verification requires the external ID; trusting only a digest embedded in a
rewritten candidate would allow the replacement to authenticate itself.
Verification is read-only and returns no artifact contents. CLI failures expose
stable `candidate.*` codes without raw file contents or operating-system errors.

Publication requires POSIX permissions and a local filesystem supporting hard
links and directory synchronization. A private temporary file is synchronized
before its final name is atomically claimed; the final file has mode `0600`.
Existing files and symlinks are never replaced, and concurrent writers cannot
both claim the same output. Native Windows publication fails before creating a
file; offline verification and draft validation are portable.

This is application-level immutability. A filesystem owner or administrator
can still modify or delete files, and a content hash is neither a signature nor
an authorization system. Independently retained IDs detect replacements;
trusted directory ownership and backups remain operator responsibilities.
An abrupt process kill before publication may leave a private `.candidate-*`
temporary file, which must never be treated as a published candidate. A storage
error after publication may leave a complete candidate; verify it against the
retained ID before retrying, and never overwrite it to hide the failure.

## Verify referenced artifacts before evaluation

The offline artifact checker resolves the quarantined candidate's baseline and
evidence digests to actual files. First, an authorized operator stages the
required artifacts in a private directory outside active runtime paths. Each
file must be named with its exact lowercase SHA-256 digest, without an extension
or subdirectory. Directory ownership and all ancestor directories must prevent
the cognitive runtime from replacing entries. Use ordinary local files in an
operator-controlled directory; do not use device files or a network filesystem.

Run the checker with the candidate ID retained independently at quarantine:

```sh
python -m hermes_dohaa.learning.artifacts /path/to/private-candidate.json \
  --candidate-id RETAINED_SHA256 --artifact-dir /path/to/private-artifacts
```

The checker verifies the candidate first, opens the artifact directory once,
and accesses only the digest-named files it references. It never accepts paths
or URLs from a candidate, scans directories, extracts archives, parses artifact
contents or executes proposed code. All files are opened read-only. A directory
descriptor anchors lookups; symbolic links, directories and other special file
types are rejected. Nonblocking file opens prevent a FIFO from hanging the
checker before its type can be inspected. This filesystem boundary requires
POSIX descriptor-relative operations; native Windows fails before opening files.

Each file is limited to 64 MiB and the combined unique artifacts to 256 MiB.
Hashing uses 64 KiB chunks and rechecks the byte budget while reading; a growing
file can consume at most one detection byte beyond the remaining budget before
the entire check fails. Repeated digests are read once, while every baseline or
evidence role remains represented in the report. Files missing from the store,
incorrect digests, observed in-place changes and entry replacements fail closed.
Metadata is checked before and after each read and all entries are checked
again before success is reported.

A successful `hermes-candidate-artifacts/1.0` report contains the candidate ID,
the unchanged `quarantined` state, each reference's role, expected SHA-256 and
verified size, plus unique-file and total-byte counts. It contains no paths,
artifact contents or computed digests for mismatched files. The CLI returns
nonzero with a safe `candidate.*` or `artifacts.*` code on failure. Keep any
retained report in the independent private evidence system; this command writes
only its JSON result to standard output and does not modify candidate files.

This is an **artifact-integrity-only** prerequisite. It proves that the bytes
read matched the candidate's committed references under the stated filesystem
assumptions. It does not establish provenance, truth of evidence, a test verdict,
approver identity or permission to activate the candidate. Metadata comparisons
detect observed races but do not create an atomic filesystem snapshot or prevent
later writes. An evaluator must reverify the artifacts it actually consumes,
or consume the verified in-memory bytes described below. Only authorized independent
evaluators may handle protected artifacts; do not expose them to the generator.

## Consume the verified bytes

An integrity report is metadata, not a durable handle to verified contents.
Opening the reported paths again would allow an intervening write to replace
what an evaluator consumes. Independent offline consumers can instead call
`load_artifact_snapshot` in `hermes_dohaa.learning.artifacts`:

```python
from hermes_dohaa.learning.artifacts import load_artifact_snapshot

snapshot = load_artifact_snapshot(
    candidate_path,
    expected_id=independently_retained_candidate_id,
    artifact_dir=operator_controlled_artifact_directory,
)
# Pass these bytes only to the separately authorized independent evaluator.
baseline_bytes = snapshot.baseline.content
evidence_bytes = tuple(item.content for item in snapshot.evidence)
candidate = snapshot.candidate
private_integrity_metadata = snapshot.report()
```

This API hashes and retains the same stream during a single read of each unique
artifact. It applies the checker's identity, POSIX file-type, mutation and byte
budget checks before returning any snapshot. The candidate is the immutable
payload checked against the externally retained ID. Baseline and evidence are
immutable `bytes`, with ordered evidence references stored in a tuple; a shared
digest uses the same retained object in both roles. No descriptors remain open
after the call. Later replacement or deletion of source files cannot change
these retained bytes. A later invocation checks the sources again and can fail.
Observed changes during reference capture still fail closed. This is not an
atomic filesystem snapshot: it commits to the individually verified contents,
not to every file having coexisted unchanged at a single instant.

Consumers must use the returned bytes and candidate, without reopening source
paths. Do not treat a previously emitted report as permission to reload content.
The existing CLI remains a streaming metadata-only checker. The new API retains
up to 256 MiB of unique artifacts plus the candidate, with transient buffering
of up to another 64 MiB while joining a file's chunks and Python object overhead.
These are content limits, not an operating-system RSS limit; an external process
boundary must enforce any evaluation memory budget. Do not serialize this private
snapshot into public reports, logs or generator prompts. Its default representation
and `hermes-candidate-snapshot/1.0` report omit candidate and artifact contents;
explicit access to the data necessarily exposes those bytes to the caller.

All three candidate kinds remain inert text and stay `quarantined`. A returned
object, its type or its report does not establish provenance, authorize code
execution, isolate an evaluator, prove learning, grant access to protected tests,
or permit promotion. Public data classes can be constructed by callers; they
are not attestations or trust tokens. Only this loader performs the stated
verification. An independent evaluator, restricted execution environment,
protected holdouts, paired shadow verdicts and promotion controls are subsequent
work. The runtime does not import this API or consume candidates automatically.

## Recorded paired shadow scoring

The [offline shadow evaluator](shadow-evaluation.md) now preregisters a candidate,
baseline, separate oracle suite, execution-policy digest, scorer identity and
criteria before scoring recorded paired outputs. It preserves failures, rejects
incomplete or incorrectly bound recordings and publishes positive or negative
results without overwriting prior evidence. It compares exact typed JSON with
zero candidate actions, runtime failures or paired regressions permitted.

This is a scoring primitive, not a candidate executor or live shadow router.
An independently audited collector must establish that the recorded executions
actually happened under the pinned policy. Results explicitly withhold execution
attestation and activation authority; every candidate remains quarantined.

## Candidate lifecycle

Beyond this initial quarantine primitive, a governed-learning subsystem still
needs the following lifecycle and transitions:

1. **Observed:** a failure or opportunity is recorded with reproducible
   evidence.
2. **Classified:** deterministic logic or an authorized reviewer assigns a
   failure class and affected scope.
3. **Candidate:** a model or developer proposes a change.
4. **Quarantined:** the candidate is stored outside the active runtime and
   receives no production authority.
5. **Evaluated:** automated tests, policy checks, adversarial cases, and
   protected holdouts run against the candidate.
6. **Shadowed:** when appropriate, the candidate processes representative
   traffic without controlling production outcomes.
7. **Reviewed:** an authorized human reviews evidence, scope, and residual
   risk.
8. **Promoted:** deterministic deployment machinery activates the approved,
   immutable candidate.
9. **Monitored:** post-promotion metrics and safety signals are compared with
   the approved baseline.
10. **Rolled back:** predefined triggers restore the last known-good version.

Skipping directly from candidate generation to promotion violates the
architecture.

## Authority separation

| Function | Permitted authority |
|---|---|
| Cognitive runtime | Propose a candidate and explain its rationale |
| Deterministic controller | Record state, enforce budgets, invoke checks, and reject |
| Assurance system | Produce independent test and policy verdicts |
| Evidence system | Persist inputs, outputs, hashes, versions, and decisions |
| Human authority | Approve promotions requiring judgment or elevated risk |
| Deployment mechanism | Activate only an explicitly approved immutable artifact |

The generator must not:

- edit or replace its verifier;
- select or reveal protected holdouts;
- change promotion thresholds;
- mark its own evidence as trusted;
- grant itself tools or network access;
- bypass required human approval;
- erase failed evaluations;
- promote mutable or unidentified artifacts.

## Evaluation requirements

A candidate evaluation should include:

- tests reproducing the original failure;
- existing regression tests;
- schema and policy checks;
- adversarial and malformed inputs;
- protected holdout cases unavailable to the generator;
- comparison against the current approved baseline;
- resource, latency, and attempt-budget measurements;
- evidence completeness and provenance checks;
- rollback verification.

A candidate should fail closed when an evaluator is unavailable or produces an
ambiguous result.

## Protected holdouts

Holdouts reduce the risk of a candidate optimizing only for visible tests.
They must be:

- inaccessible to the candidate generator;
- versioned and access controlled;
- evaluated by an independent process;
- rotated when exposure is suspected;
- excluded from model prompts and ordinary logs.

Passing a holdout is evidence for a defined evaluation, not proof of general
correctness.

## Promotion record

Every promotion decision should record:

- candidate identifier and content hash;
- parent baseline identifier;
- triggering failure or objective;
- tests and evaluator versions;
- complete verdicts and evidence references;
- policy and threshold versions;
- approving identity and authorization basis;
- deployment timestamp and target;
- rollback target and triggers.

Rejected candidates and negative results are part of the audit history and
must not be silently discarded.

## Rollback triggers

Predefined rollback conditions may include:

- deterministic gate regression;
- isolation or capability-policy violation;
- increased failure or escalation rate;
- unexpected action request;
- ledger verification failure;
- latency or resource budget violation;
- protected-holdout regression;
- operator-declared incident.

Rollback does not erase the promotion event. Both transitions remain in the
evidence history.

## Initial implementation sequence

A conservative implementation order is:

1. paired baseline and DOHAA quality evaluation;
2. structured failure classification;
3. immutable candidate storage;
4. regression-test generation as an untrusted proposal;
5. independent test execution;
6. protected evaluation sets;
7. human-reviewed promotion records;
8. shadow execution;
9. automated rollback triggers.

Until these controls exist, Hermes-DOHAA should describe its retry capability
as bounded repair rather than autonomous learning.
