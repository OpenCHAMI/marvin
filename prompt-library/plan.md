Plan Phase Contract

Goal
- Convert exploration evidence into a reviewable implementation plan.

Scope
- Read-only decomposition of work into executable steps.
- Define sequencing, validation strategy, and rollback considerations.

Hard Boundaries
- Do not edit source files.
- Do not include speculative steps with no evidence trail.
- Do not bundle unrelated work into a single step.

Required Outputs
- Ordered steps with repo/file scope and intended outcomes.
- Validation commands and success criteria per step.
- Critical risks and mitigation notes.
- Explicit execution order and dependency notes.

Quality Bar
- Steps must be independently executable and testable.
- Prefer existing code paths and patterns over reinvention.
- Keep the plan compact, concrete, and minimally ambiguous.
