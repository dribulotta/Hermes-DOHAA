# Explicit binary reasoning for the native tool adapter

## Compatibility contract after the endpoint rejection

The literal `/1.1` candidate below was rejected by the tested chat-completions
endpoint with HTTP 400: `off` is not in its accepted generic-effort vocabulary.
Catalog capabilities are model settings, not proof of API parameter acceptance.
The old candidate remains identifiable for historical evidence; its literal
values must not be presented as a working compatibility fix.

Opt-in `hermes-native-tool-policy/1.2` instead separates `reasoning_intent` (`off`
or `on`) from `reasoning_effort` on the wire (`none` or `medium`, respectively).
It requires `reasoning_contract: lmstudio-binary-via-generic-effort/1.0` and the
same exact model/capability commitment. Both the host and worker require binary
capabilities with declared default `on`. Unknown, contradictory or changed
settings are rejected before generation; no missing field selects a fallback.

This contract explicitly acknowledges the observed backend limitation:
`medium` is accepted by the endpoint but unsupported as an intensity by this
binary model, which reported falling back to its declared default `on`.
It **does not remove or hide that warning, repair the server, or claim an
independently forced internal on setting**. It is a compatible request strategy
with a declared default dependency. Other builds/models need separate validation.

Worker traces contain fixed `reasoning_request_evidence`: binary intention,
actual wire value, committed default, whether on depends on that default, and
`effective_mode_attested: false`. Terminal verification recomputes this record,
rechecks raw catalog/request/response bytes, and rejects fabricated attestation.
Returned reasoning is counted independently; zero returned characters is not a
proof about hidden internal computation. The off contract still rejects a
response that contains returned reasoning.

The planner requires profile library `/1.2` for these policies, rejects mixed
versions, and emits plan `/1.2` entries with `reasoning_mode`, `reasoning_effort`
and `on_depends_on_declared_default`. The fixed rule keys `none`/`medium` retain
their identity; they do not become reasoning-intensity claims. Reservations,
sampling, context, response syntax, permissions and host recovery are unchanged.

The regression suite includes a synthetic copy of the observed structured 400
error, denial of a second send, both mappings, committed-default mismatch,
evidence tampering, fixed budgets and privileged host recovery/unload. A fresh
live probe must establish operational acceptance separately. Any later quality
study compares these declared request strategies and must retain the dependency
and effective-setting limitation; historical cohorts are not relabeled.

The mapping also appears in the [native Hermes resolver](https://github.com/NousResearch/hermes-agent/blob/main/agent/lmstudio_reasoning.py).
That implementation is a source for Hermes behavior, not server attestation.

## Historical literal candidate `/1.1`

`hermes-native-tool-policy/1.1` is an opt-in development contract for models
whose catalog advertises exactly `off` and `on`. Its `reasoning_effort` is one
of those literal values. It adds `reasoning_model_sha256`, computed with
`native_reasoning.binary_model_sha256(catalog_bytes, exact_model_key)` by the
trusted host before dispatch. It retains the fixed tool response schema,
context, request ceiling, worker isolation, endpoint and credential bindings.

The commitment covers the full selected catalog entry except `loaded_instances`.
This binds the exact key, selected variant and raw reasoning capabilities.
Changes to descriptive metadata also require a fresh commitment. The catalog
is bounded to 512 KiB and 256 entries; malformed, missing, nonbinary, duplicate
or cross-model alias evidence is rejected. A loaded-instance alias is never
silently used as a model key. Residency ownership and exact unload remain
separate host responsibilities.

The existing Hermes transport and request overrides send the literal binary
value after native generic-effort translation. Internal `enabled`, optional
`think`, the actual wire request, and returned-reasoning checks agree with that
intent. The guard captures and verifies an existing `/api/v1/models` response
before authorizing generation. It adds no metadata or generation call: if the
native path does not perform that lookup, generation is blocked. A failed
lookup is latched for the worker; later success cannot erase it. Terminal
verification rechecks captured catalog bytes and the original policy commitment.

This is evidence of the configured intention, advertised capabilities, actual
request and observable response. It is **not per-request attestation of the
server's effective internal setting**. An accepted request or absence of
returned reasoning text does not prove the server honored the mode. A fresh
native compatibility probe must establish endpoint acceptance separately;
these unit tests do not claim live model compatibility or quality improvement.
No installed Hermes source, service configuration or alternate endpoint is used.

Previous policy versions retain their generic efforts and override behavior.
Existing closed evidence is not migrated, renamed, rescored or rerun. Binary
policies require `hermes-reasoning-profiles/1.1` for the prospective planner.
Historical selector keys remain `none` and `medium`, mapped explicitly to `off`
and `on`; `medium` is only a selector key here, with no intensity claim.
New `hermes-reasoning-budget-plan/1.1` entries include `reasoning_mode` alongside
that key. Both profiles must bind identical model metadata and all other
nonfactor settings. Reservations and whole-plan rejection remain unchanged.

Relevant API references: [model capabilities and instance identities](https://lmstudio.ai/docs/developer/rest/list)
and [the separate OpenAI-compatible chat endpoint](https://lmstudio.ai/docs/developer/openai-compat/chat-completions).
The latter does not document binary reasoning in its listed parameters, so
capability metadata alone must not be treated as endpoint validation.
