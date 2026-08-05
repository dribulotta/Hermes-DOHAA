# Contributing

Hermes-DOHAA is pre-alpha. Small, evidence-backed changes are preferred over broad rewrites.

## Development workflow

1. Open an issue describing the invariant, defect, or experiment.
2. Create a focused branch.
3. Add or update a regression test before changing behavior.
4. Run:

   ```bash
   python -m compileall -q src
   python -m unittest discover -s tests -v
   ```

5. Describe the evidence, limitations, and rollback path in the pull request.

Changes to policies, verifiers, golden cases, holdouts, promotion rules, and evidence integrity code require explicit maintainer review. Agent-generated changes must be identified as such and may not approve themselves.

By submitting a contribution, you agree that code is licensed under Apache-2.0 and documentation under CC BY 4.0 unless a file states otherwise.
