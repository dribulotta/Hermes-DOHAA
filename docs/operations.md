# Operations runbook

This runbook covers routine operation and recovery for an isolated
Hermes-DOHAA deployment. Commands and service names are examples and must be
adapted to the target environment.

## Operating principles

- Treat every model response as untrusted input.
- Keep the deterministic controller and cognitive runtime in separate trust
  domains.
- Do not weaken isolation to restore availability.
- Preserve evidence before modifying or restarting a failed component.
- Require explicit human authorization for high-risk changes.
- Re-run positive and negative probes after every material change.

## Routine health checks

Verify the controller repository and package:

    git status --short
    git rev-parse HEAD
    python -m unittest discover -s tests -v
    hermes-dohaa --help

Verify the runtime service:

    systemctl is-enabled hermes-runtime.service
    systemctl is-active hermes-runtime.service
    systemctl status hermes-runtime.service --no-pager --full
    ss -ltnp

Verify the host isolation service and active rules:

    systemctl is-enabled hermes-dohaa-runtime-firewall.service
    systemctl is-active hermes-dohaa-runtime-firewall.service
    nft list table bridge hermes_dohaa_runtime
    nft list table inet hermes_dohaa_runtime_routed

Service status alone is not proof that traffic is correctly filtered. Run
network probes from authorized and unauthorized sources.

## Authenticated API probe

The controller should be able to request `/v1/models` with its bearer token.
The endpoint should expose only the intended runtime alias.

An unauthenticated request must fail. Authentication and network isolation are
independent controls and both must be tested.

Do not print bearer tokens in diagnostic output.

## Official smoke test

Run the non-mutating integration test with a unique ledger:

    HERMES_API_KEY="$(cat /path/to/runtime-api.key)" \
    hermes-dohaa smoke \
      --hermes-url http://192.0.2.106:8642/v1 \
      --hermes-model dohaa-runtime \
      --reasoning-effort none \
      --hermes-timeout-seconds 120 \
      --ledger /var/lib/hermes-dohaa/smoke-TIMESTAMP.sqlite3

A successful result must confirm:

- status is `succeeded`;
- all deterministic gates passed;
- the nonce and marker were verified;
- no action was requested;
- the ledger chain is valid.

Retain the command result and ledger as separate evidence artifacts with
restricted permissions.

## Negative capability probes

At minimum, verify that:

- an unrelated client cannot connect to the runtime API;
- the infrastructure host cannot connect unless allowlisted;
- the runtime cannot reach general Internet addresses;
- the runtime cannot reach unrelated internal services;
- the runtime can reach only the configured model endpoint;
- a hostile prompt cannot create a file;
- the API-created agent exposes zero tools;
- the API-created agent has no memory manager;
- the persistent configuration still denies all installed toolsets.

A negative probe passes only when the prohibited effect is independently
checked. A model statement such as `file_created: false` is not evidence by
itself.

## Offline ledger verification

Verify an archived ledger without contacting the cognitive runtime:

    hermes-dohaa verify-ledger /path/to/evidence.sqlite3

To report the number of events associated with one run:

    hermes-dohaa verify-ledger /path/to/evidence.sqlite3 \
      --run-id RUN_ID

The complete ledger chain is always verified. `--run-id` filters only the
reported event count because events from all runs share one hash chain.

The command emits one JSON object and uses deterministic exit codes:

| Code | Meaning |
|---:|---|
| `0` | The complete chain is valid and the optional run exists |
| `1` | An integrity violation was detected |
| `2` | The file, SQLite database, schema, or argument is invalid |
| `3` | The complete chain is valid but the requested run does not exist |

Verification is read-only and must not create a missing database. The verifier
requires a quiescent archived snapshot and rejects databases accompanied by
SQLite `-wal` or `-shm` files. Create the snapshot using an operationally safe
checkpoint or backup procedure; do not detach a live database from its WAL.

A valid hash chain cannot independently detect replacement of the entire ledger
or deletion of an unanchored chain tail. External backups or trusted anchors
remain necessary for stronger guarantees.

## Evidence handling

Evidence ledgers should be:

- owned by the controller service account;
- readable only by authorized operators;
- stored outside model-accessible paths;
- backed up according to retention policy;
- verified before and after copying;
- associated with controller, runtime, policy, and deployment versions.

The SQLite hash chain detects changes to recorded events but is not an external
transparency log. A privileged operator may still replace an entire ledger.

## Updating the controller

Before updating:

1. ensure the worktree is clean;
2. record the current commit;
3. fetch the intended branch;
4. review the diff;
5. run the complete test suite;
6. preserve the previous install artifact or commit.

After updating:

1. reinstall the package from the reviewed source;
2. run the complete test suite again;
3. run the authenticated API probe;
4. run the official smoke test;
5. verify the ledger;
6. record the new commit and evidence paths.

Do not use an unreviewed moving branch as a production dependency.

## Updating the cognitive runtime

Pin the Hermes source revision and dependency lock. Keep source and virtual
environment ownership with root and deny writes by the runtime account.

After any Hermes, Python, dependency, model, prompt, or policy change, repeat:

- constructed-agent inspection;
- zero-tool verification;
- memory-manager verification;
- authenticated API probe;
- negative capability probes;
- official smoke test;
- bounded repair-loop test;
- reboot certification.

## Secret rotation

To rotate the runtime API key:

1. stop or isolate the runtime API;
2. generate a new high-entropy key;
3. update the root-managed runtime environment;
4. update the controller credential file;
5. verify ownership and permissions;
6. restart the runtime;
7. verify that the new key succeeds;
8. verify that the old key fails;
9. run the official smoke test;
10. securely remove temporary copies.

Never pass a real key as a command-line argument when a protected file or
service credential mechanism is available.

## Firewall changes

Before applying a firewall change:

- preserve the current ruleset;
- validate syntax;
- identify the exact ingress and egress interfaces;
- confirm which network hooks the traffic traverses;
- prepare an out-of-band recovery path.

After applying it, test both allowed and denied flows. Inspect counters to
confirm which rule handled each probe.

## Reboot certification

After rebooting the infrastructure or isolated runtime:

1. confirm the workload started;
2. confirm the runtime service is enabled and active;
3. confirm the firewall service is enabled and active;
4. inspect the loaded rules;
5. verify authenticated controller access;
6. verify unauthorized ingress is blocked;
7. verify restricted egress;
8. run the official smoke test;
9. verify and retain the resulting ledger.

A deployment is not considered persistent until this sequence passes.

## Incident response

If a gate, runtime, or isolation check fails:

1. stop new controller runs;
2. preserve logs, ledgers, versions, and active rules;
3. classify the failure independently from the cognitive runtime;
4. revoke or rotate credentials if exposure is possible;
5. restore the last known-good configuration;
6. repeat the complete certification sequence;
7. document the cause and corrective action.

Availability pressure must not convert a fail-closed control into fail-open
behavior.

## Rollback

Rollback targets should be immutable, identified by commit and configuration
version, and already certified.

A rollback is complete only when:

- the expected controller commit is installed;
- the expected runtime revision is active;
- configuration permissions are correct;
- network isolation is active;
- positive and negative probes pass;
- a new smoke ledger verifies successfully.
