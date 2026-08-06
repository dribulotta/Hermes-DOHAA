# Origin and theoretical foundations

This document explains where DOHAA comes from, what the term means, and how this repository relates to the underlying architecture. It is intentionally separate from the implementation guide: the theoretical pattern is broader than the current codebase.

## Source and terminology

The architecture was proposed by **Dante Guillermo Ribulotta** in the Spanish-language paper:

> *Arquitectura Agéntica Híbrida con Orquestación Determinista (AAHOD): un patrón para sistemas de inteligencia artificial confiables, auditables y de calidad estable.* Preprint v1.0, July 27, 2026. DOI: [10.5281/zenodo.21628049](https://doi.org/10.5281/zenodo.21628049).

The paper gives the English formulation **Deterministically Orchestrated Hybrid Agentic Architecture**, abbreviated **DOHAA**.

| Language | Full name | Acronym |
|---|---|---|
| Spanish | Arquitectura Agéntica Híbrida con Orquestación Determinista | AAHOD |
| English | Deterministically Orchestrated Hybrid Agentic Architecture | DOHAA |

AAHOD and DOHAA refer to the same architectural pattern. This repository uses the English acronym because its code and primary technical documentation are written in English.

## Operational genesis

According to the paper, AAHOD was not initially conceived as a taxonomy or as a deliberate application of a theory of agency. It emerged during the iterative construction of a continuous system that had to process large volumes of unstructured information, select the relevant fraction, and produce consistent analytical outputs.

The early design used generative AI broadly. Operational experience exposed a recurring problem: more acquired information produced greater token consumption, queue pressure, latency, and cost without a proportional increase in useful output. The resulting design decisions accumulated into a coherent architecture:

- prefiltering responded to inference budgets;
- orchestration responded to operational continuity;
- explicit contracts responded to model variability;
- guardrails responded to risk;
- external memory responded to recovery requirements;
- consolidation responded to fragmentation;
- deterministic presentation responded to editorial stability.

The paper describes this history as **design evidence**, not as a controlled experiment.

## Public antecedent: the tri-layer architecture

The first public formulation in this line of work was a technical note initially published on GitHub on May 29, 2026 and later preserved on Zenodo:

> Dante Guillermo Ribulotta. *Arquitectura tri-capa para agentes autónomos: separar ejecución determinista, motor de IA local e IA cloud para reducir tokens, latencia y exposición de datos.* Version 1.0, Zenodo, 2026. DOI: [10.5281/zenodo.21628570](https://doi.org/10.5281/zenodo.21628570).

That note separated deterministic execution, local AI processing, and advanced cloud reasoning. AAHOD generalizes the earlier solution: the central question is no longer only where inference runs, but **where authority resides** across control, cognition, assurance, evidence, action, and human oversight.

## Formal definition

The paper defines the system as:

`S = <O, P, F, G, V, M, A, H>`

| Symbol | Component | Responsibility |
|---|---|---|
| `O` | Deterministic orchestrator | Owns the operational objective, state, sequence, budgets, and control flow |
| `P` | Perception | Acquires or recognizes information from authorized inputs |
| `F` | Filtering | Applies conventional rules and transformations before expensive cognition |
| `G` | Generative module | Interprets, classifies, synthesizes, or proposes |
| `V` | Validators and guardrails | Check outputs independently from generation |
| `M` | External persistent memory | Preserves durable state outside ephemeral model context |
| `A` | Controlled actuators | Store, notify, publish, or otherwise affect external systems |
| `H` | Human authority | Defines policy and retains final authority when risk requires it |

Its distinguishing authority rule is that the deterministic orchestrator controls the process, while probabilistic cognition is bounded to perception and generation. Model output may influence a decision, but it does not possess unrestricted authority over the process.

## Four architectural planes

AAHOD/DOHAA organizes these components into four complementary planes:

1. **Control plane:** objectives, state machines, queues, retry budgets, limits, priorities, and recovery.
2. **Cognitive plane:** perception, extraction, semantic interpretation, classification, synthesis, and drafting.
3. **Assurance plane:** schemas, deterministic checks, cross-validation, policies, confidence thresholds, and publication gates.
4. **Evidence plane:** inputs, intermediate results, timestamps, policy versions, hashes, provenance, and audit history.

The assurance plane must be conceptually independent from the generator. Asking the same model to generate and approve its own result does not create independent assurance.

## Relationship to Hermes Agent

The paper is deliberately independent of vendors, models, infrastructure, data sources, and organizations. **Hermes Agent is therefore an implementation choice, not part of the definition of DOHAA.**

In this repository:

- Hermes supplies the initial cognitive runtime;
- the DOHAA controller owns task contracts, budgets, state transitions, retry, and escalation;
- deterministic gates evaluate structural and policy properties;
- the evidence ledger persists events outside model context;
- future actuators must remain behind capability and approval gates;
- candidate changes from Hermes or self-evolution systems must pass protected evaluation before promotion.

Hermes-DOHAA is one reference implementation. Other runtimes may implement the same `AgentRuntime` boundary without changing the architectural principle.

## Current implementation coverage

| Component | Current status | Important limitation |
|---|---|---|
| `O` Orchestrator | Initial implementation | Budgets cover attempts; time, cost, and concurrency budgets remain future work |
| `P` Perception | Represented through contract inputs and runtime evidence | No dedicated acquisition subsystem yet |
| `F` Filtering | Contract and schema validation | No domain-specific preprocessing pipeline yet |
| `G` Generation | Hermes HTTP API adapter with an isolated live integration path | The validated deployment demonstrates bounded integration, not semantic correctness or complete DOHAA conformance |
| `V` Validation | Structural evidence and action-policy gates | These gates do not prove the semantic truth of evidence |
| `M` Memory | Hash-chained SQLite ledger | Not an external transparency log and not yet an agent memory system |
| `A` Actuators | Not implemented | Deliberately excluded from v0.1 |
| `H` Human authority | Explicit approval boundary | Approval identity and authentication are not yet implemented |

This table prevents the repository from implying conformance with the complete architecture before the missing components and controls are implemented and tested.

## Scientific status and claims

The source paper is **preprint v1.0 and has not been peer reviewed**. It contributes a name, formal vocabulary, design invariants, an operational reconstruction, and an empirical evaluation protocol.

The paper explicitly distinguishes provenance from proof:

- the operational history explains why the architecture was adopted;
- it does not establish a specific magnitude of savings or superiority;
- empirical validation requires comparisons of accuracy, stability, cost, recovery, and traceability against alternative architectures.

Hermes-DOHAA follows the same rule. Repository documentation and experiments must distinguish:

1. architectural definitions;
2. implementation facts;
3. observed experimental results;
4. hypotheses that remain unvalidated.

## Citation

```text
Ribulotta, Dante Guillermo. Arquitectura Agéntica Híbrida con Orquestación
Determinista (AAHOD): un patrón para sistemas de inteligencia artificial
confiables, auditables y de calidad estable. Preprint v1.0, 2026.
https://doi.org/10.5281/zenodo.21628049
```

Machine-readable citation metadata is available in [`CITATION.cff`](../CITATION.cff).
