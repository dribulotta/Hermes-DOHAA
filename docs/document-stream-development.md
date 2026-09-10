# Continuous-document development harness

Issue [#72](https://github.com/dribulotta/Hermes-DOHAA/issues/72) starts the
[innovation roadmap](innovation-roadmap.md). This first increment supplies a
small, offline simulator and reference-side grader. It does **not** implement
the native agent comparison, autonomous learning, filtering gains or actuators.
The public examples are development fixtures, never a protected test set.

## Reproduce

From the repository root, with the root and `src` on `PYTHONPATH`:

```sh
python -m unittest discover -s tests -p 'test_document_stream*.py' -v
python -m tools.run_document_stream_demo --output .dohaa/document-stream-demo-1
```

The output directory must be new. It contains a synthetic SQLite database and
`summary.json`. Six manually specified reference controls should pass, two
deliberately stale/expired reports should fail, and reopening the database must
preserve the observation. These are checks of the measuring instrument, **not
agent accuracy results**. `native_model_calls` is zero.

## Stream semantics

`DocumentEvent` carries a stable delivery ID, source ID, positive source revision,
arrival tick, document text, optional expiry tick and retraction flag. Ticks are
simulated integers, not trusted real-world timestamps. Revision authority is an
assumption of this controlled world; a future external source needs an explicit
authentication and conflict policy.

- A replay of the same delivery ID and bytes inserts nothing. A different envelope
  may redeliver the same source revision and content, retaining that arrival.
- Reusing a delivery ID or source revision with different content rejects the
  entire batch. Contradictions between **different sources** remain valid input;
  this helper does not decide which source is true.
- The greatest delivered revision of a source controls its state. A later arrival
  of an older revision cannot replace it. New arrivals cannot backdate history.
- A document expires at `at_tick >= expires_at`. Expiry or retraction never
  resurrects an older revision. A genuinely newer revision may become active.
- Earlier observations exclude later deliveries. `observation()` contains only
  public documents and metadata, never expected facts or scores.

`StreamStore` binds a database to one run ID and schema. A batch transaction
records up to 256 arrivals, within a 10,000-record development run. A text is
bounded to 64 KiB of UTF-8. The store is append-only through this API, not a signed
ledger protected against local file edits. Keep its file and parent directory
under the experiment owner's control.

Process-death tests cover both an interrupted transaction and death after commit
before acknowledgement. They demonstrate local delivery durability/idempotent
replay under those faults. They do not prove power-loss durability on arbitrary
hardware, exactly-once external actions or recoverability of an unknown model
completion. The transaction must never contain model calls or network effects.

## Independent scoring boundary

`ReferenceFact` belongs to the evaluator. It specifies an exact structured value,
one or more acceptable sets of source/revision citations, and whether omission is
critical. Reference annotations must be prepared and reviewed independently of
the candidate agent. This code enforces their shape and current availability;
it does not establish their semantic truth or annotation independence.

Reports have the shape:

```json
{"facts": [{"key": "delivery_day", "value": "Friday", "evidence": [{"source_id": "notice", "revision": 2}]}]}
```

Task success requires every reference with its correct value and permitted current
evidence, with no extra facts. A current but unrelated citation is insufficient.
Malformed reports, duplicate fact keys and duplicate citations fail. Abstention
misses all required facts. An empty report succeeds only when the independently
declared reference set is empty.

The grader returns counts of correct/missing/critical-missing facts, wrong values,
extra facts, unsupported facts and stale/unknown evidence. Error categories overlap.
Do not sum them as disjoint errors. Exact structured equality is not a general
natural-language semantic judge. No assertion about long-form synthesis quality
is supported by these fixtures.

## Next native development slice

Implement separate model-facing and evaluator processes with a versioned request
contract. A model worker receives only the authorized observation, task and its
own declared memory. It cannot read reference objects, future events or another
arm's state. The Python module split alone is not process isolation.

Compare direct Hermes, a strong simple pipeline, and DOHAA with the same initial
information, available tools, temporal rules, evidence checks, memory allowance,
retry limit and per-task resource caps. If `active_sources()` is exposed as a
helper, grant it equally to the relevant arms; do not credit its deterministic
answering ability exclusively to DOHAA. Evaluate filtering policy separately.

Use new public **development** scenarios first to check native response structure,
token requirements, timing and exact model unloading. Declare this calibration
before any call. It is not the confirmatory comparison. Preserve terminal failures
and unknown completions; no blind replacement or unload after an unknown request.
Freeze fresh confirmatory cases and criteria only after calibration is complete.

No closed experiment, private case corpus, installed runtime or live service is
read or modified by this harness. Removing these new tools/tests/docs rolls back
this increment without changing the production controller.
