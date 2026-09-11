# Date and JSON Pointer failures remain recorded outcomes

The semantic evaluator rejects date arithmetic that leaves the representable
calendar. Both `add_days` and `add_business_days` return the value-free error
`temporal.date_out_of_range`, including overflow while skipping weekends or
holidays. The gate records an evaluation failure, deterministic repair declines
to invent a result, and the controller can reach its existing bounded terminal
state. Previously, an `OverflowError` escaped before a terminal was recorded.
Valid dates, zero offsets, positive/negative offsets and existing offset limits
retain their behavior.

JSON Pointer array indices now require ASCII decimal digits, with no leading
zero except the single token `0`, in both value resolution and repair writes.
This follows [RFC 6901, section 4](https://www.rfc-editor.org/rfc/rfc6901.html#section-4).
Unicode object keys remain literal valid keys: the same token may name a member
of an object without being a valid array index. Previously, Unicode decimal
digits could select an array member and other digit characters could raise an
uncaught conversion error. Invalid indices now produce `reference.invalid_index`.

Eight synthetic regressions cover both directions of date overflow, calendar
boundaries and holiday skipping, non-mutating repair rejection, Unicode keys
versus array indices, and a single controller terminal without additional calls.
They exercise deterministic components; no live model or previous study is used.

Because these modules participate in the route source commitment, new protocols
on the corrected tree explicitly select `verified-tool-admission/1.2`.
Profiles `/1.0` and `/1.1` keep their original hashes and reject the new source;
closed evidence is not migrated. The existing tool route still receives one
already verified proposal and does not use semantic repair or gain generation
authority. A passing source profile is not evidence of model-quality advantage.
