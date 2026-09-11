# Bounded native stream capacity

A new prospective two-call native transport diagnostic completed one response
and stopped on the second with `native_shadow.wire_limit`: HTTP 200, 524,326
decoded bytes observed and 524,288 retained. This establishes a response capture
limit failure in that diagnostic. It does not identify the cause of older
incomplete runs or change any closed result.

Streaming responses repeat JSON framing and metadata for many small deltas.
The encoded stream can therefore exceed the visible answer or reasoning text by
a large factor. The native bridge now separates these limits:

| Evidence | Limit |
|---|---:|
| Generation request and model metadata body | 512 KiB |
| Decoded generation response, JSON or SSE | 4 MiB |
| One serialized worker trace / worker file | 8 MiB |
| Aggregate serialized traces per adapter | 512 MiB |

Base64 expands the maximum 4.5 MiB of request plus response to 6 MiB, leaving
space for bounded trace metadata below the 8 MiB JSON reader and file limit.
Before each worker the adapter reserves room for an entire 8 MiB trace. The
aggregate bound accommodates 64 maximum-sized traces, including a 48-call paired
evaluation block. The request-count limit remains a separate upper bound, not a
promise that every request can run when another budget is exhausted. These are
private disk-evidence limits, not GPU memory allocations.

Both bridge files are still bound by the runtime policy hash. The token limit,
HTTP/worker deadlines, single-generation guard, complete-terminal requirement,
strict response parsing and exact owned-instance cleanup are unchanged. Requests
and catalog bodies do not inherit the larger response limit. Responses above
4 MiB still stop with bounded partial evidence and uncertain server completion.
No finite capture bound guarantees acceptance of every provider's framing.

Regression coverage includes a valid long reasoning stream above the old cap,
oversized requests/catalogs/responses, incomplete long streams and trace-budget
exhaustion before dispatch. The new native loopback control retains a serialized
trace larger than the former 4 MiB file limit, using synthetic deltas within an
8192-token request budget. It does not claim those fixture chunks measure actual
tokenizer work or demonstrate learning quality.
