# Verified controller and simulator routes

`tools.controlled_tool_routes.ToolWorkflow` connects the actual `DohaaController`
to the synthetic inventory executor. The controller still decides whether a
proposal passes its gates. Only a separate, durable execution intention permits
the host to apply the operation. `SUCCEEDED` never means that an effect occurred
or that the inventory task met its independently scored objective.

This is integration and conformance code for #99. It adds no LLM calls, live
actuators, semantic repair, retry budget, or changes to document contracts. The
native worker continues to produce the fixed tool-proposal response with native
tools disabled. Previously closed model cohorts remain closed.

## Shared evidence and permissions

Construct the workflow with the existing exact `HostStepStore`,
`NativeToolEvidenceBridge`, and `Executor` objects. Their evidence, intent,
effect, and residency stores must have matching bindings. Each host store is
permanently bound to one route and one workflow directory. Compare routes using
separate equivalent simulator states and grants; never apply both to one store.

The host derives a one-attempt contract and allowed `simulator.reserve` and/or
`simulator.release` actions from the persisted step. The runtime rereads verified
native evidence and returns its unchanged host-bound proposal. Exact-proposal
and action-policy gates check that contract. No expected answer or final-state
scoring oracle is part of these gates. Independent executor authorization, quota,
stock, reservation, and version checks still determine the actual effect.

The `dohaa` route calls the actual controller and validates its real hash-chained
ledger, including a unique contract, one proposal, one gate evaluation and one
completion. Admission rejects a forged return value, extra attempts, any
deterministic semantic repair, and a passing proposal that differs from verified
evidence. A controller failure without this complete proof stays unresolved.

The `simple` route never calls `DohaaController`. It reads the same verified
proposal once, evaluates the same gates directly, and records distinct simple
events. Both routes share durable execution, permission checks, recovery and
effect observation. This intentionally strong baseline prevents attributing
common host protections to the DOHAA architecture.

## Lifecycle and recovery

Use `decide(operation_id)`, then explicitly `execute(operation_id)` for an
accepted operation. `snapshot` reports admission and effect separately; it
deliberately has no `task_success` field. A separate experiment must compare the
actual persisted final state with prospectively frozen task expectations.

| Durable state | Meaning and restart behavior |
| --- | --- |
| deciding | Decision intent exists. Recover only from a complete matching ledger run; otherwise remain unresolved without rerunning the controller or model. |
| accepted | Verified proposal admission only. Recovery does not execute it. |
| denied | Gates refused admission. No tool receipt is invented. The host step remains proposed and blocks replacement; explicit unresolved host handling is required. |
| abstained | Verified abstention admitted without an effect. It cannot clear another pending intent. |
| invalid / generation_failed | Native terminal exists but provides no admissible operation. No controller or effect execution. |
| executing | Execution intent committed. Recovery may finish only the original immutable simulator operation. |
| applied / rejected | Original completed intent and actual tool receipt agree with the host evidence and stored effect digest. These are effect outcomes, not final task scores. |

The simulator atomically commits an effect and its idempotency receipt. Recovery
may therefore retry its original operation safely. This guarantee does not
generalize to remote services without the same atomic contract. An effect made
outside the workflow before its execution intention cannot be adopted as credit.
Every final report rechecks the real receipt, including after the host step has
already reached its terminal state.

Private protected storage holds contracts, raw proposals, hashes, decisions, and
the actual ledger. A process lock excludes concurrent calls for one workflow.
Source and store identities are pinned; changing source requires a new study
and new stores, not reopening an old collection under different rules. This
assumes one cooperative trusted coordinator. Hash chains detect corruption but
cannot authenticate against a writer that controls and rewrites every protected
store, the verifier, or the running process. There is no cross-database global
transaction or claim of adversarial root isolation.

## Validation and scientific limits

The new tests use synthetic native transport, actual controller/gates/ledger,
actual SQLite effects, worker permission checks, and process termination at
decision and effect boundaries. Privileged Linux tests are required for that
boundary; ordinary CI also checks unchanged document behavior. These tests do
not demonstrate LLM quality, hardware performance, or incremental DOHAA benefit.

A future live study must preregister fresh multi-step cases, budgets, reasoning
conditions, equivalent initial states, equal recovery opportunities and actual
final-state metrics. Shared verified responses are paired evidence, not
independent LLM observations. Release or integration still requires maintainer
review of this agent-authored change and its parent draft dependencies.
