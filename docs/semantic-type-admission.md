# Semantic selector type admission

The semantic language requires strings for assertion `operator`, expression
`op`, reference `source`, filter `comparator`, and sort `order`. Previously,
JSON arrays or objects in any of those fields reached set membership and raised
`TypeError`. The documented `ValueError` paths in CLI admission, evaluation
loading and gates could not handle that exception. Deterministic repair already
rejected it through its existing broader error handler.

The parser now checks the string type before membership. Malformed selectors
produce ordinary validation failures; new type diagnostics do not interpolate
the supplied payload. Unsupported string operators retain their prior handling.
The fix adds no coercion, implicit defaults for invalid values, or broad exception
catch that could hide an unrelated programming error. Omitted sort order still
defaults to ascending; explicitly supplied null remains invalid.

Eight synthetic regression tests exercise all five selectors with arrays and
objects across the parser, CLI validation/run, evaluation loading/freezing,
direct gates, deterministic repair and controller completion. They also cover
other JSON scalar types and valid nested filter/sort rules. Before the fix,
six tests recorded 70 unhandled-exception subcases; the repair rejection and
valid-rule controls already passed.

CLI run admission rejects before runtime construction or ledger creation;
freezing rejects before writing a suite commitment. Direct library controller
use still obeys its existing proposal budget and records a terminal failure.
Repair returns no candidate and does not mutate the contract or proposal.
These tests use synthetic proposals and make no native model requests.

This module belongs to the route protocol's source commitment. Profile
`verified-tool-admission/1.3` pins the reviewed source; all three previous
profiles keep their existing file lists and hashes. The one-proposal route
still receives a completed answer and gets no new generation or repair ability.
These are software reliability checks, not comparative evidence of model or
DOHAA superiority.
