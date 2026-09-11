# Query-memory pilot v1: reduced prospective design

Status: **design only; no cases or executable run have been frozen or dispatched**.
This is a fresh development pilot for #114, not a confirmatory generalization
study. Its selection algorithms are specified in `query-memory-selection.md`.
The previous source/repair and document-projection studies remain unchanged.

## Hypothesis and intervention

Greedy coverage per byte may preserve more complete, supported document-workflow
answers than the per-key recency comparator under the same 1,536-byte serialized
document-request cap. It may also regress when short text contains misleading
keywords. The intervention is the selected context **before native generation**.
The simple comparator gets the same source history, public instructions/query,
active-source reducer, byte cap, permissions and native resources.

No redundant DOHAA/simple controller arms: the downstream document citation gate
and one-proposal processing are held fixed. Results compare retrieval components.
An identical selected input requires only one new native call shared by the two
conditions, with that dependence recorded; it is not two independent observations.

## Planned cases and settings

- Eight new workflows: two instances in each family of compact multi-key evidence,
  keyword distractors/authority conflict, revision/retraction, and expiry with
  necessary multi-source evidence. Two checkpoints per workflow, before and after
  a new delivery or validity transition. These families are intentionally chosen
  development challenges, not a random sample of real-world tasks.
- Two selectors and two scheduled repetitions per checkpoint: at most 64 actual
  native calls. Repeated measurements and the two checkpoints are clustered within
  their original workflow; the sample size is eight workflows, not 64.
- Cases, independent evaluator-side reference values/evidence groups, both planned
  views and order are fixed before native calls. Check exact novelty against the
  already retained case identities without exposing or rescoring old answers.
  Reuse of task families is disclosed. Public examples are excluded.
- One fixed Qwen27 catalog identity under the previously validated native setup,
  subject to fresh readiness verification. Request strategy `none` for both arms,
  without claiming independently attested hidden reasoning-off. No `medium`
  intensity claim or silent model substitution. Keep #109's measurement limit.
- 4,096 output tokens, 8,192 configured context, temperature zero and the same
  seed/top-p/time limits for both arms, frozen from the actual supported policy
  before dispatch. The 1,536-byte input cap excludes the identical fixed native
  prompt/envelope; verify the complete request fits before generation. No mid-run
  budget increase, case replacement or retry until success.
- Client concurrency one. Use the established owned-instance lifecycle and exact
  unload after each known completed request; unload before any model switch.
  Preserve owned instance, source, request, wire and terminal commitments. Any
  unknown completion stops the cohort without blind resend or unload.
- Proposed wall ceiling three hours, no new request in the final 240 seconds;
  HTTP 180 seconds and worker 210 seconds. If actual supported policy differs,
  revise and freeze this design **before** inference rather than silently adapt it.

Before executing, prepare a balanced deterministic order over selector and
workflow within each repetition, record the seed/order, validate the recorder and
unknown-completion path with fresh synthetic responses, verify exact published
source and persist the final private executable protocol. These are prerequisites
for this specific pilot, not new generic validation infrastructure. No raw private
case, response or operational configuration should be published.

## Outcome and stopping interpretation

Primary unit: a workflow is stable for a selector only when both checkpoints in
both repetitions produce correct, fully supported reports under independently
retained references. Extra unsupported values, missed required facts, invalid
format and verified terminal token exhaustion count as failures. Public citation
shape checks alone do not establish task correctness. Report abstention failures
and wrongful acceptance separately, including unsafe or unrequested actions.

The predeclared exploratory continuation rule is at least **two additional stable
workflows**, **zero stable workflow regressions**, no increase in unsafe/incorrect
acceptance, no unresolved run, and no more than 10% additional observed completion
tokens against the comparator. If token usage is missing, the cost criterion is
unmeasured, not passed. A missing observation leaves the cohort incomplete;
descriptive partial results cannot be used to pass the rule. Report initial
evidence retention separately from actual LLM answer quality.

Report the eight paired workflow outcomes, gains/losses by family and the exact
paired sign-test result conditional on discordant workflows. Small-sample
uncertainty and selected synthetic families limit inference; two gains with no
losses do not by themselves establish statistical significance. Use no claim of
non-inferiority or general superiority from this pilot. Do not stop early when an
interim result looks favorable. Technical/time stopping preserves all outcomes.

Also report actual vs shared/counterfactual call counts, measured input/output
tokens, serialized bytes, projection time, generation time and total elapsed time
including every load/unload. Retain raw per-request measurements privately; small
samples do not establish stable tail latency. Zero-LLM evidence retention is an
instrument diagnostic, not an oracle supplied to the model. Deterministic direct
extraction controls should be reported separately where the case format allows it.

If the pilot fails, retain the result and reconsider or discard this selector.
If it passes, it only warrants a separately designed fresh confirmatory study
with broader independently reviewed tasks, a power/sample-size rationale and
fixed meaningful-effect thresholds. Neither outcome proves all DOHAA hypotheses,
useful persistent learning or V5 readiness.
