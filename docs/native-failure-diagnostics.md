# Native generation failure diagnostics

A failed send, response read, capture limit, HTTP status, parse or close now retains
the first dispatched-generation failure in the private worker trace. The finite
`generation_failure` record contains a code, stage, HTTP status when available,
request size, decoded response bytes observed and bytes retained. These counts
describe chunks delivered by the HTTP client, not the number of network bytes
received by the server. Raw exception messages, URLs and headers are excluded.

The private `wire_response_base64` field retains at most 4 MiB of decoded response,
including a prefix when the next chunk exceeds the limit. Requests and metadata
remain limited to 512 KiB. See [stream capacity](native-stream-capacity.md);
partial evidence is never accepted as a terminal receipt. No response is invented
when the send fails or the client has not yielded any bytes. A worker killed before
writing its trace can still have no such diagnostic; the parent reports its own
worker timeout and keeps backend completion uncertain.

After checking worker identity, profile and request/code bindings, the native
adapter propagates an allowlisted diagnostic through `NativePromptError.code`.
Missing or malformed diagnostics remain `native_shadow.worker_unverified`.
General collection/proposal boundaries still sanitize arbitrary adapter exceptions.
An orchestration observer can retain the native exception code separately without
turning it into a completed response or changing the collection's verdict.

Blocked retries and summaries do not replace the first failure and do not issue
another generation. Uncertain completion still blocks new requests and unload.
Any later operational reconciliation requires independent evidence of completion
and exact ownership. Diagnostic improvement does not establish the cause of an
older incomplete trace, repair its audit or make an unsuccessful trial pass.

The regression tests use synthetic streams and exceptions, including a secret-like
exception message, oversized chunks, partial reads, malformed responses, close
failures and denied continuations. They require no model or live credentials.
