# Fixed model context for native comparisons

`hermes-native-tool-policy/1.3` requires the explicit field
`context_contract: fixed-native-json-context/1.0`. It retains the binary intent,
generic wire compatibility, catalog commitment and declared default-on dependency
of policy 1.2. Neither version attests the server's internal reasoning setting.

The new profile uses native Hermes `AIAgent` with a deliberately minimal system
context. Its entire permitted message list is:

1. One system message: `Return only the requested final JSON object.`, two
   newlines, then the existing request-bound `HERMES_SHADOW_BEGIN/END` prompt frame.
2. One user message containing exactly the committed input.

Each message has only `role` and `content`. Additional environment/profile paths,
timestamps, identity instructions, metadata, messages or reordered roles are
rejected before the generation sender. Terminal verification checks the retained
original request bytes against the same contract. A valid frame substring alone
is insufficient under this profile.

Fresh workers still have separate temporary HOME, HERMES_HOME and working
directories, a separate UID/GID and no native tools or persistent memory. The
bridge replaces both prompt builders only on the fresh agent instance, because
the pinned native conversation loop rebuilds its cached prompt at session start.
It does not modify the installed Hermes source or share worker directories.
Any later native hook that changes the actual messages fails the wire guard.

Use `hermes-reasoning-profiles/1.3` for paired profile libraries; plans report
`hermes-reasoning-budget-plan/1.3`. Both profiles must agree on this context
contract and all other nonfactor settings. Older policy/library versions retain
their existing semantics and cannot implicitly opt into this contract. Source
admission profile `verified-tool-admission/1.4` adds the context, reasoning and
worker dependencies to the existing reviewed downstream pipeline; it does not
change the intervention stage or authorize a quality claim.

This contract binds client messages, not a backend chat template. Live paired
verification must separately capture the model input, predeclare any identifier
normalization and permit only the intended reasoning-template difference.
Do not strip newly discovered differences after seeing results. Such a bounded
control is an infrastructure check, not evidence of a DOHAA quality advantage.
