# Native Hermes adapter for isolated shadow development

`NativePromptAdapter` in `hermes_dohaa.learning.native_prompt` implements the
prompt collector's `start/generate/finish` interface. It invokes the installed
native `AIAgent` in a new unprivileged process for every request, verifies the
actual HTTP request and terminal response, and retains private transport traces.
It uses a separately reserved LM Studio backend with an OpenAI-compatible `/v1`
endpoint and the `/api/v1/models` lifecycle API.

This is an optional **development** adapter. Its conformance tool runs installed
Hermes against loopback response fixtures, with no LLM inference. Passing those
controls does not establish live-provider compatibility, learning improvement,
general accuracy, or readiness to deploy this privileged launcher. The completed
learning pilots and their negative verdicts remain unchanged.

## Authority and isolation assumptions

The collector/launcher runs as root on the isolated Linux development host.
Workers run under a different, unprivileged UID/GID with no supplementary groups.
Reserve that worker account exclusively for this purpose before handling real
credentials. Do not share it with unrelated processes. The conformance command
can use a disposable test identity because it supplies only synthetic credentials.

Keep collector evidence, oracle files and their ancestors private to the
collector account. Only the public input, prompt, hashes, runtime policy and
selected provider credential enter the worker's private stdin pipe. No oracle
bytes, oracle paths, training evidence, prior responses or inherited environment
secrets enter that configuration. Subprocesses close inherited descriptors,
disable user site-packages and unsafe current-directory imports, and receive an
explicit environment. Python and native dependencies must remain trusted.

The worker imports a root-owned, read-only copy of the product package and a
trusted native installation. Its temporary profile is separate from active
runtime profiles. Memory, user profiles, tools, MCP, title generation and
background review are disabled. Construction checks the actual agent fields
before generation. After exit, root seals the profile directory against later
workers; it does not traverse or modify worker-created child links. Private
history and transport evidence are retained, not deleted.

This is an OS account/file-permission boundary plus trusted application guards,
not a container, network namespace, arbitrary-code sandbox or protection against
a compromised host/kernel. Workers must not be allowed to read oracle files via
other accessible paths. The conformance command explicitly checks an actual
private marker file as the worker identity. Root and unrelated writers must not
alter installed source during a run. Candidate code patches/tests remain inert.

## Pin runtime and collection policies

Prepare strict UTF-8 JSON with exactly these runtime-policy fields:

| Field | Required value or range |
|---|---|
| `schema_version` | `hermes-native-shadow-policy/1.0` |
| `native_commit` | Independently retained 40-character native Git commit |
| `bridge_sha256` | `native_bridge_sha256()`, covering adapter and worker source |
| `model` | Exact selected model identifier, 1–256 characters |
| `endpoint` | HTTP(S) literal IP, explicit port, exact `/v1` path; no credentials/query/fragment |
| `reasoning_effort` | `none`, `minimal`, `low`, `medium`, `high` or `xhigh` |
| `seed` | Integer, 0 through 2^31−1 |
| `temperature`, `top_p` | Finite numbers in 0–2 and greater than 0 through 1 |
| `max_tokens` | Integer, 1–16384, sent exactly to the provider |
| `request_timeout_seconds` | Integer, 1–600 |
| `worker_timeout_seconds` | Integer, 2–720, greater than request timeout |
| `request_limit` | Integer, 2–512; an upper bound on logical calls |
| `worker_uid`, `worker_gid` | Nonzero integer identity of the reserved worker |
| `exclusive_backend` | `true`, an operator assertion of exclusive use |

The immutable public policy mapping prevents accidental in-place parameter
changes. Both parent and worker recheck the policy/source commitment. Native
startup checks the expected Git commit and tracked cleanliness; these checks do
not fingerprint every dependency, interpreter or loaded module and are not
signatures. A trusted, immutable installation remains necessary.

Make the collection policy with `create_collection_policy`, using
`native_adapter_sha256()` and the exact runtime-policy hash. Declare the public
result fields and preregister the ordinary shadow plan/suite independently.
Construct `NativePromptAdapter` with the runtime bytes/hash, collection-policy
bytes, native source directory, Python interpreter, a new private evidence
directory and provider key supplied through an authorized credential source.
Pass it to `collect_prompt_shadow` with the normal pinned inputs. There is no
CLI that reads a live profile or automatically discovers/reuses credentials.

The provider credential is kept out of command arguments, environment, policy
and safe reports. Authorization headers are excluded from transport captures.
The worker's on-disk config contains a placeholder;
the real credential is supplied to its per-request route in memory. Diagnostic
logs are private because native third-party diagnostics may still contain
sensitive data. Never publish raw traces, profiles or logs.

## Transport and budget checks

Before dispatch, the adapter validates the logical request hashes and collection
policy. The worker verifies the exact selected model, reasoning effort, seed,
temperature, top-p and token request limit on the serialized HTTP body. An
optional native `think` compatibility flag must be a boolean consistent with
the reasoning policy. Unknown generation options, multiple-choice requests,
tools or conflicting parameters are rejected before dispatch.

There must be exactly one user message equal to the committed public input and
one uniquely framed prompt inside the native system message. Prior assistant
messages, substituted roles and added public-input text are rejected. The frame
identifies the prompt insertion; the rest of the native system template is
controlled by the pinned native source and fresh restricted profile.

The HTTP guard allows only the selected endpoint and bounded model metadata
reads. Redirects are disabled. Socket connections are restricted to the selected
literal IP and port. Exactly one generation request is allowed per worker;
additional SDK requests and native iteration-summary fallbacks are blocked.
Native fallback exhaustion and provider `finish_reason: length` stay failed
`budget_exhausted` outcomes, even if partial visible text exists.

Responses require strict JSON or a complete SSE sequence ending in `[DONE]`,
exact model identity, a single choice and one terminal `stop`/`length` reason.
Tool/function calls, conflicting roles, malformed JSON, truncated streams and
multiple terminal reasons fail closed. The parent independently reparses the
captured request/response and compares completed native output with wire-visible
text (ignoring only outer whitespace in that comparison). It returns the actual
wire text to the collector; it does not strip fences or repair a model response.

The adapter pins the **requested** token limit and blocks additional generations.
It does not independently measure GPU work or prove a provider honored that
limit; raw response usage remains in private transport evidence. Per-request
HTTP timeouts and a separate process deadline bound waiting. A process timeout
kills only that worker, not the provider; server completion remains unknown.
Core dumps are disabled and worker files are size-limited. Request and metadata
bodies are bounded to 512 KiB, generation responses to 4 MiB, individual worker
traces/files to 8 MiB and aggregate traces to 512 MiB. The adapter reserves room
for a maximum-sized trace before every worker. These are data limits, not a
complete CPU/RSS/disk quota system. See [stream capacity](native-stream-capacity.md).

## Residency and failure behavior

Start requires an empty loaded-model catalog and the selected model present in
the catalog. After each verified terminal generation, the adapter records the
selected model's observed instances. Before another call or unload it checks
that those exact instances remain and no unrelated model appeared. Model
switching is not supported inside a block. `finish` unloads only the recorded
instances, then verifies an empty catalog. An unload acknowledgment alone is
insufficient. Unexpected/new instances are not unloaded.

Exclusive backend use is an operator assumption, not a backend-wide lock. A
different client loading the same model during a request cannot be reliably
distinguished from this adapter's own JIT load. Do not run concurrent users or
clients against this reserved backend during collection.

Invalid or unverified worker/transport results stop collection conservatively.
Unknown completion never permits automatic unload or another generation. This
also covers timeout or failures where the adapter cannot prove all expected
bindings. Resolve server state through independent observations before cleanup
or another run. No retry resumes an interrupted study, and no completed old
study is rescored under a changed policy.

## Repeatable conformance without LLM inference

`tools/check_native_shadow_adapter.py` accepts `--native-source`,
`--native-commit`, `--python`, `--worker-uid`, `--worker-gid`, a new `--output`
directory and `--scenario`. It starts a loopback fixture server and exercises
the real installed `AIAgent`, isolated workers, product collector and independent
recording reconstruction. It does not read active profiles or real credentials.

Scenarios are `valid`, `length`, `fenced`, `wrong-model` and `truncated`. They
check correct collection, retained budget/admission failures, stopped invalid
transport, no uncertain unload, account separation, sealed prior profiles,
fresh restricted agents and verified unload of the simulated owned instance.
The valid scenario's positive score is a deterministic fixture expectation, not
evidence that an LLM learned. Ordinary public tests cover policy, wire parsing,
subprocess isolation parameters and lifecycle error cases without installing
Hermes or contacting any provider.

All collection reports still say `execution_attested: false` and
`activation_authorized: false`. Transport consistency and trusted-process
observations are not cryptographic server attestation, scientific proof of
learning or authorization to deploy. A subsequent bounded
[live-provider canary](native-shadow-live-canary-20260907.md) passed 17 integration
checks with four real generations, but its response criterion failed at 3/4
correct. Controlled development adoption/reversal remains subsequent work.
