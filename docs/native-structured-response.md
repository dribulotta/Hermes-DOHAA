# Explicit structured output for the native document workflow

Issue [#76](https://github.com/dribulotta/Hermes-DOHAA/issues/76) follows the closed
#74 calibration: three of four responses ended with incomplete JSON despite a
normal terminal stop. Those outcomes remain unchanged. This change constrains
new generations; it never repairs or reinterprets an old response.

[LM Studio's structured-output documentation](https://lmstudio.ai/docs/developer/openai-compat/structured-output)
describes `response_format` with a JSON schema on `/v1/chat/completions`. Support
and model behavior must still be verified on the actual backend. No result of
that documentation is evidence of Hermes-DOHAA performance.

## Policy boundary

Legacy `hermes-native-shadow-policy/1.0` retains its request behavior and rejects
any undeclared `response_format`. The new explicit policy version
`hermes-native-shadow-policy/1.1` requires:

```json
{"response_contract": "document-stream-proposal/1.0"}
```

This field supplements the existing required identity, endpoint, sampling,
budget and isolation fields. It selects exactly one code-owned schema. Arbitrary
caller schemas, reference constants, schema URLs and unknown contract identifiers
are not admitted. The wire guard requires the exact full schema, including a
boolean `strict: true`; missing, substituted or weakened schemas fail before
dispatch. The worker sends the format through Hermes' request overrides alongside
the existing seed, temperature and top_p controls.

`native_response_format.py` returns fresh schema objects and is included in the
bridge hash with the adapter, worker and progress code. A change to any of these
requires new policy commitments. Existing frozen source/policy pairs retain their
own files and identities; do not transplant this code into a closed experiment.

## What the schema does and does not establish

The fixed object contains `result.facts` plus empty top-level claims, evidence and
requested_actions arrays. Facts carry string keys/values and source/revision
citations. The schema constrains object membership, basic types and array bounds.
It contains no answers, task-specific keys, current source IDs, enum values or
external references.

Strict local parsing, string/integer limits, uniqueness, currentness, citation
relevance and independent reference scoring still apply. Schema support does not
guarantee semantic correctness, safety of an action, or completion when a token
budget is exhausted. Native completion, wire identity, reasoning mode, unknown
state handling and exact unload rules are unchanged.

## Validation and prospective use

Regression tests cover legacy behavior, explicit admission, missing/unknown
contracts, version downgrade, exact wire binding, schema/boolean substitution,
fresh-object mutation resistance, request override values and source identity.
The original native adapter suite continues to run. Linux checks cover the
subprocess lifecycle cases that are unavailable on Windows.

The next native test uses a new fixed development workflow and fresh observations,
with four calls maximum: two checkpoints in none and medium reasoning modes.
It retains the prior pilot's declared 2048-token/180-second HTTP/210-second worker
limits and no-retry policy, but does not pair scores with the old tasks. All routes
share the structured-output capability and initial response. Source, requests,
references, schema policy and stop rules are committed before inference.

This is a format-reliability development check. A successful run would justify
broader development cases, not a claim that DOHAA outperforms a strong simple
pipeline. Agent-generated policy/assurance changes require maintainer review.
No installed source, service or old experiment is modified. Rollback removes the
new opt-in policy path and its schema helper in a reviewed development revision.
