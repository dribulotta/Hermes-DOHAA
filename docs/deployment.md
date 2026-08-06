# Isolated deployment guide

This guide describes a reference deployment that separates the deterministic
DOHAA controller from the untrusted Hermes cognitive runtime.

It is an example architecture, not a claim that a deployment is secure merely
because it follows these steps. Operators must validate the resulting controls
in their own environment.

## Trust boundaries

| Role | Responsibility | Authority |
|---|---|---|
| DOHAA controller | Owns contracts, transitions, gates, retry, escalation, and evidence | May accept or reject proposals |
| Hermes runtime | Produces untrusted proposals | Cannot approve itself or execute controller actions |
| Model server | Performs inference | Receives only traffic from the restricted runtime |
| Human operator | Approves exceptional or high-risk transitions | Retains final authority |

A recommended topology uses separate hosts, virtual machines, or containers:

- the controller can contact the Hermes API;
- the Hermes runtime can contact only its model server;
- unrelated clients cannot contact the Hermes runtime;
- the Hermes runtime cannot contact the controller, the Internet, DNS, or other
  internal services unless explicitly required;
- credentials are stored outside the repository.

The addresses in the examples use documentation-only networks from RFC 5737:

| Example role | Address |
|---|---|
| Controller | `192.0.2.105` |
| Hermes runtime | `192.0.2.106` |
| Model server | `198.51.100.10` |

Replace them with values appropriate for the deployment.

## 1. Prepare the controller

Install Hermes-DOHAA in a dedicated virtual environment:

    python3 -m venv /opt/hermes-dohaa/.venv
    /opt/hermes-dohaa/.venv/bin/python -m pip install /opt/hermes-dohaa

Store the Hermes API bearer token in a root-managed file readable by the
controller service account. Do not place it in Git, command history, logs, or
task contracts.

Suggested properties:

    owner: root
    group: controller service group
    mode: 0640

Validate the installation:

    /opt/hermes-dohaa/.venv/bin/python -m unittest discover \
      -s /opt/hermes-dohaa/tests -v

## 2. Prepare the Hermes runtime

Use a dedicated operating-system account with no interactive shell. Keep the
Hermes source and virtual environment owned by root and non-writable by the
runtime account.

The runtime profile used by the API must have:

- an empty API-server toolset;
- all installed toolsets explicitly disabled;
- tool-use enforcement disabled;
- a one-turn or otherwise tightly bounded generation budget;
- no memory manager;
- no writable access outside the minimum runtime state directory.

These controls must be verified from the constructed agent object.
Configuration inspection alone is insufficient.

## 3. Configure the API service

Copy `deploy/env/runtime.env.example` outside the repository and replace every
placeholder. Generate a random API key and never reuse the model-server
credential.

Install the systemd template after adapting its paths and account names:

    install -o root -g root -m 0644 \
      deploy/systemd/hermes-runtime.service.example \
      /etc/systemd/system/hermes-runtime.service

    systemctl daemon-reload
    systemctl enable --now hermes-runtime.service

The example assumes:

- account: `hermesrt`;
- home: `/home/hermesrt`;
- environment: `/etc/hermes-runtime/runtime.env`;
- virtual environment: `/opt/hermes-runtime/venv`;
- writable state: `/home/hermesrt/.hermes`.

## 4. Enforce network isolation

`deploy/nftables/runtime-isolation.nft.example` is an illustrative policy. It
must be adapted to the actual interfaces, addresses, routing path, and firewall
manager.

Test candidate rules before applying them:

    nft --check --file deploy/nftables/runtime-isolation.nft.example

Do not apply a remote firewall change without a tested recovery path.

## 5. Verify the deployment

Verification must include positive and negative probes:

1. the controller reaches the authenticated `/v1/models` endpoint;
2. the runtime reaches the model server;
3. an unrelated workload cannot reach the runtime API;
4. the host cannot reach the runtime API unless explicitly authorized;
5. the runtime cannot reach general Internet destinations;
6. the runtime cannot reach unrelated internal services;
7. a model request cannot create a file or produce a tool call;
8. the official `hermes-dohaa smoke` command succeeds;
9. the evidence ledger chain verifies;
10. the same checks pass after a reboot.

Example smoke invocation:

    HERMES_API_KEY="$(cat /path/to/runtime-api.key)" \
    hermes-dohaa smoke \
      --hermes-url http://192.0.2.106:8642/v1 \
      --hermes-model dohaa-runtime \
      --reasoning-effort none \
      --hermes-timeout-seconds 120 \
      --ledger /var/lib/hermes-dohaa/smoke.sqlite3

Passing this smoke test demonstrates connectivity, proposal parsing,
deterministic gates, and ledger integrity for that run. It is not a general
security or semantic-correctness proof.

## 6. Fail-closed expectations

The controller must escalate rather than continue when:

- the cognitive runtime is unavailable;
- authentication fails;
- the response is not valid proposal JSON;
- deterministic gates reject the proposal;
- the attempt budget is exhausted;
- the runtime repeats an earlier proposal;
- required human approval is absent.

Deployment hardening must not depend on prompt compliance.
