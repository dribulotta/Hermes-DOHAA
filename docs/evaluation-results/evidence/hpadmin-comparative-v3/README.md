# HPADMIN Comparative V3 public evidence

This directory publishes the immutable, recomputable public bundle and the
most review-relevant files separately for browser access.

The preregistered result is `NOT_PASSED`: DOHAA passed 16/40 cases and Direct
passed 15/40, with one paired win, zero losses, 39 ties, and an exact two-sided
sign-test p-value of `1.0`.

The bundle SHA-256 is:

```text
cfb36ccaa1dbec09a6b5f9e15b860423abc34e083bb94df96089c5df2387a50d
```

Extract the archive and run `bash verify-public-evidence-v3.sh` without network
access. The verifier recomputes aggregate results and validates the published
commitment chain. It does not independently re-score omitted raw proposals or
attest model identity, internal reasoning state, hardware, or execution.

The full 40-case synthetic suite, its expected results, runner, gates, and
generator are intentionally public inside the archive. The suite is therefore
retired permanently and must not be reused as a confirmatory holdout.

Generic RFC1918, loopback, link-local, `localhost`, and redirect-test literals
inside the runner are validation constants, not leaked deployment endpoints.
