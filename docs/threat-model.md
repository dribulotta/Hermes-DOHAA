# Threat model

## Assets

- task contracts and approval policy;
- verifier code, golden cases, and hidden holdouts;
- evidence records and source artifacts;
- secrets and provider credentials;
- actuator capabilities and external systems;
- promoted skills, prompts, and code.

## Trust boundaries

The following are untrusted by default:

- model output, including self-critique and confidence claims;
- tool output and web content, which may contain prompt injection;
- agent-written tests and synthetic evaluation data;
- candidate memories, skills, prompts, policies, and code;
- identifiers or evidence supplied only by the model.

Deterministic code, protected configuration, and independently obtained source hashes form the initial trusted computing base. They are still subject to conventional software defects and require tests and review.

## Principal threats and controls

| Threat | Initial control | Residual risk |
|---|---|---|
| Agent authorizes its own action | Controller owns state and action allowlist | Hermes tools must also be sandboxed externally |
| Prompt injection changes policy | Policy is not stored in the model prompt alone | Semantic attacks can still influence proposals |
| Fabricated evidence | Claim/evidence reference gates and hashes | v0.1 does not independently validate evidence content |
| Infinite repair loop | Hard attempt budget and repeated-proposal detection | Near-duplicate proposals need stronger similarity checks |
| Evaluator gaming | Protected validators and hidden holdouts | Test leakage and proxy overfitting remain possible |
| Ledger tampering | Hash chain and integrity verification | SQLite is not an external transparency log |
| High-risk autonomous action | Explicit human-approval boundary | Identity and approval authentication are future work |
| Plugin hook failure | Hooks are telemetry/soft policy only | Hard gates must stay outside fail-open extension points |

## Fail-closed requirements

DOHAA must stop or escalate when:

- the contract is invalid or unsupported;
- no assurance gate is configured;
- the cognitive runtime fails;
- any requested action is forbidden or undeclared;
- required evidence is absent;
- the same proposal repeats;
- the attempt budget is exhausted;
- human approval is required but unavailable;
- ledger integrity validation fails.

## Non-goals of v0.1

- secure remote attestation;
- authenticated multi-party approval;
- containment of an unsandboxed Hermes process;
- proof that evidence content is true;
- safe autonomous mutation of active code or policy;
- deterministic model generation.
