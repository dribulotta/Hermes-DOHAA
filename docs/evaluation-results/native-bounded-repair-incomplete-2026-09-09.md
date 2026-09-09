# Incomplete native comparison after bounded repair scheduling

The prospective comparison of the controller correction in [PR #62](https://github.com/dribulotta/Hermes-DOHAA/pull/62)
closed **incomplete** on 2026-09-09. It establishes neither superiority nor
equivalence to the strong simple baseline, and does not establish lack of benefit
of the corrected architecture. The synthetic scheduling regressions remain
separate evidence; no persistent learning was tested.

The plan used 48 new cases, three models and none/medium reasoning, for 288
configurations. It retained the same strong baseline, analysis thresholds,
shared initial response, two-generation limit per arm, 8192-token cap and serial
client concurrency. The visible-rule reference without an LLM solved 48/48.
Full manifest and protocol bytes were retained outside the executor host before
launch. The source was an isolated integration of pending changes, not a deployed
release.

Execution lasted 2134.958 seconds. There are 78 request/dispatch records, 77
terminal receipts and 77 configuration rows. The next worker, using Qwen 27B
with medium reasoning, exceeded its 210-second worker timeout
(`native_shadow.worker_timeout`). Its terminal receipt is missing and its worker
trace is empty. The 211 remaining configurations are unobserved, not zero scores.
The timeout does not by itself establish memory pressure, a network fault or
controller-attempt exhaustion.

Executor and the supervisor's single final audit both exited 2. The audit
correctly returned `incomplete` before full decision reconstruction. Its
`real_calls` field follows the executor's dispatch accounting in this branch;
78 must not be presented as a fully verified count of completed server
generations. Partial quality totals are not published as validated study results.
No case was replayed or rescored and no budget or decision threshold was extended.

Four finished blocks verified exact model unloading. The interrupted block
retained an uncertain state and did not unload blindly. Later independent checks
found no owned study or worker processes, and the exact owned model was idle with
zero queued requests and no intervening use. Only that instance was then unloaded;
LM Studio was verified empty. This later cleanup does not prove completion of the
historical request or recover its missing receipt. The original interrupted-block
record remains unchanged.

All frozen files still match the retained manifest. Installed native source and
services match prelaunch state. No activation, deployment, service restart or V5
release occurred. The public summary and hashes do not independently attest
execution (`execution_attested: false`). The companion JSON records identities,
accounting and limitations. The timeout root cause remains unresolved; no
replacement campaign was started automatically.

Manifest: `d282bf70c567bcfdd38267ae94980a7c353e8dc4bba7cef2f92e5171d4755ce2`.
Protocol: `d72336274205cf0cc60c2711aebc5b2bfb216284ad391bddfda043ce80845d80`.
Source commit: `9dd7578b3f9134270df532b51cb425862dc5910b`.
Source tree: `43c2f5d2e532181953dda17cb35d6f2de7a7001d`.

Related documentation issue: [#63](https://github.com/dribulotta/Hermes-DOHAA/issues/63).
The closed earlier studies in #58 and #60 are unchanged.
