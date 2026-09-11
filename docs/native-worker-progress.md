# Native worker progress diagnostics

A parent timeout can kill a worker before its terminal trace is written. The
parent now retains small progress hints through an inherited Unix datagram socket
and writes a separate `worker-NNNN.progress` record after the worker ends. This
helps distinguish its last reported phase (setup, metadata, send, read, parse or
native return) even when the terminal trace is empty. It does not establish why a
historical request timed out or recover evidence from an earlier run.

The child receives only its own socket descriptor, not a writable evidence path.
The receiver retains a bounded snapshot in parent memory; this survives child
termination, not a parent/host crash. It permits at most 64 packets of 1024 bytes
and persists at most 2048 bytes per call. A 2048-byte reservation counts against
the existing total trace budget. Read updates are throttled to at most 40, leaving
room for later phases, with the first decoded bytes reported immediately. Delivery
is best effort: hints may be absent, dropped or stale, and are not a heartbeat or
proof of a stalled server.

Packets contain only finite phases, bounded counts, elapsed time, HTTP status and
request/runtime-policy/bridge hashes. Duplicate/unknown fields, invalid bindings,
partial or oversized packets, stale sequence numbers and regressing counters
reject the snapshot without retaining raw input. No prompt, response, endpoint,
credential, header or exception text enters the progress record. The worker can
still lie within the permitted shape; bindings identify context, not truth.

Every summary explicitly marks itself non-authoritative and says server completion
is unverified. Progress never changes terminal verification, result acceptance,
retry, model unload or request/worker time budgets. A forged `native_returned`
phase cannot turn a timeout into completion. Missing or rejected progress leaves
existing generation behavior intact, as does failure to persist this optional
diagnostic. The original terminal trace remains the only completion evidence.

The code binding includes the adapter, worker and progress module. New runs need
new code-bound policies as usual; closed campaigns and their policies stay frozen.
The feature is limited to the existing POSIX isolated-worker boundary. Tests use
synthetic streams, inherited descriptors and terminated child processes, with no
model/provider calls. See issue #65. Rollback is a revert of this focused change;
no installed runtime or study is activated by adding the diagnostic.
