# Current-document projection for development

The full delivered history can contain replaced revisions and repeated deliveries.
`tools.document_stream_projection.current_document_view` produces a disposable
snapshot containing one delivered event per currently active source revision.
It preserves the task, observation time and delivery order. Every active source
is retained, including irrelevant documents and untrusted content; no model,
reference answers or semantic relevance classifier participates in selection.

Selection first validates the complete original request. For each source it
finds the highest delivered revision, then applies its expiry/retraction state.
An expired or withdrawn latest revision therefore cannot revive an older value.
When the same revision has multiple equivalent deliveries, the first is retained.
Conflicting revisions or future events cannot disappear through filtering.

The returned receipt records canonical UTF-8 input/output hashes, serialized
byte counts and mutually exclusive removal counts. These measure request-body
bytes, not tokens, full wire payload, memory, inference speed or task quality.
`verify_current_view` recomputes selection and checks the complete output and
receipt. A hash receipt does not authenticate documents or certify factual truth.

Always recompute from the complete durable observation at each checkpoint.
**Never persist a projection as the delivery log or feed later deliveries into
it**: it omits withdrawal and expiry history needed to prevent resurrection.
The helper neither writes the log nor updates any runtime policy. It is not
enabled in native generation and does not alter frozen/closed experiments.

The tests cover current-source equivalence with the durable store and identical
reference scores for correct values, wrong values and stale citations. This is
an implementation invariant, not evidence that an LLM behaves identically with
less history. Removing history can remove useful context or change interpretation;
the synthetic stream's latest-revision model assumes a revision replaces its
predecessor, not that it is a patch requiring old content.

A fresh prospective experiment should compare full history and this projection,
keeping authority rules and capabilities equal for simple and DOHAA. Measure
workflow correctness, omissions, citation relevance and request/token/time cost,
including projection overhead. Any quality threshold and stopping criterion
must precede inference. This does not test semantic filtering, learned memory,
repair or architecture superiority.

Refs #79 and roadmap #72. Agent-generated development instrumentation; review
before merge. Rollback removes the standalone helper, tests and documentation;
there is no live runtime switch or installed source change to undo.
