# Durable host steps before model proposals

`HostStepStore` persists the trusted host's allocation before a model request.
It is a standalone development component on top of the synthetic tool simulator
and fixed `controlled-tool-proposal/1.0` parser. It does not load a model, send a
generation request, execute a tool, recover a pending effect or mark a workflow
successful. No native tool contract is registered by this change.

The host supplies immutable `HostStep` identities and scope. Allocation stores
the exact canonical envelope and its digest in a private SQLite database,
separate from `IntentJournal` and `ToolStore`. Repeating the same task/step and
envelope retrieves the original allocation. Changed permissions or operation
identities conflict. Only one active allocation is admitted per task, and a
request digest cannot be reused for another operation in this host store.

## Lifecycle

| State | Meaning | Permitted next transition |
| --- | --- | --- |
| `allocated` | Stable host identities and scope are durable. | Bind one request and policy, before dispatch. |
| `requesting` | A request is durably reserved; its completion is unresolved here. | Record evidence from the trusted terminal verifier. |
| `proposed` | A terminal generation produced a valid scoped operation. No effect is implied. | Observe a completed matching intent and tool receipt. |
| `abstained` | A verified terminal generation proposed no operation. | The host may allocate a genuinely new step; the workflow is not declared successful. |
| `invalid` | A verified terminal generation violated the fixed proposal contract or host scope. | Preserve the failure; only the host can allocate a new step. |
| `generation_failed` | The verified generation ended in a known budget/runtime failure. | Preserve the failure; only the host can allocate a new step. |
| `applied` / `rejected` | The separate tool and completed intent agree on this exact operation's outcome. | The host may allocate a new step. Rejection is not success. |

`begin_request` stores step/request/policy binding before returning. A repeated
call is rejected even with the same digest: it is not another send lease.
`request_binding`, `snapshot` and `host_step` are read-only recovery information,
not permission to resend. A crash after binding but before actual dispatch is
conservatively unresolved until the trusted evidence boundary resolves it.
An empty backend or a worker progress indication cannot release the step.

## Terminal evidence boundary

The constructor requires a host-configured `terminal_verifier` callback.
`record_terminal(operation_id)` invokes it with the persisted `RequestBinding`;
the public method does not accept raw caller-supplied receipts, model claims or
a `verified=true` argument. The callback must retrieve evidence previously
verified by the native transport and model-ownership boundary, including actual
request/policy/wire binding and terminal server completion. This component does
not replace those checks. A callback that simply wraps arbitrary bytes is not
a valid production integration.

The callback returns `TerminalEvidence(policy_sha256, receipt_bytes)` or no
evidence. The store independently checks exact policy/request binding, the fixed
terminal receipt shape and proposal semantics against the persisted host scope.
Duplicate JSON keys, nonterminal receipts and unsupported status/error fields
are rejected. Missing evidence, verifier exceptions and mismatches preserve the
requesting state. Concurrent conflicting terminal results cannot overwrite the
first recorded result. The store retains the terminal digest and valid proposal
bytes; the verifier must preserve the original private transport evidence so its
digest remains resolvable after a restart.

`TerminalEvidence` is a typed trusted-caller boundary, not a signature or proof
that arbitrary bytes came from a server. Native tool-wire verification is a
separate future increment. These tests use explicit verifier stubs and do not
certify a real native tool generation.

## Effects and recovery

New allocation, request admission and terminal proposal admission check for a
pending tool intention. An abstention cannot clear another pending intention.
A `proposed` step continues to block replacement even before a tool intention
exists. The host may explicitly pass its bound operation to the existing
`Executor`, whose quota, ownership, version and duplicate checks remain intact.

`reconcile_effect` only observes: the intention must already be complete, its
payload must equal the bound operation, and its receipt must agree with the
tool's stored receipt and exact request digest. Failed lookup, missing receipt
or a pending intention preserves the proposed state. If an effect committed
before acknowledgment, the host first uses `Executor.recover` under the existing
simulator guarantees and then asks the host store to observe completion.
This method never sends an effect or completes an intention itself.

All per-task actions must use the same trusted coordinator and stores. The
checks across three independent databases are not one transaction spanning a
model request or effect, and cannot prevent a writer that bypasses the host
coordinator from introducing an intention immediately after a check. The host
store's active-step barrier serializes cooperative allocations. No universal
exactly-once guarantee for external APIs or bypassing writers is claimed.

## Persistence and validation

Each transition and a linked event are committed in one SQLite transaction
with `synchronous=FULL`. Reads check the event chain, the current snapshot's
digest, canonical scope and database identity bindings. This detects accidental
state/journal edits or mismatched stores; it does not authenticate a database
against an attacker who can rewrite every row and hash. Parent directories and
callbacks must remain host controlled. POSIX files are created with mode0600;
an existing public file or a symlink is rejected. Windows confidentiality still
depends on the caller's directory ACL. Existing ACLs are not modified.

Nineteen regressions cover duplicate allocation, conflicting identity/scope,
request/receipt mismatch, edited state and metadata, failed evidence lookup,
pending effects, abstention, tool rejection and actual process termination at
five boundaries. Reopening after a committed effect and recovering the original
operation must yield one reservation, not two. These are process-crash checks,
not physical power-failure, storage-corruption or real-actuator tests.

Related: #93 under M4/#82, following #84 and #91. Agent-generated implementation;
maintainer review required. No LLM calls, installed-runtime changes, deployment,
schema migration or document-action widening. Rollback: omit this standalone
module and its tests/documentation; retain private development evidence as needed.
