# Bounded serial blocks with one owned model instance

`tools.native_model_block.record_block` collects one to eight native fixed-contract
requests using one model load, distinct native worker profiles, and one exact
owned unload at the end. It retains the native adapter, catalog identity check,
durable residency journal and wire/terminal verifier.
The previous one-call recorder is unchanged. This module does not dispatch a
campaign, choose cases, score answers, retry a failed generation or switch models.

Explicit policy 1.2 contracts are `document-stream-proposal/1.0`,
`shadow-boolean-answer/1.0` and `shadow-prompt-proposal/1.0`. The latter two use
the existing shadow envelope: `result.answer` is boolean, or `result.artifact`
and `result.rationale` are strings, with an empty top-level `actions` array.
For these two new contracts, the collection field map must match before loading.
An unknown contract or caller-supplied JSON schema is not accepted. Legacy native
policies do not acquire these contracts implicitly. Document schema and existing
collection declarations remain unchanged.

The schemas constrain output shape only. False answers and ineffective prompts
can still be valid JSON. Byte limits, feedback, candidate quarantine and semantic
evaluation remain in the existing learning modules. String schema lengths are
not used as a substitute for UTF-8 byte limits. A recorded prompt response is
not itself a candidate or permission to activate one.

All request bytes and IDs are checked for validity and uniqueness before loading.
Their order is fixed. Each verified completed or known budget/runtime terminal
is recorded before another generation may start. Optional token accounting runs
after cleanup, so an accounting error cannot strand a known completed model or
change which cases run. Missing or conflicting counters remain unmeasured.

An unknown generation stops the block without a replacement or blind unload.
An ambiguous load/unload is not repeated. An unrelated loaded model prevents
startup; a later residency change stops progress. The no-new-call margin limits
admission of another call; it is not a hard cancellation deadline for the server.
Unattempted requests, unresolved attempts and verified requests are separate.

Every generation uses the adapter's fresh profile and empty native conversation
history. The previous profile is sealed before proceeding, and its path and
worker index are retained in a private witness. This does not attest hidden
backend state or promise identical output between resident and separately loaded
runs. Only request strategy `none` is accepted by this initial document collector;
it does not attest the server-effective reasoning mode.

Load/unload time belongs to the block. Generation time and optional token use
belong to each request. Positions are labeled `first` and `subsequent`; they are
not measurements of a cold or warm prompt cache. A future experiment must balance
order and report block resources, shared-call dependence and quality. It must
not compare new resident-block timing directly with a prior per-call-load study
and attribute that difference to answer quality or controller superiority.

Synthetic tests use protected files and real residency storage with mocked
provider replies and worker startup. They check fresh profiles, durable terminal
history, one load/unload across multiple requests, retained known failures,
unknown completion, replay prevention, input/model identity and admission budget.
No live model call or native performance claim follows from these tests.
