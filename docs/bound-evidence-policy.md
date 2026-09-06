# Contract-owned evidence and factual claim bindings

For tasks needing stronger checks than evidence IDs, the contract author can set
`inputs.evidence_policy`. `ClaimEvidenceGate` then checks every source commitment
and every specified factual claim, including after deterministic result repair.
This works through the existing controller, CLI and evaluation gate configuration.
Custom controller integrations must include `ClaimEvidenceGate` to enforce it.
The agent cannot opt out by omitting evidence or returning empty claims.

This is an explicit mode. Contracts without this field retain their existing
structural-only evidence checks. They do not gain source or claim verification
merely by upgrading the code. A present but invalid policy, including `null`,
is rejected; it never disables the stronger checks silently.

## Trust boundary

The contract author must obtain the source records and commitments from an
authorized, independently trusted process before the agent runs. Do not accept
an agent's proposed hash as the trusted commitment. The source string is an
opaque label checked for equality, not a URL the gate fetches or authenticates.

The gate verifies consistency with those commitments. It does not prove that the
original source is truthful, authenticate a server or person, inspect provenance,
or judge arbitrary natural-language assertions. The author also owns the fixed
wording of each claim. Matching the wording does not independently validate its
meaning. Other result fields still need their own result specifications and
semantic assertions.

## Policy shape

The complete runnable contract and matching proposal are in
[`task_contract_bound_evidence.json`](../examples/task_contract_bound_evidence.json)
and [`proposal_bound_evidence.json`](../examples/proposal_bound_evidence.json).
They use newly authored synthetic stock data. No model request is needed to
validate the contract:

```bash
PYTHONPATH=src python -m hermes_dohaa.cli validate examples/task_contract_bound_evidence.json
```

The policy object requires exactly these fields:

- `schema_version`: the string `1.0`.
- `sources`: 1–32 objects, each containing exactly `evidence_id`, `kind`, `source`
  and `sha256`. IDs are unique. The digest is 64 lowercase hexadecimal digits.
- `claims`: 1–32 unique bindings, each containing exactly `evidence_id`,
  `evidence_pointer`, `result_pointer`, `prefix` and `suffix`. Each binding refers
  to one committed source.

IDs have at most 128 characters; source labels and kinds at most 512. These fields
must be nonempty and have no leading or trailing whitespace. Prefix and suffix
are literal strings of at most 512 characters each and may be empty. Pointers
are valid JSON Pointers of at most 1024 characters; the empty pointer selects the
whole value, and `~0` and `~1` escape a tilde and slash. All policy strings must
be valid Unicode.

The content digest is SHA-256 of the source's JSON representation encoded as UTF-8:
sorted object keys, no extra whitespace, Unicode retained, and no non-finite
numbers. It matches `EvidenceItem.create` for supported finite JSON values.
The gate recomputes the actual content digest and checks it against both the
item's digest and the contract's independently supplied commitment. It also
requires exact ID, kind and source-label matches. Every committed source must be
present; extra sources are rejected.

For each claim binding, the source pointer selects a value from that committed
evidence. The result pointer must select the same JSON value, including its type
and nested structure. The required claim statement is:

```text
prefix + canonical JSON of the source value + suffix
```

For stock `29`, prefix `Stock is ` and suffix `.`, the statement is `Stock is 29.`.
String values retain JSON quotes: a region value `west` renders as `"west"`.
The claim must reference exactly that binding's single evidence ID. Every bound
claim is required, and additional or duplicated claims are rejected. Source and
claim ordering may differ from the contract. Bindings that render the same
statement and evidence reference twice are rejected as ambiguous. Choose literal wording without
leading or trailing whitespace, consistent with proposal statement parsing.

## Repair, diagnostics and approval

The repair mechanism continues to edit only the permitted result values. It
does not rewrite or delete claims or source evidence. After a repair, the same
gate runs again. A corrected result with a stale contradictory claim cannot
pass the bound mode. A repair whose existing claim already matches the trusted
value can still pass. Existing action restrictions and human approval rules
continue to apply.

Failures use `evidence.policy_invalid`, `evidence.binding_mismatch`, or
`evidence.claim_binding_mismatch`. Existing missing-reference and duplicate-ID
failures remain available. The new failure feedback contains no source content,
source label, expected claim statement or expected value. This does not sanitize
the private ledger, which still records the actual contract and proposal.

The new policy module is part of the controller identity used by approval
checkpoints. A code change changes that identity, so old checkpoints require
fresh verification and approval rather than silently inheriting authorization.

This mode imposes bounds on policy definitions, not a total response-content
size limit. Its success is scoped verification of committed facts; it is not
proof of general correctness, persistent learning or safe autonomous actuation.
