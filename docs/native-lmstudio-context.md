# Explicit native LM Studio context

Native policy `hermes-native-shadow-policy/1.2` adds a required integer
`context_length` and retains the fixed `document-stream-proposal/1.0` response
contract. The generation cap must be smaller than the context; context is bounded
at 262144. This is a policy bound, not proof that a model supports or has loaded
that capacity. Collectors must verify the actual instance with their residency
journal before calling the worker.

The version selects native provider `lmstudio` and writes the exact context in
the fresh worker's `model.context_length`. The pinned native Hermes source has
a generic minimum context floor and a supported exception for an explicit
LM Studio context. The previous generic `custom` provider did not select that
exception: the setup-only #87 reproduction with context8192 failed at
`agent/agent_init.py:2846`, before generating. Setting a larger fictional context
would obscure the actual capacity. Loading a larger context solely to satisfy
that generic floor would consume additional resources unnecessarily.

For version1.2, the worker also verifies native provider `lmstudio`, configured
context and effective compressor context against the policy. It continues to
require disabled tools, memory and background review, one turn, exact model,
OS identity/profile isolation and the fixed actual generation request/response
checks. The new field configures native initialization; it is not an unverified
generation-request override. Native loading remains JIT-compatible, while the
collector explicitly owns/preloads and verifies the instance.

Legacy versions1.0/1.1 keep their provider/configuration behavior and reject an
undeclared context field. The changed policy, worker and response-format files
remain covered by the bridge digest. No arbitrary provider, arbitrary response
schema or tool action is enabled.

Issue #88; agent-generated and pending maintainer review. Use isolated
development copies and prove setup-only compatibility before a new frozen
cohort. The #87 diagnosis does not retroactively establish the unrecorded cause
of #83. No installed-runtime change is made. Rollback is using legacy policies
or reverting this opt-in version; closed cohorts are never replayed.
