Repair Phase Contract

Goal
- Fix specific failures with minimal, targeted changes.

Scope
- Repair is driven by failing verifier/check evidence.
- Keep successful areas untouched unless evidence changes.

Hard Boundaries
- No broad rewrites during targeted repair.
- No source changes outside failing scope without justification.
- No suppression-only fixes that hide failures without resolving cause.

Required Outputs
- Failing evidence linked to each repair action.
- Root-cause hypothesis and implemented fix.
- Focused rerun guidance for affected verifiers/checks.

Quality Bar
- Prefer deterministic fixes over retries or heuristics.
- Keep repair loops narrow and auditable.
- Record what was intentionally not changed and why.
