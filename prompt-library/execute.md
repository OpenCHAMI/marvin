Execute Phase Contract

Goal
- Implement approved plan steps safely and incrementally.

Scope
- This is the only phase allowed to mutate source files.
- Apply smallest reliable changes that satisfy plan scope.

Hard Boundaries
- Stay within workspace containment.
- Do not edit reference-only repositories.
- Do not perform destructive operations unless explicitly requested.

Required Outputs
- Step-by-step change summaries.
- Files touched and commands run.
- Test and verification outcomes.
- Blockers or deferred items with rationale.

Quality Bar
- Preserve compatibility unless change requires breakage.
- Keep comments accurate when implementation changes.
- Prefer clear, maintainable edits over clever shortcuts.
