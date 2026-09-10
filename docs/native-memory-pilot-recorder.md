# Native memory pilot recorder

`tools.native_memory_pilot` supplies the specific terminal/lifecycle/scoring
connection needed by the new #114 pilot. It does not choose cases, launch a
campaign, change native source or introduce another provider transport.

`record_call` requires a fresh root-owned directory and the actual native document
adapter with the fixed structured-response contract and request strategy `none`.
It binds the original request/policy and an independently retained exact catalog
model identity, then uses the existing `ModelResidency` load/generation/terminal/
unload sequence. A durable start marker prevents another execution in that root.
The worker runs under the adapter's existing isolated identity and sealed profile.

The original protected trace and adapter return are reverified and retained with
a host witness before exact unload. Missing/unknown completion stops without
replacement generation or blind unload. Known budget/runtime terminal failures
remain measured outcomes and can unload. Optional JSON/SSE token accounting runs
after unload; missing or conflicting usage stays unmeasured. A later accounting
failure marks the call incomplete and does not generate a replacement.

`score_terminal` keeps evaluator references outside worker inputs and scores
against the original complete active-source observation. The public citation gate
uses the selected input, so guessed citations to omitted sources cannot pass.
An empty selected context cannot erase required facts from the evaluator. Native
terminal failures, invalid reports and correctly formatted wrong answers remain
distinct, including incorrect acceptance. No deterministic repair is performed.

The outer private, frozen runner must still verify source/case/selection/order
commitments, bound the global call/wall budget, handle shared identical inputs,
keep references inaccessible to workers and retain final evidence. A witness is
host-controlled integrity evidence, not authentication against its administrator.
The model metadata check does not attest hidden effective reasoning. `none`
remains a named request strategy under the documented #109 limitation.

Fresh synthetic tests exercise the real document adapter's worker-verification
path with mocked provider/process startup, actual protected files and durable
residency storage. They cover SSE, optional usage, omitted evidence, wrong and
invalid answers, terminal budgets, unknown completion, repeated execution, model
substitution and post-unload accounting failure. They are not real model results.
