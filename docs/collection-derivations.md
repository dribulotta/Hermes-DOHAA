# Verifying collection-derived results

A shape check cannot detect a wrong total, an incorrect time-zone conversion or
an invented list of missing fields. Contracts that require those facts must
declare the corresponding `semantic_assertions`. Acceptance prose alone does not
install a factual verifier. The controller does not infer formulas from prose.

Four bounded expressions extend the existing assertion language:

| Expression | Arguments | Behavior |
|---|---|---|
| `keys` | object | Return present keys in object insertion order. Null-valued keys are present. |
| `difference` | array, array | Remove values present in the second array; preserve the first array's remaining order and duplicates. |
| `lookup_many` | object, array of string keys | Look up literal keys, preserving requested order and repeated keys; a missing key fails evaluation. |
| `dot_product` | two arrays of numbers | Sum paired products; require equal lengths, reject booleans and non-finite or out-of-bound numbers. Empty arrays produce integer zero. |

All expressions use `{"op": "name", "args": [...]}`. They retain the language's
10,000-item collection bound, expression depth and node limits, reserved-input
restrictions and value-free evaluation errors. `difference` uses the same strict
canonical identity as `unique`: booleans, integers, floating-point numbers and
strings remain distinct. Object key order does not affect membership. Keys passed
to `lookup_many` are literal strings, not JSON Pointers. Numeric bounds also apply
to every product and running total, so overflowing intermediate values fail even
if later values could cancel them.

The [development examples](../examples/collection-derivations) show five complete
contracts: offset-aware elapsed time, a filtered price join, source selection,
stable deduplication and missing-field reporting. Joins combine `filter`,
`project`, `lookup_many` and `dot_product`, deriving values from visible data.
Every required result field has an input-derived equality. Positive references
and deliberately incorrect results are kept in a separate test fixture, never
inside a controller contract or model prompt.

These examples define deliberately narrow policies. Source selection trusts
supplied boolean flags; it does not verify signatures or authenticate sources.
Ties use the first matching input record. Missing-field reporting counts presence,
not non-nullness or type validity, and that example escalates when no input is
missing rather than implementing the successful-computation branch.

No default gate selection or existing contract changes. Adding these expressions
does not automatically validate undeclared facts, arbitrary natural language,
claims or provenance. Contract authors remain responsible for correct formulas,
source quality and coverage of every required output. A successful deterministic
repair is not model learning. Development regressions do not replace results from
earlier benchmarks.

The semantic assertion module is already included in approval checkpoint code
identity. Existing checkpoints need fresh verification after this code change.
