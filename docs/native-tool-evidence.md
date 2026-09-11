# Native tool proposals and durable host evidence

Agent-generated development implementation for #95; explicit maintainer review
is required before integration. This branch has an isolated foundation combining
native PR #89 and host-step PR #94. Those draft PRs and main are not merged by
this work. The installed native Hermes checkout is unchanged.

## Fixed contract, separate authority

`hermes-native-tool-policy/1.0` selects exactly
`controlled-tool-proposal/1.0`, with an explicit LM Studio context. The shared
schema and parser constants live in `learning/native_tool_contract.py`. The
schema describes syntax, never permission. Old native document policies retain
their previous contracts, including empty document actions. No arbitrary schema,
tool name, caller identity, legacy fallback, or native tool execution is added.

The existing worker still checks an isolated identity, a fresh profile, disabled
tools and memory, no background review, one iteration and one actual generation
request. The bridge digest now covers every Python file copied into the worker,
including transitive helpers and the shared schema. Package-relative paths and
normalized line endings give the same identity on Windows and Linux.

## Evidence path

The root-owned native adapter publishes a private session record after its pinned
source and startup checks. Before a tool generation it requires that exact
record. After the child exits successfully, it validates the original logical
input, outbound model/settings/schema, single request, terminal wire response,
native completion and content agreement. Only then does it atomically publish a
request-addressed terminal artifact referencing the original trace and session
digests. Progress messages never create a terminal artifact.

The host configures `NativeToolEvidenceBridge` with fixed paths for the native
evidence, residency journal and three simulator databases, plus the exact native
and collection policies. Its private metadata binds those paths and the host
verifier source digest across restart. The collection policy remains a transport
binding; document scoring is not used to authorize tool effects.

1. Start the native adapter on an empty, exclusively reserved backend. Start one
   `ModelResidency` using the same model, context and native policy digest. Its
   callbacks must be the trusted native backend transport.
2. Create the bridge and `HostStepStore` with `terminal_verifier=bridge.verify`.
   Allocate the immutable host step through the store.
3. Call `bridge.generate(host, operation_id, request_bytes, adapter)`. The bridge
   checks the durable loaded instance against both backend catalogs and binds
   that exact instance to the adapter. It persists the host request/step binding
   and exact logical bytes before recording generation intent and dispatching.
4. The returned bytes must equal the reverified native artifact. The residency
   journal commits the terminal digest with its original request, model, context,
   exact instance and load receipt. This per-request history survives later
   generations and unloading; it cannot alone prove native completion.
5. `host.record_terminal(operation_id)` invokes the verifier. It rereads protected
   artifacts and matches native completion to durable ownership and the original
   host step/request/policy. The host then parses the fixed proposal against its
   original scope. A valid proposal is still not an effect.
6. Only an explicit host `Executor` may apply the simulator operation. The tool
   independently checks permissions, quota and inventory version. The host marks
   an effect only after matching completed intent and actual tool receipts.
7. Finish the owned residency only after known completion, then clear the
   adapter's owned set and finish its empty-backend lifecycle. Unknown ownership
   or completion must remain unresolved; never substitute an empty catalog.

Model output receives the public logical task, not database paths, scope grants,
reference answers or host identities. The host chooses all such authority.

## Recovery and limits

`bridge.recover(binding)` never sends a generation or executes a tool. If a
verified native artifact survived a crash before residency bookkeeping, it may
commit that exact terminal against the still-pending original generation. It
then performs the same ownership and wire verification as normal admission. A
crash before an artifact leaves the request unresolved. Calling `generate` again
does not grant permission to resend. A crash between host binding and bridge
binding is likewise unresolved, not automatically repairable.

All per-task activity must use one trusted coordinator. Independent host,
residency, intent and effect stores do not form a universal transaction against
writers bypassing these APIs. Filesystem ownership protects this boundary from
the unprivileged worker; digests do not authenticate data against a root writer
who can replace every artifact and database. Session records are assertions from
the trusted adapter after source checks, not remote cryptographic attestation.

The live evidence bridge requires Linux root, private root-owned directories,
regular private files, no symlinks or extra hard links, and protected parent
directories. Do not place databases or references inside a worker profile. No
Windows ACL emulation or automatic permission repair is provided.

Regression tests use synthetic transport through the actual adapter verifier,
including altered schema/wire/content/source, wrong identity/request count,
missing ownership history, duplicate artifacts, pending completion, read/write
denial under the worker UID, two process crashes and an actual simulator effect.
Root-only tests must be run in isolated Linux; ordinary non-root CI skips them
and is insufficient evidence by itself. Generation-blocked native setup is a
separate compatibility check. Neither establishes real model quality, task
success rates, DOHAA advantage, or compatibility of LM Studio's schema decoder.
A fresh preregistered simulator canary is still required before those claims.
