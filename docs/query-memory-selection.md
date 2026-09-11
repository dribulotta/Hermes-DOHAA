# Query coverage under a fixed memory budget

This is a pre-generation component experiment, following #112/#114 and roadmap
M3. It changes which **whole active documents** reach the LLM. It does not change
the downstream DOHAA controller, train model weights or learn persistent memory.
Both selectors are available to the robust simple route as well as DOHAA; any
benefit is attributed to the selector, not claimed as architecturally exclusive.

The existing current-source projection keeps every active source. Its #81 pilot
had a paired regression despite lower serialized bytes, and remains negative
under its stated criterion. The #59 and #70/#71 controller comparisons remain
negative too. These new examples do not reopen, repair or rescore those studies.

## Two concrete algorithms

Both start by validating the complete document request and computing the current
view. Superseded, expired or retracted sources cannot return implicitly. Each
source revision replaces its predecessor; this does not support textual patches
that depend on old revisions. Rebuild from the full durable history at every
checkpoint; never append updates to a selected snapshot.

Query terms come only from the public requested fact keys. Tokenization applies
Unicode NFKC, case folding and word extraction with underscores as separators.
A document matches a key when its text contains every token of that key. Neither
source identifiers nor expected answers influence matches. Instructions remain
unchanged; they are not parsed to manufacture semantic authority or relevance.

- **coverage_per_byte:** repeatedly select the fitting document with the largest
  number of newly covered keys divided by its exact added serialized bytes.
  Compare ratios with rational arithmetic. Break ties by greater key gain, then
  recency and stable source/event identifiers. Stop when no new key can be covered.
- **per_key_recency:** cycle through the requested keys in their declared order,
  selecting one fitting newest matching source per key per round. Recency is
  arrival tick, then revision; stable identifiers break ties.

The candidate then uses the same per-key fill rule as the comparator. Both finish
with the same newest-first fallback for remaining documents, including unmatched
context when it fits. Oversized documents are skipped whole, never shortened.
The final output restores original delivery order, so the intervention changes
selection rather than introducing a separate prompt-order treatment.

Both inspect the same active text and metadata, receive the same public query
and byte cap, and use no model calls, embeddings, reference facts or classifier.
Repeated mentions of one key do not increase coverage. Different source texts
can nevertheless contain misleading keywords; lexical coverage is not truth or
sufficient evidence. Multi-document reasoning, synonyms and authority conflicts
remain real limitations. Neither algorithm is presumed to dominate.

## API and accounting

`select_memory(request, strategy=..., max_bytes=...)` returns a fresh selected
request and a receipt. `max_bytes` is 1–65,536 UTF-8 bytes for the entire canonical
document request, including unchanged task/header, event metadata, JSON escaping
and array separators. If the empty task header cannot fit, selection fails.
The fixed native prompt and transport envelope are outside this byte measure;
they must be equal and separately accounted for in a live comparison. Bytes are
not tokenizer counts, VRAM, wall time or a guarantee of model context fit.

The receipt binds full original input, active view, selected output, strategy,
budget and selector/dependency source identities. It reports selected events and
**lexically_covered_keys**, with `semantic_sufficiency_verified=false`.
`verify_selection` recomputes from the original observation and exact declared
strategy/cap. This is reproducible transformation accounting, not source
authentication, permission, persistence or an execution certificate.

The eight document helper/test files integrated here are unchanged copies from
reviewed #80, public commit `3566625be55f3266395ff094f72f2ccdff322222`. Each was
compared byte-for-byte with that public source before integration into the #113
development tree. Existing source/gate behavior and the one-proposal tool
contract are unchanged. Full project checks cover this combined tree.

## Development controls that can favor either algorithm

Run `python -m tools.run_query_memory_demo` from an installed development checkout.
The two cases are public, handcrafted instrument controls, never holdouts:

| Control | Candidate retains supporting keys | Simple retains supporting keys | What it demonstrates |
| --- | ---: | ---: | --- |
| One compact document supports both keys; newer separate documents consume the cap | 2/2 | 1/2 | Coverage can retain more useful evidence under a tight cap |
| An older compact keyword bait competes with a newer detailed authority | 0/2 | 2/2 | Lexical coverage can discard the only supporting evidence |

The second candidate output still contains both query keywords. The evaluator
deliberately scores **zero** supporting keys. Its reference groups are separate
from the selector call; they never become retrieval labels or expected answers
inside the model request. This result is not a live quality failure rate or a
statistical comparison. It demonstrates why a real controlled pilot is needed.

The prospective design is in `query-memory-pilot-v1.md`. No native cohort is
launched or model loaded by this increment. No default enablement, merge,
installed-runtime change, V5 publication or quality advantage is authorized by a
successful synthetic check.
