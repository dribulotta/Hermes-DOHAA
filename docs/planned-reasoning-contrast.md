# Check the planned intervention before spending inference

Three strategy names do not guarantee three different assignments. An adaptive
rule can choose the same fixed profile for every task. Different matched-rule
labels or identical token caps do not change that conclusion. The standalone
`tools.planned_reasoning_contrast` module diagnoses this before dispatch.

## API and trusted inputs

`diagnose_contrast` accepts the existing planner's original workload, profile
library, rule-table bytes and their independently retained SHA-256 commitments,
plus one shared token ceiling. `plans` must contain exactly `adaptive`,
`fixed-none` and `fixed-medium`. Each value has exactly `document_bytes` and
`document_sha256`, referring to a previously retained canonical plan.

The diagnostic calls the existing `validate_plan` for each named strategy with
the same original inputs. This recomputes every reservation and requires exact
plan equality, including task/input identities, ordering, request opportunities,
profile settings, limits and source measurements. Rehashing an altered plan is
not sufficient. Missing strategies, plans from another workload, swapped labels,
nonfactor-setting changes and inconsistent planner source measurements reject.
Invalid-input diagnostics do not interpolate private values.

The caller must protect the original commitments. Substituting both a plan and
all its supposed originals is outside what these hashes can authenticate. Host
metadata remains declared input, not independent proof of facts, reference
quality or legitimate feature construction. A static source measurement does
not attest a running model or guarantee an unmodified execution environment.

## Interpreting the report

The function returns canonical JSON bytes with schema
`hermes-planned-reasoning-contrast/1.0`. It compares selected complete policy
commitments for each ordered task, ignoring strategy and matched-rule labels.
For each fixed baseline it reports changed and unchanged task counts. If any
fixed baseline matches the adaptive assignment for the entire workload, status
is `contrast_absent` and `identical_to` names it. Otherwise status is
`contrast_present`. Counts refer to tasks, not independent observations of quality.

A present contrast is only a planned profile difference. It does not show a
useful difference, causal reasoning effect, lower cost, quality retention or
DOHAA superiority. Profile differences can include generation caps; any study
must specify which factors it intends to vary and justify their fairness.
The report binds the original inputs, three plans, planner and diagnostic source
measurements. It explicitly leaves execution authorization, source admission,
effective-mode attestation, quality claims and measured savings false.

The same request IDs occur in the three counterfactual plans. They prove equal
planned opportunities here; they are not three live dispatch permissions. The
report retains the per-strategy request count and does not add these reservations
into observed usage. It makes zero native requests. A live experiment needs its
own disjoint request identities, state, ownership, paired-input evidence and
complete costs; this report cannot be used as its runtime receipt.

## Scope and checks

Thirteen fresh synthetic tests cover mixed and constant assignments, unchanged
policies with different rule labels, altered/rehashed/unpaired plans, missing
baselines, changed commitments/settings, bounds, source-measurement changes,
request accounting and absence of model/network/process actions. They read no
historical cohort and do not provide scientific cases or results.

The existing planner and published source profiles remain unchanged. No model
transport, selector rule, benchmark, candidate, installed service or closed study
is modified. This diagnostic neither admits a new source nor resolves binary
reasoning transport compatibility. It is an additional prospective design check,
not an automatic launch gate or a reason to replay completed evaluations.
