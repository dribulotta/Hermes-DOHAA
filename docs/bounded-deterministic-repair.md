# Bounded deterministic repair scheduling

Rule-aware contracts now finish a sequence of calculable, authorized repair
units before requesting another runtime response. For example, three independent
visible equality errors can be corrected after the initial proposal even with
`max_attempts: 1`. Each unit still edits only its independently derived scope.

The controller first checks a runtime retry against the original scope and its
previous failure set. An out-of-scope, immutable-field or non-improving retry is
rejected before any further deterministic repair. For an accepted proposal, each
deterministic unit must preserve protected fields, resolve all selected failed
rules, introduce no new visible failures, and undergo full gate evaluation.
Rejection retains the previous proposal and stops the deterministic sequence.

Every accepted unit strictly reduces the visible failure set. There is also a
hard ceiling of 64 units per runtime attempt, matching the semantic language's
maximum assertion count. A missing scope, non-calculable first unit, rejected
candidate or repeated fingerprint ends the sequence. Scope selection order is
unchanged; this does not search around a blocked unit or widen its authority.

The evidence ledger records every evaluated unit, its scope, before/after
fingerprints, changed paths and failure comparison. Oracle-only gates never
authorize units or supply repair values; all gates must still pass for success.
Human approval remains a separate requirement. Contracts without a rule-aware
policy retain one deterministic repair, adopted only if all gates pass.

This fixes a fresh synthetic scheduling regression. It does not change, rescore
or explain every failure in the closed native studies, prove useful learning,
or establish superiority over the strong simple baseline. A separate prospective
comparison on fresh cases is required for that narrower architecture claim.

Rollback is a revert of the scheduling change. No deployed runtime, verifier,
contract schema, model budget, promotion rule or release is modified.
