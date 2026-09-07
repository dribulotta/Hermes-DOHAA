# Native adapter live-provider canary — 2026-09-07

This prospective development canary exercised the PR #52 `NativePromptAdapter`
with the installed Hermes `AIAgent` and the authorized local LM Studio provider.
It used four new controls and the product's strict response admission/scoring.
It was not a paired candidate collection or a learning experiment.

## Preregistered boundaries

The protocol and all five private driver/auditor helper sources were committed
in a manifest. Its hash was independently saved before dispatch. The integrated
source was `d77afda7d94230df0ada2f0eb7fbfdf7a22a3147`, tree
`0a916dbb9f05ff5e9e0d175ace712bc838f07ff7`, incorporating PRs
#42/#44/#46/#47/#49/#51/#52. The PR #52 head at registration was
`9d53099bfe574a57ba771a099a7101f55217ab36`. This report changes documentation only.

- Manifest: `450b6d03529f446ab8c90b18264932da13fbe8e6bfc39b1501702ddbfa074447`.
- Protocol: `1b8328d0953cb3fa453f9d95cd3202e2f9327d1e383b8d17332d680e1ffa6fb0`.
- Exactly four planned generations: two `none`, then two `medium` reasoning.
- Serial client, zero retries, 8192 requested tokens per call, temperature zero,
  top-p one, fixed seed per block, 1200-second wall budget.
- Fresh restricted worker profiles under an otherwise unused nonprivileged
  identity, empty supplementary groups and an actual private-file denial probe.
- Verify initial empty catalog; unload only observed owned instances after
  verified terminal completion of each block, then verify an empty catalog.
- Require all four responses admitted, exactly correct and action-free for
  response success. Report integration checks separately.

A preparation-only attempt required a configured provider key and stopped before
registration or generation. The existing local endpoint permits keyless access;
read-only access was verified and the SDK received a nonsecret placeholder.
The corrected preparation used a new directory before the final registration.
No authentication settings or deployed configuration were changed.

## Observed results

All **17 integration checks passed**. The canary made exactly **four real
generation requests**, completed in **85.43 seconds**, and retained all four
terminal responses. Captures bound the logical request, requested model and
parameters, actual native worker identity/profile and terminal wire response.
Native completion/content consistency, one generation per worker, sealed
finished profiles and owned-instance unload after both blocks were verified.

The separate response criterion **failed: 3/4 correct**.

| Block | Controls | Correct | Admission failures | Proposed actions |
| --- | ---: | ---: | ---: | ---: |
| No reasoning | 2 | 1 | 1 | 0 |
| Medium reasoning | 2 | 2 | 0 | 0 |

The failed control returned a terminal JSON object without the required
`result`/`actions` structure. It remained `invalid_response`; no repair, retry or
rescoring was performed. This structural diagnosis was posthoc and did not
change the preregistered verdict. The two blocks used different controls and
cannot establish a comparative reasoning benefit.

The supervisor finished. An independent read-only environment check found zero
loaded model instances, unchanged service process identities/start times and
the unchanged deployed revision. Raw inputs, oracle answers, responses, profiles,
provider details and transport logs remain private.

## Interpretation and remaining work

This is bounded live-provider compatibility evidence for the development
adapter, including preservation of a real format failure. It is not evidence of
useful persistent learning, general model superiority, cryptographic execution
attestation, production readiness or authority to activate a candidate. The
reported requested token cap is not independent GPU/provider-compliance
telemetry. No previous campaign was rerun or rescored. All candidates remain
quarantined; no deployment, service restart, activation or merge occurred.

Controlled development adoption and reversal remain the next implementation
step. The prior negative prompt pilots remain negative.
