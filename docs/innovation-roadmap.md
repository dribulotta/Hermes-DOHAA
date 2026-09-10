# Hermes-DOHAA innovation and evaluation roadmap

Owner: Dante Guillermo Ribulotta. Prepared with Codex, 2026-09-09.
Tracking issue: [#72](https://github.com/dribulotta/Hermes-DOHAA/issues/72).
Status: first development increment implemented; independent maintainer review
and a native continuous-workflow comparison remain pending.

## Starting evidence and decision rule

The closed fixed-budget study [#70](https://github.com/dribulotta/Hermes-DOHAA/issues/70),
reported in [#71](https://github.com/dribulotta/Hermes-DOHAA/pull/71), found direct
111/192, strong simple repair 162/192 and DOHAA 160/192 correct configurations.
DOHAA did not meet the registered incremental-utility criterion. The visible-rule
control solved all 32 cases without an LLM. These observations stay closed and
unchanged; they do not demonstrate general agency, learning, or universal failure.

The next program tests distinct, explicitly named hypotheses. The goal is to
decide which DOHAA components justify their complexity. A neutral, negative or
inconclusive result is a valid deliverable. A new task or budget cannot repair an
old result retrospectively. Operational origins in continuous information
processing motivate M1-M3; see [the foundations](origin-and-foundations.md).

## Ordered milestones

Milestones are acceptance gates, not calendar promises. Each receives a focused
issue/PR when implementation starts. A passed unit test is not an empirical win.

| Milestone | Innovation or question | Deliverable | Acceptance / decision |
| --- | --- | --- | --- |
| M0. Reproducible integration | Can we identify the exact system being compared? | Source/dependency manifest; resolve new review defects; integrate approved changes in an isolated copy. | Exact source identity, relevant Linux checks, no unexplained failure; maintainer merges remain separate. Tools-only M1 can proceed on main. |
| M1. Continuous-document world | Can the instrument detect stale, missing and unsupported information through updates and interruptions? | Durable synthetic stream, separate reference grader, public development demo and crash regressions. | Duplicate replay is idempotent; revision/expiry/retraction and checkpoint visibility match independent expectations; invalid answers fail. **Initial implementation is in this PR.** |
| M2. Native workflow comparison | Does deterministic orchestration improve complete, sustained tasks over a strong simple pipeline? | Isolated real Hermes adapters, paired development pilot, then a separately frozen fresh comparison. | Account for every attempt; verify reasoning modes, budgets and exact unload; evaluate whole workflows and quality/cost jointly. No superiority claim from calibration. |
| M3. Useful filtering and memory | Can filtering, provenance, expiry and consolidation reduce work without losing critical information? | Versioned memory with source lineage, deduplication, freshness policy, auditable filtering and deterministic report formatting. | Compare full/no-filter/no-memory variants to a simple pipeline with equivalent capabilities. Count discarded important information, unsupported synthesis, report stability, queue delay and total cost. Savings alone do not pass. |
| M4. Controlled tools and recovery | Can the system finish a multi-step workflow despite partial failures and changing state? | Local actuator simulator, operation ledger, bounded retries, reconciliation, restart and rollback cases. | Correct final state; no forbidden or duplicate effects under injected faults; recovery time and manual interventions recorded. Do not infer exactly-once external execution from SQLite transactions. |
| M5. Untrusted-data resilience | Can source documents redirect authority or suppress legitimate work? | Clean/attacked pairs, mixed trusted/untrusted sources, explicit tool capability boundary. | Attack success, data exposure, unsafe acceptance and legitimate completion/false blocks measured together. Both arms receive equivalent protections. |
| M6. Adaptive compute | When should the system verify, retry, reason longer or switch models? | A bounded routing policy and full accounting for generation, verification, loading/unloading and overhead. | A quality/cost frontier against fixed policies, matched resources, correct error classification and fresh validation. More tokens or a bigger model alone is not an architectural gain. |
| M7. Transferable system learning | Does persistent validated experience improve new tasks without retaining obsolete beliefs? | Versioned/revocable memory, training-only candidate construction, shadow evaluation and exact reversion. | Improvement on withheld families; no protected-data leakage, stale-memory regression or unapproved activation. This is system learning, not model-weight training. |
| M8. External and sustained validation | Does a supported contribution generalize beyond our templates? | Independent task families, external benchmark adaptation, bounded long runs and release readiness report. | Prespecified success criteria, uncertainty, failure/cost logs, resource limits, recovery and reviewed rollback. No V5 publication or production deployment solely on benchmark success. |

M3-M7 require the M2 measurement boundary. M4 and M5 can be developed separately
once its simulator interfaces are stable. M6 follows measured budget calibration.
M7 follows a stable memory design and uses separate training/validation/test data.
M8 tests only contributions with a stated reason to continue. Stop or simplify an
unhelpful module; do not keep expanding it to seek a favorable benchmark.

## First execution sequence

1. Land for review the offline M1 harness and its public controls. The demo's
   six reference checkpoints and two negative controls are grader checks only.
2. Implement the smallest native slice: one synthetic document workflow with
   updates, irrelevant text, late old revisions and independent expected facts.
   Start with a declared development canary to validate transport and accounting.
3. Expand the development pilot to multiple semantic families: logistics changes,
   support-policy updates, incident timelines and inventory notices. These are
   proposed synthetic domains, not actual customer data or ready holdouts.
4. Give direct, strong simple and DOHAA arms the same task information and relevant
   capabilities. Test one change at a time; preserve a no-LLM control where useful.
5. Use pilot variance, ceiling/floor behavior, failure rates and measured runtime
   to choose the confirmatory sample size and budgets. Review annotation ambiguity.
6. Freeze protocol, source, fresh task families, expected answers, randomization,
   stopping rules and analysis before the first confirmatory model call. Retain
   commitments separately from execution; state the actual retention boundary.
7. Run the bounded comparison, audit once, publish all results, and decide whether
   M3/M4 refinements are justified. Larger scope is not automatic after a failure.

## Common experimental contract

- **Primary unit:** a complete independently generated workflow. Checkpoints,
  models, reasoning modes and repetitions are correlated repeated observations,
  not additional independent cases. Analyze pairing/clustering at workflow level.
- **Quality:** correct supported task completion, critical omissions, stale facts,
  unsupported claims, false blocks, permitted/forbidden effects and stability.
- **Resources:** actual requests, input/output/reasoning tokens when observable,
  end-to-end elapsed time, queue/verification/load/unload overhead and peak memory
  when measured. Do not report inferred energy or unseen token counts as measured.
- **Baselines:** direct Hermes as context; the strong simple pipeline is the main
  comparator. Share relevant validators, tools, memory and retry allowances.
  Document deliberate ablations, different costs and information access exactly.
- **Sampling:** separate development from fresh confirmatory cases; include new
  families, difficult cases, negative controls and externally designed tasks.
  Review reference correctness independently of candidate outputs. No same-model
  self-judging as independent assurance. Exact values do not grade prose quality.
- **Inference operations:** one active client request and one owned model instance
  at a time. Confirm terminal completion, unload that exact model after its block,
  verify release before switching. Never unload an unrelated instance. Unknown
  completion stops the affected campaign for diagnosis without blind resubmission.
- **Reasoning:** compare supported no-reasoning/reasoning modes, verify effective
  settings, calibrate limits on development data and freeze them prospectively.
  Include terminal budget failures in outcomes; preserve infrastructure unknowns.
- **Claims:** preregister the primary endpoint, minimum worthwhile effect,
  non-inferiority margin for quality if claiming savings, statistical method,
  multiplicity handling and resource ceiling. Do not pick numerical margins from
  confirmatory results. Report intervals, denominators, missing data and limits.

## Review, publication and operational boundary

Autonomous work covers development copies, synthetic tests, issues, branches and
draft PRs. The maintainer reviews and merges. Policy/verifier/reference-integrity
changes follow CONTRIBUTING.md. Existing PR dependencies must be checked before
retargeting; no automatic merge is implied by this roadmap.

Real actuators, installed runtime changes, service restarts, firewall changes,
production deployment and V5 publication are separate approval boundaries.
Never publish private prompts, answers, cases, credentials or histories. Use
finite authorized summaries. Continue the existing scheduled task on the next
unfinished milestone and notify meaningful advances, failures or necessary action.

## Research references for design, not evidence of DOHAA performance

- [AgentDojo](https://arxiv.org/abs/2406.13352): tool agents processing untrusted
  data; motivates paired attack resistance and legitimate-utility measurements.
- [tau-squared-bench](https://arxiv.org/abs/2506.07982): shared changing environments;
  motivates separating task reasoning from coordination and state errors.
- [TheAgentCompany](https://arxiv.org/abs/2412.14161): realistic professional tasks;
  motivates external workflow validation beyond local templates.
- [Scaling LLM Test-Time Compute Optimally](https://arxiv.org/abs/2408.03314):
  motivates difficulty-aware compute allocation, to be tested independently here.
