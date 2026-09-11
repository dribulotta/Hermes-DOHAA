# Fixed proposals for simulated tools

`controlled-tool-proposal/1.0` is a standalone development contract for one
synthetic reservation, full release or abstention. A proposal is untrusted data.
`bind_tool_proposal` parses UTF-8 bytes and combines valid parameters with a
trusted `HostStep`. It returns an `Operation` or a no-operation abstention.
Parsing never calls an LLM, executes an effect, records an intention, reconciles
a pending operation or marks a task successful.

The host owns task, step and operation identities, allowed operation kinds,
allowed products, the per-step quantity ceiling and visible reservation
references. The envelope uses immutable tuples and frozen records. It must be
constructed from trusted host state, not model output or document instructions.
The caller must durably allocate each host step before requesting a proposal
and reuse its identities after a crash; this module does not persist the steps.
A different operation ID cannot bypass the simulator's task/step uniqueness.

The proposal has exactly these fields:

```json
{
  "schema_version": "controlled-tool-proposal/1.0",
  "decision": "reserve",
  "product": "synthetic-widget",
  "quantity": 4,
  "expected_version": 0,
  "reservation_id": null,
  "reason": "none"
}
```

For `reserve`, the reservation ID must be null. A `release` must match a host
visible reservation's ID, product and full quantity. Both require an allowed
product, positive integer quantity within the host ceiling, nonnegative integer
version and reason `none`. The simulator still checks current inventory,
aggregate task quota, authorization, ownership and version inside its effect
transaction. A visible reference is not proof of ownership or current state.

For `abstain`, all four operation parameter fields are null. Its reason is one
of `no_action`, `inventory_unavailable`, `insufficient_stock` or
`conflicting_sources`. That reason is a model claim, not a verified diagnosis.
An abstention cannot clear a pending intention, count as a completed effect or
silently declare a workflow successful.

The parser rejects duplicate and extra keys, wrong versions, unknown decisions,
numeric coercion, booleans in integer fields, nonfinite values, malformed UTF-8,
invalid JSON and input over 16 KiB. No field permits a tool name, shell command,
endpoint, database path, permission grant or model-selected operation identity.
`tool_response_format()` returns a fresh fixed JSON schema without case answers
or host authority. The parser additionally enforces branch semantics and host
scope; generation constrained by the schema is insufficient authorization.

Before a future native generation, the trusted host must reconcile an earlier
pending intention. The existing executor also rejects a replacement operation
while an intention is unresolved. For a bound operation, the host may explicitly
invoke `Executor.execute`; applied/rejected receipts and actual final inventory
must determine execution outcomes. The controller's current successful-proposal
status must not be interpreted as successful tool execution.

Fifteen regressions exercise the parser and its connection to the existing
simulator, including forged identity fields, scope violations, full-release
binding, independent tool ownership/quota/version checks, changed-content replay,
pending intentions and zero-effect abstention. The simulator's earlier process
crash tests remain applicable to execution; this module adds no persistent store.

This contract is not registered with the native adapter. Its document contract
continues to require empty actions. Actual wire validation, immutable host step
persistence, provider compatibility, isolated native integration and a fresh fair
comparison are follow-up work under #82. The simple baseline and DOHAA must get
the same permissions, tool executor and recovery guarantees. This increment
does not establish architectural advantage, learning or real-world exactly-once
effects. No installed runtime or real actuator changes.

Agent-generated implementation; maintainer review required. Rollback: omit the
standalone proposal module, regression file and this document. No migration or
runtime activation is performed.
