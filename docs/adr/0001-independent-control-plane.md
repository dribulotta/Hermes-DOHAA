# ADR-0001: Build DOHAA as an independent control plane

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

Hermes Agent evolves quickly and already provides a mature agent loop, tools, sessions, multiple protocols, plugins, memory, and a separate self-evolution project. Directly forking or deeply subclassing its core would couple DOHAA governance guarantees to upstream implementation details.

## Decision

Hermes-DOHAA will be an independent repository and Python package. It will integrate with Hermes through documented external protocols. The first adapter uses the OpenAI-compatible API; TUI Gateway JSON-RPC is the preferred future adapter for fine-grained lifecycle and approval events.

Hermes plugins may provide telemetry, context, and convenience tools. They are not hard assurance gates because extension failures must not silently remove the controller's safety boundary.

## Consequences

- Upstream Hermes can be upgraded independently and tested through compatibility checks.
- DOHAA can support other cognitive runtimes behind the same `AgentRuntime` protocol.
- Hard policy remains outside the model process.
- Integration tests must track upstream protocol changes.
- Deployment requires two clearly configured trust domains: Hermes runtime and DOHAA supervisor.
