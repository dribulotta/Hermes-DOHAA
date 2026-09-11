# Backend concurrency diagnostic — 7 September 2026

**Four concurrent requests were not beneficial in this workload.** The independent recount passed all 15 validity checks, but throughput at 4 was 0.543 times throughput at 1, with one paired accuracy regression. This is a direct LM Studio backend test on Qwen3.6-27B-MTP without reasoning; it does not test DOHAA controller concurrency.

| Concurrent requests | Correct outputs | Runtime failures | Mean latency | p95 latency | Requests/second |
|---:|---:|---:|---:|---:|---:|
|1|4/16|0|9.41s|14.04s|0.1063|
|4|4/16|0|68.59s|72.30s|0.0578|

Both configurations returned incorrect sequence content in 12/16 cases. Equal aggregate accuracy hides one case that was correct at 1 and wrong at 4, offset by another case improving. No request was repeated or excluded. Complete-request throughput decreased by 45.66% at 4; this result favors a serial client for comparable testing, not a universal model-server configuration.

## Fixed design and validation

The test began only after the new native accuracy matrix passed all validity checks and verified final unload. A new 13-file protocol/manifest fixed 16 paired cases plus two excluded warmups before any capacity inference. First eight cases ran at 1 then 4; last eight at 4 then 1. Each pair sent identical request bytes asking for a 64-integer arithmetic sequence, with temperature 0, top_p 1, seed 9072719, reasoning none and 1024 output tokens. Server settings and loaded context were unchanged throughout all six batches.

Three audit unit tests and a 34-request synthetic-transport end-to-end fixture passed with zero real inference before launch. The actual run completed 34 requests in 511.11 seconds. The separate auditor reread retained request/response bytes, hashes, exact integer sequences, timing intervals and cleanup ordering. Client overlap reached 4; this does not prove parallel GPU execution. All final responses were terminal, the exact owned model was unloaded, and the final catalog was empty.

No inference overlapped with the accuracy matrix. The earlier skipped capacity test remains untouched. There were no service restarts, deployment or model-setting changes. Private response bodies and operational catalog details are retained outside this publication.

No GPU/system-RAM telemetry was collected, so this test cannot attribute the slowdown to memory spill, device saturation, scheduling or competing work. The small synthetic sequence task has poor answer accuracy in both configurations; the measurements must not be generalized to all models, context lengths or tasks.

Protocol SHA256: `24fb49ac2f7d0812260a472328b220f35f6cd291dd4609ea700adfb05028019e`.

[Aggregate audit](backend-concurrency-20260907.json) · [Frozen protocol](backend-concurrency-20260907.protocol.json) · [Native accuracy comparison](native-contract-matrix-20260907.md)
