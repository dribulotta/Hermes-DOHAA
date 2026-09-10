# What the one-proposal route comparison can measure

The reviewed controlled-tool pipeline generates a native answer **before**
selecting its DOHAA or robust simple admission route. Its contract accepts one
verified proposal, without another native call or semantic repair. The proposal,
permissions, evidence/action gates, executor and recovery opportunities are
shared. The route-specific controller and ledger can affect downstream behavior.

| Stage | Shared input or mechanism | Route-specific intervention | Eligible outcome |
| --- | --- | --- | --- |
| Input and reasoning selection | Same information, policy and selector | None in this pipeline | No DOHAA attribution |
| Native generation | One completed and verified answer | None in this pipeline | Raw answer quality is already fixed |
| Admission | Same exact proposal and action/evidence gates | Actual DohaaController or simple gate loop; respective ledger proof | Acceptance, denial or abstention |
| Effect | Same durable host intent and simulator executor | Consequences of the preceding admission decision | Actual applied/rejected effect, never acceptance alone |
| Recovery | Same state/receipt verification and opportunity | Persisted route decision and ledger proof | Recovery, unresolved state, duplicate/prohibited effects |

Evidence for this source review is in `controlled_tool_routes.py`:
`_VerifiedRuntime.propose` permits one read and rejects feedback; `_material`
sets `max_attempts=1`; `decide` records/verifies the native terminal before the
route switch. The original integration issue #99 deliberately required this
boundary. This is a scoped interpretation of that pipeline, not proof that all
DOHAA mechanisms are equivalent or that a downstream controller cannot help.

## Offline protocol boundary

`tools.controlled_route_protocol.validate_protocol` accepts bounded committed
JSON bytes and an independently retained SHA-256. Its built-in reviewed profiles
bind fixed source files covering the route, controller/gates, host/evidence boundary
and simulator. The expected commitment is pinned, not computed from the current
checkout and then automatically accepted. A changed or missing audited file
requires a new review; a caller cannot supply a source root, alternate hash or
intervention label. Source bytes normalize CRLF to LF, matching project source
identity conventions.

Version `hermes-route-attribution-protocol/1.0` supports three explicitly selected
pipelines and study kind `fresh_synthetic_conformance`:

- `verified-tool-admission/1.0` preserves the original 19-file review commitment
  `f257124425ec1593a12b1764b5bad99591637d2964bb0a036892191c04378aec`.
- `verified-tool-admission/1.1` binds the reviewed composition with evidence
  policy fixes #46/#69, collection operations #47 and bounded repair #62. Its
  20-file commitment includes the new `assurance/evidence_policy.py` dependency:
  `2f3488dda8397ea7a414a1e187946929444278dda3beb974951472be682ebe8b`.
- `verified-tool-admission/1.2` binds the same 20 files after the reviewed
  [date and array-index boundary fixes](semantic-boundaries.md). Its commitment is
  `eefba9e31150c287ef278c2ec9fca75895236017b093edbe309d222132657c5e`.

The caller must select the profile matching its reviewed source. No automatic
upgrade, fallback, caller-supplied replacement hash or dynamically accepted
fingerprint exists. Old protocol bytes remain unchanged and do not pass against
the newly composed source under the old label. Unknown versions, mixed-source
compositions and missing/changed dependencies fail closed. A later source change
requires another review even if its unit tests pass.

The integration review checked the changed controller, gate, semantic operation
and identity paths. The tool route still uses one already verified proposal,
`max_attempts=1`, no repair-capable runtime and only exact-proposal/action gates.
It supplies no semantic assertions to trigger the new deterministic repair
sequence. Its durable proof rejects repaired or extra decisions. Consequently
the raw native answer remains fixed before the route switch under all profiles.
The evidence-policy module is now imported by gates and included in controller
identity, so the new profile must pin it even though this narrow route does not
opt into its claim gate. This review adds no new outcome or execution authority.

Allowed endpoints are `proposal_admission`, `effect_outcome` and
`recovery_outcome`. `native_answer_quality` is rejected because it precedes this
intervention; unknown pipelines and outcomes are rejected instead of inferred.

The two arms must be exactly DOHAA and simple, with equal commitments for input,
proposal, initial state, permissions, policy and interruption schedule, and equal
limits of one native proposal and 1–65,536 output tokens. Equal token limits are
planned caps, not proof of equal actual consumption or model support. Pairing
must declare an identical fresh synthetic proposal in independent state stores.
The test fixture in `test_controlled_route_protocol.py` shows the complete schema;
its placeholder commitments are examples, not real evidence or holdouts.

The API has no model transport or effect executor. For a file already frozen by
the experiment author, a CLI is available via
`python -m tools.controlled_route_protocol --sha256 DIGEST` with JSON on stdin.
Duplicate keys, non-finite JSON values, malformed commitments, extra fields,
unequal conditions and unsupported reasoning labels are rejected.

Successful validation reports **scope_validated**, not approval to run a study.
It does not authenticate a caller's case commitments, prove independent runtime
stores, attest loaded code, inspect external package/OS behavior, score a task,
prove an effect size or attest a hidden model mode. It is a reviewed static
source profile and a prospective constraint check. Actual execution must bind
those commitments and its measured results separately. Editing both this tool
and its source commitment remains a trusted code change requiring review.

## Fresh paired conformance controls

The new tests instantiate independent host, evidence, ledger and simulator
stores for each route. Each receives the same newly constructed synthetic
answer through a mocked native transport. These are neither replayed historical
terminals nor independent real LLM observations.

They check unchanged proposals across the route switch, actual controller use,
effect independence, abstention, substituted actions, corrupted native binding,
and process exit at six boundaries: decision intent, controller completion,
decision commit, execution intent, committed effect and observed effect. Recovery
must not ask for another proposal. A missing decision completion stays unresolved;
an existing effect remains singular. Acceptance is never counted as task success.

These checks are an instrument validation. Passing them does not show an
architectural advantage. Separate identical-profile LLM calls can vary; their
raw-answer differences cannot be attributed to this downstream switch.

## Next live study requires a different question or intervention

A study of memory selection or bounded repair must identify a mechanism that can
change its chosen endpoint before that endpoint is fixed. The strong simple
baseline receives equivalent information, permissions, opportunities, call/token
caps and the shared selector. Additional computation must be reported separately.
Do not add semantic repair to the existing one-proposal tool contract.

Before new live quality measurements, define a primary outcome and minimum useful
effect, fresh task families, case-level pairing, repetitions and order, uncertainty
analysis, stopping rules, unknown-completion handling, and full load/generation/
unload costs. Development examples are not confirmatory cases. No cohort is
launched here, and no completed evaluation is replayed or rescored.

Reasoning measurement retains #109's limitation. The strategy names are `none`
and `medium_default_on`; the latter explicitly depends on the model's declared
default. Neither reports an independently attested internal mode. #111's two
successful compatibility requests did not remove the server warning, establish
a medium intensity, or demonstrate quality superiority. Existing negative
results and shared-selector findings remain unchanged. V5 readiness and merge
decisions are outside this increment.
