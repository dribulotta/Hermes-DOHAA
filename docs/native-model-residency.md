# Durable model ownership for development collectors

Issue #85 follows a stopped calibration where model identity and context had
been checked in memory, then native agent creation failed before dispatching a
generation. A restart could not retrieve a durable record of the load response.
The original cause of that startup failure remains unconfirmed.

`tools.native_model_residency.ModelResidency` is an opt-in collector helper.
It stores the model, requested context and runtime policy digest in a private
SQLite journal, with `synchronous=FULL` transitions and an event history. The
transport callbacks remain supplied by the trusted collector, with its existing
endpoint, credential, timeout and redirect restrictions. No live runtime or
default model behavior changes merely by importing this helper.

The collector calls `start()` only against an empty, exclusive backend. It
commits a load intention before the request and the terminal instance ID and
observed context before allowing generation. Only one matching loaded instance
with verified context is accepted. A process crash after that receipt can reopen
the same journal and retain exact cleanup authority. A missing load acknowledgment
leaves `loading`: another matching model name is not proof of ownership and
neither loading nor cleanup is reissued.

Before each actual generation, call `begin_generation(request_sha256)`. Only
after the native adapter verifies its full worker identity, profile, request,
wire response and terminal receipt may the collector pass that exact receipt to
`record_terminal()`. The helper rechecks the outstanding request inside the
journal transaction. It stores the receipt digest, not its response content.
Its receipt shape checks do not authenticate a worker or replace the adapter's
verification. A diagnostic hint, zero-dispatch counter, malformed receipt or
backend becoming empty cannot clear an unresolved generation.

`finish()` checks the same instance again, commits the unload intention and
unloads that exact ID. It commits `closed` only after observing an empty backend.
A lost unload acknowledgment stays `unloading`; cleanup is not automatically
repeated. Unexpected model residency or context stops the operation. A terminal
load with a known but wrong context retains its identity as `loaded_invalid`;
cleanup is possible only if the catalog exactly matches that recorded identity
and observed context. Missing context evidence does not authorize generation.

The worker also retains a finite `setup_failure` field for agent creation,
configuration or profile checks. Exception kinds come from a small fixed list;
exception messages, arbitrary class names, paths and credentials are excluded.
These diagnostics remain non-authoritative: existing adapter error/identity/
profile/wire checks and unknown-completion behavior remain unchanged. The
changed worker is included in the existing bridge digest.

Tests cover real child-process exit after the load receipt, lost load/unload
acknowledgments, conflicting policy, wrong/missing context/identity, foreign
residency, unresolved generations, malformed receipts and request substitution.
The journal and callbacks assume a trusted, exclusive collector and a private
host-controlled directory; they do not handle malicious journal writers or prove
physical power-loss durability. Transport operations must use bounded timeouts.

This is agent-generated development code and requires maintainer review.
Rollback consists of omitting the opt-in helper and reverting the diagnostic
field; there is no installed-runtime migration or deployment. Closed experiments
remain closed. Any subsequent real calibration needs fresh frozen materials
and a prospective commitment.
