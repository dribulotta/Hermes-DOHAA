# Recorded paired shadow evaluation

The optional [prompt collector and development review](shadow-collection.md)
provide serial adapter invocations, private execution records, independent
recording reconstruction and revocable development recommendations. Native
Hermes adapter integration remains separate; the scorer's authority is unchanged.

The offline evaluator compares a quarantined candidate with its baseline using
a separately controlled oracle and predeclared criteria. It scores **recorded
outputs**, not candidate-supplied grades. It never executes candidate code,
applies a patch, invokes a model, reads live traffic or changes the runtime.
This is the scoring boundary for a future independently controlled shadow
runner, not evidence that such a runner or persistent learning already works.

## Inputs and separation of responsibilities

1. Retain the candidate ID independently and stage its baseline and evidence
   artifacts as described in [the learning loop](learning-loop.md). Evidence
   referenced by the candidate is training/trigger evidence; protected evaluation
   answers must remain separate and must never be supplied to its generator.
2. An independent evaluator authors a new suite and a fixed execution policy.
   Pin the suite's SHA-256 over its exact UTF-8 file bytes. The execution policy
   must define the collector, runtime identity, model, sampling and call/token
   budgets, per-arm isolation and scheduling, and model lifecycle controls.
   This scorer binds the policy digest; it does not execute or inspect that policy.
3. Create and publish the plan before collecting either arm. Independently retain
   the returned SHA-256 and the chronology in the operator's evidence system.
   The plan fixes the candidate, baseline, suite, execution policy, scorer and
   thresholds. A digest alone cannot prove when a file was created or that the
   generator did not see the answers.
4. A separately authorized collector runs the two arms against each logical case
   under that policy. Candidate code requires a restricted execution environment;
   a matching artifact hash is not permission to execute it. The collector must
   preserve failures and record complete paired outputs, not select successful
   samples or retry until success. It must never execute proposed actions.
5. Pin the complete recording's SHA-256 after collection and run the scorer.
   Keep plan, suite, recording, result and retained identities in independent
   private storage. A negative result is a completed evaluation, not a reason
   to overwrite or hide it. A future study needs a new preregistration.

The evaluator consumes the verified candidate snapshot and checks its actual
bytes again. Public snapshot data classes are not attestations. Original file
paths are never reopened during scoring. The filesystem CLI obtains the snapshot
with the POSIX loader; the pure scoring API is portable. All input files and their
directory ancestry must be controlled by the independent operator. Protected
answers and recorded private outputs must not enter candidate prompts or logs.

## Suite and recording formats

A `hermes-shadow-suite/1.0` document contains exactly `schema_version` and `cases`.
Each case has exactly these fields:

```json
{
  "case_id": "arithmetic-001",
  "input_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_result": {"answer": 42}
}
```

The digest is illustrative: use the SHA-256 of the logical case input actually
provided to both arms, excluding their different baseline/candidate artifacts.
The collector must verify that binding at execution. Case IDs use 1–64 letters,
digits, underscores, periods or hyphens, starting with a letter or digit. A suite
has 2–256 cases with distinct IDs and distinct input digests. This first version
does not accept repetitions or weight duplicated inputs as new independent cases.

A `hermes-shadow-observations/1.0` document contains exactly:

- `schema_version`;
- `plan_sha256`, `candidate_id`, `baseline_sha256`, `suite_sha256` and
  `execution_policy_sha256`, copied from the independently retained plan;
- `trials`, one entry per suite case, with exactly `case_id`, `input_sha256`,
  `baseline` and `candidate`.

Each arm is either a completed output:

```json
{"status": "completed", "result": {"answer": 42}, "actions": []}
```

or a failed generation:

```json
{"status": "failed", "error_code": "budget_exhausted"}
```

Allowed failure codes are `timeout`, `budget_exhausted`, `invalid_response`,
`runtime_error` and `cancelled`. Raw errors belong in restricted collector logs,
not this document. A failed generation cannot carry a result that bypasses its
failure. Actions are reported as a bounded list of strings and never executed;
their contents do not appear in results. A response that cannot be represented
in this format must be preserved as a collector failure, not silently discarded.

Every JSON document is limited to 8 MiB, 32 levels of nesting and 100,000 value
nodes. Duplicate keys, non-finite numbers (including exponent overflow), invalid
Unicode, unknown fields and unsupported versions fail closed. Case order in a
recording may differ, but the complete set must match and pairing uses case ID.
Missing, duplicated, extra or incorrectly bound cases prevent any utility verdict.

## Predeclared scoring

### Explicit collector response contract

Collectors can use `render_shadow_request(task_bytes, result_fields=...)` to make
the transport envelope explicit. It embeds the logical task and a structural
contract requiring raw JSON without Markdown, top-level sibling fields `result`
and `actions`, exact declared result fields, and an empty actions array. Hash the
**rendered message**, not the unwrapped task, as the case's `input_sha256`. Use the
same declaration for both arms and freeze it in the execution policy before
collection. Field declarations come from the public task specification and must
not contain oracle answers.

`result_fields` is a nonempty mapping of at most 64 bounded field names to `null`,
`boolean`, `integer`, `number`, `string`, `array` or `object`. The name `actions`
is reserved for the outer envelope. `number` denotes a finite float distinct
from `integer`. This first helper checks top-level result field types; element
constraints and correctness remain the suite/scorer's responsibility.

`admit_shadow_response(content, result_fields=...)` returns an `outcome` suitable
for a recording plus a separate `feedback` list of stable, value-free codes.
The collector can retain that feedback with **training** evidence so a candidate
generator learns why admission failed. Examples include
`shadow_response.markdown_fence`, `shadow_response.actions_not_top_level`,
`shadow_response.result_fields` and `shadow_response.result_type`. Invalid
responses remain failed outcomes with `error_code: invalid_response`. The helper
does not remove fences, move fields or repair a response. Nonempty proposed
actions remain in completed outcomes so the scorer can count and reject them;
their feedback contains only `shadow_response.actions_proposed`, never action text.

Do not feed held-out feedback back to the candidate generator, change its prompt
during the held-out run, or reinterpret a completed recording using a new
admission policy. Successful admission establishes shape only. In particular,
the negative [native prompt pilot](evaluation-results/prompt-shadow-pilot-20260907.md)
remains unchanged; its deficiencies motivated these prospective helpers.

### Utility criteria

The oracle is exact typed JSON equality: booleans, integers and floating-point
numbers are distinct; object key order is ignored and array order is significant.
All keys must match. There is no LLM judge, candidate-defined evaluator, implicit
tolerance, partial credit or semantic truth inference. Choose appropriate exact
representations when authoring a suite. A completed output is correct only when
it matches the expected result and proposes no actions.

Two positive integer thresholds must be declared, each at most the case count:
`min_improvements` and `min_candidate_correct`. Non-relaxable criteria require
zero paired regressions, zero candidate runtime failures and zero candidate
proposed actions. A paired improvement means baseline incorrect / candidate
correct; a regression means baseline correct / candidate incorrect. Failures
remain incorrect observations in the denominator, with separate failure counts.
A tie cannot establish an improvement, and aggregate gains cannot hide regressions.

The scorer reports `meets_predeclared_criteria` only when all five criteria pass.
Otherwise a valid complete recording yields `does_not_meet_predeclared_criteria`.
This is a descriptive result on the recorded cases, not statistical significance,
generalization, causal proof of learning, or an automatic promotion decision.

## Commands and retained records

Use an existing trusted private directory for new plan and result files:

```sh
python -m hermes_dohaa.learning.shadow plan \
  --candidate /private/candidate.json --candidate-id RETAINED_CANDIDATE_ID \
  --artifact-dir /private/artifacts --suite /private/new-suite.json \
  --suite-sha256 RETAINED_SUITE_SHA256 \
  --execution-policy-sha256 RETAINED_POLICY_SHA256 \
  --min-improvements 1 --min-candidate-correct 20 --output /private/plan.json
```

Choose thresholds for the actual suite before collecting outputs; the illustrative
threshold 20 requires at least 20 cases. Retain the printed `sha256` independently.
After the separate collector finishes and its recording is pinned:

```sh
python -m hermes_dohaa.learning.shadow score \
  --candidate /private/candidate.json --candidate-id RETAINED_CANDIDATE_ID \
  --artifact-dir /private/artifacts --suite /private/new-suite.json \
  --plan /private/plan.json --plan-sha256 RETAINED_PLAN_SHA256 \
  --observations /private/observations.json \
  --observations-sha256 RETAINED_OBSERVATIONS_SHA256 --output /private/result.json
```

Plan/result publication requires POSIX, mode `0600`, synchronized writes and an
atomic no-overwrite claim. Existing files and symlinks are never replaced;
concurrent publishers have one winner. A crash may leave a private temporary
file or a completed output after a late storage error; do not treat a temporary
file as published or overwrite a completed result. File ownership, backups,
permissions and trusted directory ancestry remain operator responsibilities.

Exit codes: **0** for a published plan or a result meeting the criteria; **2** for
a published negative result; **1** for invalid inputs or storage failure. CLI
failures use stable codes without raw contents, paths or operating-system errors.
The JSON API raises an error for invalid inputs and returns both valid verdicts.

The `hermes-shadow-result/1.0` record binds all input hashes and the scorer source
fingerprint. It includes aggregate and paired counts, predeclared thresholds,
individual criterion checks and per-case correctness/failure/action counts in
suite order. It omits case names, expected values, outputs, prompts and paths.
`result_id` hashes canonical result JSON excluding that ID; the CLI additionally
returns the SHA-256 of the exact published file, including its final newline.
These two digests serve different purposes and must not be interchanged.

The scorer fingerprint covers `shadow.py`, `artifacts.py` and `quarantine.py`
with line endings normalized for equivalent Git checkouts. Freeze that trusted
installation for the lifetime of the process. The fingerprint is not a signature,
complete environment attestation or protection against a hostile interpreter.

Every result retains `candidate_state: quarantined`, `activation_authorized:
false` and `execution_attested: false`. A consistent recording could still be
fabricated by its author. The independent collector and its audit must establish
that both arms actually ran under the pinned policy and were isolated from the
oracle. Live shadow routing, promotion, rollback and actuation remain separate
unimplemented boundaries.
