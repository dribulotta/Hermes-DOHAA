# Native document-stream development calibration

Tracking: [#74](https://github.com/dribulotta/Hermes-DOHAA/issues/74), part of M2 in
the [roadmap](innovation-roadmap.md). Builds on #73; this is not a confirmatory
comparison or a change to a closed study.

`tools.document_stream_request` accepts only a versioned observation and task.
It rejects extra fields, future/backdated events, conflicting revisions and
duplicate delivery identities. It bounds the native input to 64 KiB and binds
its bytes and trusted prompt to the native collection policy and request ID.
Document text remains untrusted data. Schema whitelisting cannot prove that an
operator did not place a reference answer inside an otherwise valid text field.

The proposed output is a strict JSON proposal with facts inside `result` and
empty top-level claims, evidence and requested-actions arrays. Fact citations
belong inside each fact. This is a development response contract, not a general
replacement for the project's claim/evidence format. Duplicate JSON keys, extra
fields, nonfinite constants, missing citations and side channels are rejected.

`StreamCitationGate` reads only the public observation. It checks shape, requested
fact keys and current delivered source revisions. It deliberately cannot establish
whether a value is true, a current citation is relevant, or an omission is justified.
Tests prove that a wrong value with a current citation can pass this gate. The
separate grader must detect that failure. Never label gate passage as factual
correctness or pass expected values into this gate.

`SingleProposalRuntime` lets the actual `DohaaController` consume an observed
proposal once, recording its ordinary evidence ledger. The simple arm applies
the same gate directly. Both have a one-attempt budget and zero model retries.
The direct arm describes the same initial response. These are three routes for
one shared observation, not three independent model generations or equivalent
estimates of end-to-end latency.

## First fixed development pilot

- One fresh synthetic shipment workflow with delivery-day/dock facts, an
  irrelevant notice, a correction and a late delivery of the older revision.
- Two checkpoints, one before and one after the correction/late arrival.
- Qwen27, first no reasoning and then medium reasoning; at most four generations.
- One client request at a time; 2048 output tokens, seed 909120901, temperature 0,
  top_p 1, HTTP timeout 180 seconds, worker timeout 210 seconds.
- Native one-turn `AIAgent`, toolsets/memory/profile/background review disabled,
  request and wire/terminal binding verified by the existing native adapter.
- Entire pilot wall limit 1200 seconds; reserve 240 seconds before each new call.
- Terminal wrong/invalid/limited responses are recorded, without replacement.
  Unknown completion, identity/integrity failure or unexpected residency stops
  subsequent calls. Completion must be verified before exact owned-instance
  unload; verify release at the end of each mode block. Never unload unrelated
  instances or use a progress hint as cleanup authority.

This calibration uses the integrated development source containing the pending
native adapter/progress changes and these tools. Record its exact tree separately
from the PR branch. A merged #73 alone does not install the native adapter chain.
The installed Hermes source and services remain unchanged.

Before inference, retain source/runner, requests, references, native policies,
stop rules and their hashes in a new run directory. Publish the manifest digest
before launch. Verify that the worker UID/GID cannot read evaluator and future
event files. Keep the evaluator root private, copy only code to the worker import
directory, seal every finished worker profile and check its read denial from a
subsequent identity probe. Read-only probes are development checks, not a proof
against kernel compromise or every possible side channel. Credentials stay in
memory and private traces never enter public results.

## Reporting

Report requested/dispatched/terminal counts, response format, reasoning observed,
public-gate acceptance, independently graded supported facts, controller ledger
verification, elapsed generation time and exact cleanup separately. A fully
successful pilot establishes that the plumbing works on this narrow case.
It cannot establish architectural superiority, persistent learning, broad
semantic accuracy or robustness of long-running workflows.

Follow with broader development families and variance/resource calibration,
then a separately preregistered fresh comparison. Regressions run through the
standard project checks; no live inference is part of CI. Rollback removes the
new request helper, tests and document without changing production modules.
