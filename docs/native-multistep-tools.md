# Fresh native multi-step route collection

`tools.native_multistep_tools.MultistepCollector` collects one bounded flow of
up to eight steps through a real `ToolWorkflow`. The workflow selects either
the actual DOHAA controller or the independent simple route. Use separate,
equivalent collectors/stores to compare routes and reasoning profiles. This
collector does not choose model settings or an experimental comparison for you.

Optional token usage is read from the exact verified native response, whether
JSON or complete SSE. Missing/null usage, malformed counters and conflicting
reports remain unmeasured (`None`); identical repeated SSE reports count once.
Counter values must be nonnegative integers (booleans are not counters). No
missing value is converted to zero. The complete framing and model identity
remain subject to native verification, including during recovery: optional
accounting does not authorize an incomplete or altered response, another model
request, a duplicate effect or a passing result for an invalid proposal.

This fixes the accounting failure identified in #103 after the first applied
operation of #101. The original frozen cohort remains incomplete; corrected
code must be validated and separately committed before any new live study.

The prospective private plan pins collector and route source, native and
collection policies, initial state, grants, task/step identities, public request
templates, continuation states, fault schedule and expected outcomes. Only a
pristine workflow and a ready owned model can start a new flow. A durable owner
binding prevents replacing the collector or plan for that workflow. The fixed
native policy supplies the model, reasoning mode and generation budgets.

Each step uses its frozen public instruction plus a fixed serialization of
current inventory and this task's reservations for permitted products. The
observation and exact derived request are committed before host allocation and
dispatch. That view excludes grants, private expected states and scoring rules.
These references affect scoring only: continuation depends on declared actual
route states, never whether an expected answer happened to match. Requests are
new generations, not reuse of prior closed cohorts or independent labels for
one shared response.

## Operation and recovery

Construct the collector with `plan_bytes`, `expected_sha256`, the exact existing
`workflow` and native `adapter`; call `run_next()` in the creating process. The
collector records an attempt before dispatch, the verified terminal before
controller evaluation, and decision timing before separate effect execution.
The native bridge, controller gates and executor retain their own integrity,
permissions and idempotency checks. It never treats proposal acceptance alone
as a completed task or adopts a common-executor result as a DOHAA invocation.

Only the actual creating process can generate. A fork cannot inherit this
permission. Reopening permits `recover_current()`, `summary()` and `finish()`:

- Recovery rereads verified native evidence and the original workflow state.
  It never starts a missing controller decision or executes a merely accepted
  proposal. A missing decision or execution intention stays unresolved.
- Only the workflow's already committed execution intention may recover its
  original atomic simulator effect. No replacement request or operation is
  invented, and no new generation can continue the suffix after reopening.
- A terminal result outside the plan's `continue_on` states blocks all later
  steps. Invalid answers, denials and generation failures can never authorize
  continuation. A wrong scored answer may continue if its actual state is
  allowed; this prevents reference answers from steering the run.
- Finalizing a reopened flow can mark untouched steps `restart_uncollected`.
  They remain in scheduled and missing-terminal denominators. The flow fails
  its completion criterion even if every attempted prefix step was correct.
- Unknown attempted completion prevents finalization and unload. Terminal
  abstention/failure with a blocked suffix can be finalized safely. Cleanup
  uses durable instance ownership; it never repeats an unload after closure.
- If native completion is proven but the route remains unresolved, explicit
  finalization may retain that step as `unresolved`, block its suffix and unload
  the model. It does not execute a merely accepted operation, recover an effect
  automatically, or count the step as completed. Recover the original workflow
  before finalizing if a recoverable effect should be included in this flow.

`finish()` verifies completed native evidence, actual workflow receipts and the
latest persisted simulator state before exact owned unload. Every planned step
must complete with its declared route state, effect code, host reason and full
inventory/reservation/receipt state for `flow_passed` to be true, and exact
unload must be verified and every declared fault must at least be armed.
Blocked or missing steps cannot disappear from the
denominator. Final task correctness is distinct from controller acceptance and
from an operation merely being applied.

## Declared process interruptions

Each step declares `fault_stage` as `none`, `execution_intent` or `tool_effect`.
The latter two deliberately terminate the collecting process with exit code
86 after, respectively, the workflow's durable execution intention or the
simulator's committed atomic effect/receipt. Run these cases in an isolated
child process whose exit can be observed. The fault is armed durably before
termination, is not delivered again on recovery, and does not change grants,
references, prompts or operation identity.

The collector reports armed faults only. That marker cannot prove a process
exit occurred; a live runner must separately retain the observed process exit.
For complete-flow recovery tests, placing a fault at the final step avoids
claiming that a restarted collector generated an untouched suffix. A fault at
an earlier step must retain that uncollected suffix as a flow failure.

## Measurements and limits

Generation, route/controller, execution, recovery and unload intervals are
separate milliseconds. An interval lost during a process exit is `null`, not
zero. Optional token usage comes only from the exact verified native response;
missing or malformed counters are unmeasured. The collector does not measure
model startup, cross-process end-to-end time or peak VRAM. Those fields remain
`null` until a separate prospective runner provides its own observations.

The implementation and regression suite use synthetic native transport, real
controller/gate/ledger and actual simulator effects. They do not establish LLM
quality or architecture benefit. Identical gates on an identical unmodified
proposal should produce identical admissions; a first live comparison can
measure operational correctness, recovery parity and orchestration overhead.
Independent model generations alone do not identify an architecture effect.

Before live collection, seal fresh cases, route/profile order, task denominators,
references, budgets, fault positions, external timing/exit observation and the
decision rule. Validate the collector and publish a prospective commitment
before generation. Preserve every failure and earlier negative study. This
agent-authored development module requires maintainer review and depends on
the parent draft stack; it makes no installed-runtime or document changes.

Private protected stores and process locks assume one trusted cooperative
coordinator. There is no global transaction across all stores, hostile-root
authentication, or exactly-once promise for external services without the
simulator's atomic effect/receipt contract.
