# Selecting whole decision dependencies under a byte budget

`tools.evidence_selection.select_evidence(request, query_contract, policy=...,
max_bytes=...)` is a pure development helper for the restricted structured-record
domain. It uses the direct no-model resolver from `tools.evidence_records`.
Both selection policies see the same full delivery history, task, permitted
sources and public query contract. Neither receives evaluation references.

A short citation is not enough to preserve a decision. Suppose two nominated
sources agree. The direct resolver may cite just one, but it read both to rule
out a conflict. For each **originally resolved** key, the selector therefore
requires all nominated root sources, including roots with no matching records,
plus the union of every agreeing root's complete dependency group. Retained
revisions are whole, exact and active; shared sources count once. Original
delivery order is restored in the selected request.

The helper resolves the full history first. A malformed active permitted source
makes the entire input out of scope, with no selected payload. Conflicting,
nonasserted, missing or otherwise unresolved alternatives never become candidate
keys through pruning. All task keys and the entire query contract remain in the
payload. The receipt preserves the original per-query status and reasons even
when removing sources changes a conflict into a missing-source abstention.
Before returning, resolution of the selected payload must reproduce precisely
the original groups for covered keys and produce no additional resolved keys.

Supported policies:

- `complete_coverage`: enumerate at most 4,096 subsets of at most 12 eligible
  active sources; maximize fully supported resolved keys, then minimize exact
  bytes, then prefer lexical source IDs. Partial groups get no credit. This is
  exact for this structural objective, not an optimizer of independent truth,
  task utility, LLM quality or latency.
- `complete_recency`: visit the same complete groups by latest completing
  delivery, then their oldest component, then lexical query key. Retain a group
  if the union fits; skip unaffordable groups and continue. This is an explicit
  deterministic recency comparator, not a claim that all possible recency
  heuristics were optimized.

The budget is 0 through 131,072 bytes of the exact UTF-8 canonical JSON envelope
`{"request": selected_request, "query_contract": public_contract}`. It includes
the unchanged task, source records, contract, separators and field names. If the
empty envelope exceeds the budget, status is `budget_exceeded` and `payload` is
null. No record or contract is truncated. Unpermitted sources are excluded by the
common resolver; permitted sources irrelevant to every resolved decision get no
packing priority. Inputs beyond the declared schema/source limits fail explicitly.

The bound applies only to `payload`. Other returned fields are an audit receipt
and include the full original resolution; they are **not** part of a bounded
model prompt. The algorithm still reads the original full input. This is not a
total process-memory bound, a tokenizer measurement or a native-model transport.
Rebuild from the complete original log for every new observation. Never use a
previously selected payload as the authoritative history. `verify_selection`
recomputes against that original and the caller-specified policy/budget; hashes
do not authenticate sources, caller identity or world truth.

Fresh synthetic controls cover alternative roots, empty roots, dependency
unions, shared sources, conflict preservation, malformed-source refusal, temporal
changes, exact byte boundaries, UTF-8, ties, tampering and an independently
recomputed small exhaustive optimum. A constructed packing example favors exact
coverage; with enough space both policies cover every key. Those examples test
the algorithms and do not constitute an empirical quality study.

The direct resolver already computes these admitted literal/link/lookup answers
without generation. Prefer that control for this domain. This change makes zero
model calls, does not integrate into Hermes runtime or change an existing memory
policy, and provides no evidence of controller superiority or general language
understanding. A future generative evaluation needs a separately justified task
and new independent references; do not treat deterministic packing wins as that
justification.
