# Numeric evaluation on the native route

`hermes-native-evaluation-policy/1.0` accepts only
`native-numeric-answer/1.0`: one object with an `answer` decimal string.
It is a separate opt-in evaluation policy, not an inventory operation or a
production tool-policy upgrade. It retains the fixed context, exact catalog
binding, original-wire verification, single generation and host-owned evidence
used by the native comparison route. Existing policy schemas remain unchanged.

The strict parser rejects duplicate keys, extra fields, booleans, JSON numbers,
non-finite values, exponent notation, prose and overlong answers. Decimal
comparison avoids floating-point rounding. It verifies syntax only; a separate
host grader owns reference truth. No answer, example or expected value is
embedded in the generation schema. Tools remain disabled.

Binary requests retain the declared `none` / `medium` compatibility mapping.
The latter depends on the model's committed default-on. This policy does not
attest the backend's internal reasoning setting or resolve that limitation.
The source-record planner rejects this new evaluation policy: a numeric study
must supply its own explicitly versioned, prospective selection protocol.

Live evidence, infrastructure, dataset records and selection rules are kept in
the separate local study. This code introduces no study, merge, deployment,
promotion or inference on import.

The changed native dependencies receive a separate reviewed source commitment
under `verified-tool-admission/1.5`, including the numeric and response-format
modules. All earlier profile pins remain unchanged and reject this new source.
This source-profile update does not authorize quality claims in the downstream
tool-route validator, nor does it turn numeric answers into authorized effects.
