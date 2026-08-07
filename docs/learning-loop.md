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
| `action.forbidden` | The proposal requests an explicitly forbidden action |
| `action.not_allowlisted` | The proposal requests an undeclared action |
| `evidence.duplicate_id` | Evidence identifiers are not unique |
| `evidence.reference_missing` | A claim references unavailable evidence |
| `evidence.claim_unsupported` | A claim has no evidence reference |
| `evidence.required_missing` | Contract-required evidence is absent |

The controller records `failure_code` with each gate verdict and sends only
failed verdicts to the next proposal attempt. The retry event records the same
structured objects in the evidence ledger.

Terminal controller outcomes also expose a stable `reason_code`:

| Code | Meaning |
|---|---|
| `run.succeeded` | All deterministic gates and approval requirements passed |
| `runtime.failed` | The cognitive runtime failed before producing a proposal |
| `repair.no_progress` | A previous proposal fingerprint was repeated |
| `budget.exhausted` | The bounded attempt budget was consumed |
| `approval.required` | Deterministic gates passed but human approval is pending |

The human-readable terminal `reason` remains descriptive. Automation should
branch on `reason_code`, never on the wording of `reason`.

## Candidate lifecycle

A future governed-learning subsystem should implement these states:

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

1. structured failure classification;
2. immutable candidate storage;
3. regression-test generation as an untrusted proposal;
4. independent test execution;
5. protected evaluation sets;
6. human-reviewed promotion records;
7. shadow execution;
8. automated rollback triggers.

Until these controls exist, Hermes-DOHAA should describe its retry capability
as bounded repair rather than autonomous learning.
