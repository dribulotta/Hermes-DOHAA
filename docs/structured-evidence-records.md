# Structured records and an explicit public query contract

`tools.evidence_records.resolve_records(request, query_contract)` resolves a
small declarative record domain without a model. It does not select memory,
infer authority from prose, implement general language understanding or verify
real-world truth. The existing document request format is unchanged; the query
contract is an additional caller-provided public input, not hidden evaluation
data. A future integration must expose that same contract to every comparator.

The contract has exactly `schema_version`, `allowed_sources` and `queries`:

```json
{
  "schema_version": "hermes-evidence-query/1.0",
  "allowed_sources": ["order", "directory"],
  "queries": [{"key": "route", "root_sources": ["order"]}]
}
```

There are at most 12 permitted source IDs. Query keys must exactly match the
document task's requested keys; every query has at least one permitted root
source. Caller authentication and source authenticity are outside this helper.
A document cannot add itself to the permitted sources or rewrite this contract.
Unpermitted sources are explicitly reported as excluded.

Each active permitted document must consist entirely of a JSON object with
exactly `schema_version: "hermes-evidence-records/1.0"` and a `records` array of
at most 32 records. Duplicate JSON fields, duplicate record IDs, unknown fields,
nonfinite values and invalid types make that source out of scope. Any such
active permitted source makes the whole resolution out of scope with no facts;
the helper does not silently discard it to remove a possible conflict. Historic
revisions are validated as deliveries, but only latest active text is parsed.

Every record has `id`, `key`, `kind`, `status` and the fields listed below.
Identifiers follow the existing document identifier rules. `status` is one of
`asserted`, `example` or `negated`; only explicit metadata is interpreted. A
negation inside an otherwise asserted string is not analyzed as natural language.
Values and table keys are nonempty strings of at most 512 characters.

| Kind | Additional fields | Meaning |
|---|---|---|
| `value` | `value` | An asserted literal string. |
| `table` | `entries` | A map of at most 64 exact string keys to string values. |
| `link` | `target` | Resolve one exact referenced record. |
| `lookup` | `input`, `table` | Resolve a string and a table, then use an exact map lookup. |

Each reference has exactly `source_id`, positive `revision` and `record_id`.
For example, an order document can contain a code value and a route lookup whose
input references that code and whose table references a directory record. A
resolved route requires both source revisions; a fragment is not sufficient.
There is no execution of expressions, interpolation, fuzzy lookup or implicit
conversion. References identify specific records, not inferred same-key entities.

The helper rebuilds the active view from the complete delivery history before
resolving anything. Expired/retracted latest revisions never revive old data.
Absent, unpermitted or wrong-revision dependencies, missing records/map entries,
type mismatches, cycles and chains beyond 16 edges remain unresolved. Cached
subgraphs retain their height so query order cannot bypass the depth bound.

For each requested key, all nominated root sources must be active. Every record
with that key in those roots is considered. A matching nonasserted root or any
unresolved alternative prevents an answer, including a mixed example/assertion.
Different resolved values produce a conflict; the helper does not pick the
newest, cheapest or shortest value. This is a deliberately conservative contract.
Conflict detection concerns competing query roots, not all records that happen
to share a label across unrelated entities. The contract and explicit references
define which records a query depends on.

When alternatives agree, the result retains every distinct complete evidence
group. The direct no-model answer uses the smallest source group, with stable
lexical tie breaking; this is citation choice, not budgeted memory selection.
Shared source/revision pairs appear once within each group. Partial resolution
keeps an explicit result for every unresolved key rather than removing it from
the task. Input, contract and active-view hashes bind the result; they do not
authenticate the caller or sources.

`complete: true` means all requested keys resolved under this declared grammar.
`semantic_truth_verified` is always false. A perfectly formed permitted record
can assert a false value, and an independent evaluator must still count it wrong.
Tests demonstrate that distinction with a reference outside the resolver.

This helper is the direct no-LLM control for this restricted domain. If it already
solves a proposed task with equal or better quality and cost, use rules rather
than manufacture a need for generation. A later memory selector must receive the
same extractor, public information, full-source requirements and complete groups
as its recency comparator. No selector, native campaign, live performance result
or controller-superiority claim is introduced here.
