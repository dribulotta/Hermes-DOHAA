# HPADMIN Comparative V4 public evidence

The preregistered V4 assessment is `NOT_PASSED`. Rule-aware DOHAA passed 78/96
cases versus 30/96 for deterministic policy only, with 48 paired wins, zero
losses, and an exact two-sided sign-test p-value of
`7.105427357601002e-15`. The all-required decision failed because generic
self-repair produced nine unsafe finals and because of the frozen non-reuse
schema mismatch. The restricted audit record separately reported zero exact or
unresolved collisions; the sanitized release does not independently verify
that claim because its private prior-art inventory is withheld.

## Privacy-sanitized full-outcome release

The original public candidate was not released: its unsalted endpoint digest
could reveal a private IP address and port through offline dictionary search.
The published archive is a post-score sanitized derivative. As an
operator-attested projection, it keeps the retired suite and oracle byte for
byte, all 384 condition outcomes, all 738 proposal snapshots, controller
ledgers, request parameters, scores, and the offline re-scorer. Endpoint
coordinates, endpoint-dependent historical digests, operational host artifacts,
and the private non-reuse source inventory are withheld. The public verifier
checks suite structure, deterministic generation, and content hashes, but not
the original external suite binding.

Byte-exact suite retention necessarily keeps two non-endpoint SHA-256
commitments to the omitted non-reuse audit and prior-art inventory. They do not
bind the endpoint, but may confirm a correct guess of that withheld inventory;
the sanitization manifest records this residual boundary.

The sanitized archive SHA-256 is:

```text
dbf88cfea9fb98c2aa4d882c4ab89660a7f760384a0bd193415e8ba0f0b194ba
```

Run the bundled offline verifier after checking that digest. It re-scores all
384 final proposals and 738 trace proposals, validates the complete evidence
inventory, recomputes the paired tests and Holm correction, and reproduces
`NOT_PASSED`.

The verification boundary is intentionally narrower than the restricted
execution record. The public derivative does not independently verify the
private endpoint, original package-manifest chain, original prescore
chronology, source TAR byte identity, remote GitHub timestamp, or model-artifact
digest. The recorded zero commitment violations remain a restricted-record
attestation; they are not established by the sanitized verifier. The two
publicly reproducible failures already force the negative formal result, so
this limitation cannot change the assessment.

The two pre-existing commitment JSON files in this directory remain as
historical provenance. They are excluded from the sanitized archive and are
not consumed by its verifier; neither file authenticates the post-score
sanitized results.

See the [sanitization manifest](SANITIZATION-MANIFEST-v4.json) for the exact
retained, transformed, and omitted material. The suite is retired permanently
and must never be reused as a confirmatory holdout.
