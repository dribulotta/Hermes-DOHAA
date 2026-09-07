# Native prompt-shadow pilot: negative admission result

A single-model pilot tested a prompt candidate generated from four new synthetic
training cases against its baseline on twelve separate held-out ledger tasks.
It completed 29 native Hermes requests: four training generations, one candidate
proposal and 24 paired held-out generations. Native execution took 172.08 seconds.
No candidate code, live activation or actuator execution was involved.

The model was `qwen3.6-27b-mtp`, reasoning disabled, seed 9072907, temperature 0,
top-p 1 and at most 8,192 output tokens per request. Requests were serial, with a
fresh restricted AIAgent per generation. The model was unloaded after training
and after held-out collection, with an empty catalog verified at both boundaries.
The existing services and deployed revision were unchanged at the final check.

The candidate and its training evidence were quarantined before the held-out
plan was fixed. The plan required at least one paired improvement, 12/12 candidate
successes, zero paired regressions, zero candidate failures and zero proposed
actions. Neither retries nor case replacement was allowed. Old campaigns and
their protected cases were not reused or rescored.

| Metric | Baseline | Candidate |
|---|---:|---:|
| Held-out outcomes admitted and correct | 0/12 | 0/12 |
| Admission failures (`invalid_response`) | 12 | 12 |
| Recorded proposed actions | 0 | 0 |

There were zero paired improvements and zero paired regressions. The result is
`does_not_meet_predeclared_criteria`. All 25 recording, binding, native-profile,
request-budget, chronology, response-retention and cleanup checks passed. The
independent scorer reproduced the retained result. All 29 native generations
completed; admission failures must not be described as transport or model runtime
failures. Zero recorded actions is not proof about the contents of rejected
outputs; the independent native trace separately recorded no tool calls.

Read-only post-hoc shape inspection found all four training outputs and all
twelve baseline holdout outputs wrapped in Markdown fences, which the frozen
raw-JSON admission rule rejected. The twelve candidate holdout outputs were raw
JSON, but placed `actions` inside `result` rather than at the top level. The
candidate therefore changed formatting without producing an admitted output.

The task wording did not make field placement sufficiently explicit, and training
feedback retained only a generic admission failure. These are limitations of the
pilot's request/feedback boundary. Trace validity does not establish clarity of
the requested schema. This pilot does not demonstrate useful prompt adaptation,
persistent learning or generalization, and does not show that arithmetic itself
failed. No rejected response was unfenced, rearranged or rescored after observing
the outcome. The negative result and original artifacts remain intact.

The subsequent request formatter and structural feedback helper address those
deficiencies prospectively. Their tests use synthetic shapes, not a re-evaluation
of the held-out answers. Any new learning study requires a new candidate, new
cases and independently retained preregistration; it must not repeat this pilot
to seek a favorable result.

Scorer tested in this pilot: public tree
`87c684e8d74b0a95c7174637f587194904413c69` (initial PR #49), in a separate integration
with the candidate snapshot and other pending development corrections. The
private collector/auditor were fixed before the first request. An initial
zero-inference fixture failure was preserved and corrected before launch; the
final 21 harness tests and five actual-AIAgent synthetic-transport checks passed.
The candidate prompt, raw responses, task instances and oracle answers remain
private. Aggregate results are retained in the adjacent JSON record.
