Verify Phase Contract

Goal
- Evaluate execution outputs with non-mutating verifiers.

Scope
- Run verifiers against a frozen verification bundle.
- Produce structured verdicts with evidence and findings.

Hard Boundaries
- Verifiers must not mutate source under verification.
- Side effects are limited to artifacts, logs, or scratch output.
- Required verifier failures block overall success.

Tiering Model
- Tier 1: cheap blockers, fail fast.
- Tier 2: medium-cost checks, parallel when possible.
- Tier 3: expensive or optional checks, configuration-gated.

Required Outputs
- Per-verifier verdict: PASS, FAIL, or PARTIAL.
- Findings and evidence pointers per verifier.
- Aggregated verdict with required-failure list.
- Rerun recommendations scoped to failures.

Quality Bar
- Prefer direct runtime evidence over inferred confidence.
- PARTIAL means verification could not be completed, not uncertainty after execution.
- Keep reports machine-readable and operator-readable.
