# Preregistered native simulator canary collector

Agent-generated implementation for #97, stacked on draft #96. Explicit
maintainer review is required; this does not merge its parent, deploy a runtime,
enable native tools or activate real actuators.

`CanaryCollector` coordinates the verified native bridge, durable host steps and
the synthetic tool executor. It is Linux root-only infrastructure. It does not
choose a model, load one, create permissions, invent expected outcomes, or send
reference answers to the worker. A trusted preparation procedure must create
the simulator's initial inventory, grants and allocated steps before collection.

## Freeze before generation

The private manifest pins the collector source, native/collection policy hashes,
initial simulator state, exact ordered logical requests, existing immutable host
steps and expected final outcomes. One to sixteen distinct requests are allowed.
Each case declares the expected host status, reason, exact effect receipt code
(or null for abstention), complete inventory/reservation state and counts of
effect receipts/completed and pending intents. The integer minimum-correct
threshold is fixed before any send. Applied/rejected statuses require the exact
matching effect code; accepting a proposal never constitutes an executed effect.

The preparation procedure must publish reviewed finite manifest/source/protocol
hashes and the numerical acceptance criterion prospectively. This public
commitment is an operator workflow requirement, not authentication performed by
the collector through a network call. Keep cases, grants, reference outcomes,
network addresses and credentials private. Public unit-test fixtures are separate
from live cases and are not evidence of held-out generalization.

Create the native adapter on an empty reserved backend and load one exact owned
instance through `ModelResidency`, using the same fixed policy. Construct the
bridge, host and executor with their protected paths and set the host terminal
verifier to `bridge.verify`. Instantiate the collector with the already frozen
manifest and externally pinned manifest digest. Keep all per-task work in this
one trusted coordinator. `run_next()` performs one case at a time.

## Attempts, effects and recovery

A private FULL SQLite transaction records each attempt before the bridge can
dispatch. A nonblocking process lock prevents simultaneous operations through
the same collector directory. The bridge verifies the precise native wire and
durable model ownership. The collector then admits the host terminal, explicitly
executes or reconciles the simulator operation and observes its actual persisted
effect. The final verdict includes the actual inventory and reservation ownership
and the exact tool rejection reason; formatting alone is insufficient.

Only the process that created a new collector database may send its never-attempted
requests. An attempted case blocks later cases until its outcome is reconciled.
Reopening the collector is recovery-only: `recover_current()` may revalidate
native evidence and reconcile the original simulator intent, but never sends an
LLM request. A crash before dispatch is conservatively unresolved too. Remaining
unattempted cases after a collector restart stay uncollected; recovery does not
create a fresh send lease. Such a run cannot satisfy complete-cohort acceptance.

Simulator retries are confined to the existing executor's exact, durable operation
identity and atomic effect/receipt semantics. They do not justify replaying an
LLM request or retrying an external API. Digests detect inconsistent artifacts;
they do not authenticate against a root writer replacing all stores and manifests.

`finish()` requires every scheduled case complete, then unloads solely through
durable model ownership and verifies the backend is empty. Unknown generation or
unload is not guessed or repeated. Reopening after an already confirmed unload
never unloads again. The adapter's volatile unload loop is not used for recovery.
The trusted collector is finished and its adapter must not be reused for another
generation.

## Measurements and interpretation

The finite summary distinguishes attempts, verified terminals, missing terminals,
completed cases, correct outcomes, applied effects, abstentions, invalid proposals
and generation failures. Request time includes native transport and evidence
verification; process-crash recovery leaves that time unknown rather than inventing
a duration. The external trusted runner must separately retain startup/load timing.
Unload timing is recorded by the collector. Do not run heavy validations during
measured model inference, change budgets, hide failed requests or substitute models.

The overall criterion requires the full cohort, a verified terminal for every
request, the prospectively chosen correct count and exact owned unload. Report
individual task/outcome categories alongside the aggregate, including any intended
safe rejection. A successful compatibility canary does not establish model accuracy
on external tasks or a DOHAA advantage. A later simple/DOHAA comparison needs actual
implemented routes with equivalent permissions, executor, recovery and budgets;
shared proposals are not independent architecture results. The document evaluator
retains its empty-action contract.

Regression tests exercise actual synthetic effects, incorrect final states,
ordered cases, exact rejection codes, unknown completion, process crashes before
dispatch and after a committed effect, immutable plans, process locking and
single-instance cleanup. Privileged Linux testing is necessary; non-root CI skips
those tests. Rollback is to keep this draft branch unintegrated and not begin a
canary. Do not delete an incomplete collector's evidence to manufacture a new run.
