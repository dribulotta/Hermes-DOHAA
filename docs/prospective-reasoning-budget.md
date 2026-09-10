# Prospective reasoning and generation-budget planning

`tools/prospective_reasoning_budget.py` builds a deterministic reservation
manifest before model responses exist. It selects among validated native
profiles; it does not estimate task accuracy, load a model, dispatch a request,
execute an operation, repair a response or promote a runtime configuration.
Rules are experimental parameters, not proven difficulty classifiers.

## Inputs and provenance

All documents are canonical UTF-8 JSON with an externally supplied SHA-256
digest and a maximum encoded size of 1 MiB. Embedded documents use exactly
`document_base64` and `document_sha256`. Unknown fields, malformed identities,
noncanonical encodings and substituted digests are rejected.

The workload has `schema_version: hermes-budget-workload/1.0` and `tasks`, an
ordered list of 1–256 embedded task descriptors. A descriptor has exactly:

| Field | Meaning |
| --- | --- |
| `schema_version` | `hermes-budget-task/1.0` |
| `task_id` | Unique host task identity |
| `public_input_sha256` | Identity of the host's frozen public task input |
| `required_items` | 1–512 unique requested output-item identifiers |
| `source_records` | Up to 2,048 records, each with `source_id` and `revision` |
| `operation_steps` | Up to 128 unique planned operation-step identifiers |
| `planned_request_ids` | 1–8 unique prospective native-request identities |

Identifiers are nonblank, at most 128 UTF-8 bytes, and are never normalized or
interpreted as instructions. Task and request identities cannot be reused in a
workload. Revisions are integers from zero through 2^31−1 and must increase for
each source in the supplied sequence; duplicates and backward revisions fail.

`derive_features(descriptor_bytes, expected_sha256)` returns canonical feature
bytes. `required_items`, `source_records` and `operation_steps` count the
corresponding records. `revision_transitions` is the source-record count minus
the number of distinct sources: revisions 1 and 5 describe one observed change,
not four. Planned request count is separate and determines the reserved cap.
No strings are used as semantic difficulty labels. Changing an opaque name or
input digest changes identity but cannot select a different profile by itself.

The coordinator must derive these records from the requested contract, verified
host source history and declared workflow, before looking at model output or
private expected answers. This module checks structure and identities; it cannot
authenticate that a caller really verified the sources or truthfully counted
the task. A metadata digest is not proof of provenance against a malicious host.
Descriptors therefore require independent controls in any live protocol.

Do not include external-note instructions, route/condition labels, proposed
operations, model confidence, private references or grants. Neither the feature
schema nor rule language accepts those fields. Source payloads and expected
values are absent. Bytes, if measured elsewhere, must not be reported as tokens.

The public-input digest identifies the task input, not the final native wire
request. Existing collectors must still append real observations and bind every
derived request. This planner handles descriptors available before collection;
it does not predict observations that depend on future model actions.

## Profiles and rules

The profile library has `schema_version: hermes-reasoning-profiles/1.0` and
`profiles`, containing exactly `none` and `medium` embedded native policies.
Each passes the existing `validate_native_policy`, declares explicit context and
response contract, and uses the reasoning mode matching its label. Only
`reasoning_effort` and `max_tokens` may differ. Model, endpoint, context,
contract, worker settings, request allowance, seed and other execution settings
must match exactly. Task features cannot introduce another profile or mutate it.

The rule document has `schema_version: hermes-reasoning-rules/1.0`, a
`default_profile`, and up to 16 ordered `rules`. Each rule has exactly `rule_id`,
`feature`, `at_least` and `profile`. The feature is one of the four counts above;
the threshold is a bounded nonnegative integer; the profile is `none` or
`medium`. Rule identities are unique. The first matching threshold wins, with
the default used when none match. An empty rule list is valid. Thresholds are
supplied prospectively; this module contains no tuned production policy.

## Planning, reservation and validation

Call `build_plan` with keyword arguments `workload_bytes`, `workload_sha256`,
`profiles_bytes`, `profiles_sha256`, `rules_bytes`, `rules_sha256`,
`token_ceiling`, and optional `strategy`. Strategies are `adaptive`,
`fixed-none`, and `fixed-medium`. The fixed baselines preserve the workload and
charge their own profile caps. A token ceiling is an integer from zero to
2^31−1. Boolean and fractional budgets are invalid.

Each task reserves `len(planned_request_ids) * selected_policy.max_tokens`.
Request count cannot exceed that policy's validated request allowance. The
whole workload must fit the ceiling; otherwise planning fails without returning
a partial manifest, dropping cases or silently changing a choice. This is a
worst-case generation-token reservation, not measured usage, context capacity,
money, latency or VRAM. It does not refund unused or unknown resources.

The returned canonical manifest records ordered choices, matched rule, task and
input identities, feature/rule/library/profile/source digests, request identities,
per-task caps, total reserved allowance and remaining ceiling. It contains no
model responses, reference answers, source payloads or runtime credentials.
`validate_plan(raw, expected_sha256, **original_build_arguments)` recomputes it
against the original inputs; recomputing only a tampered manifest's hash is not
sufficient to validate it.

The coordinator must freeze the original input digests and manifest before any
dispatch, verify the selected full policy and actual request identities, and
use the existing ownership/no-replay collectors. A manifest grants no execution
authority and alone cannot enforce live spend or prove when it was created.
In particular, the native policy's minimum transport allowance of two is not a
second planned proposal or permission to retry a one-request task.

## Evaluation boundary

Regressions were specified before implementation. They cover multiple-request
reservations, fixed baselines, exhausted ceilings, ordered overlapping rules,
metadata/resource bounds, source revisions, substitutions, profile invariance,
and absence of network/model/process actions. Public fixtures are synthetic
development controls, not live cases or empirical evidence of improvement.

A later live comparison must freeze fresh varied tasks, independent feature and
answer controls, rules, order, budgets, quality tolerance and a cost criterion
before generation. Compare against both fixed baselines, including the stronger
one, and count all failed, unstarted and unknown outcomes. Measure actual usage
and complete runtime costs including load/unload. A shared selector is not a
DOHAA-specific contribution. If a fixed policy matches or dominates it, preserve
that negative result. Closed studies remain unchanged; no live study is created
by this module.
