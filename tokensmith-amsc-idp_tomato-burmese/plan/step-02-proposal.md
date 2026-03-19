# Step 2: Markdown implementation proposal (RFC 8693 Phase 1)

Source: repos/tokensmith/docs/proposals/rfc8693-phase1.md

This step produces the architecture, scope, and migration plan used as the implementation source of truth. See that file for the capability matrix and contracts.

Highlights
- Add typed token profiles and a configurable project/accounting context claim (default: project_accounting_context).
- Keep endpoints stable; add RFC 8693 form handling to /oauth/token; preserve current JSON body for compatibility.
- Stabilize kid and JWKS content; enforce alg allowlist; include signing_alg_values_supported in metadata.
- Define standardized error model for API responses.

Next steps
- Step 3 will extract explicit compatibility, migration, and error model details into a normative spec to drive code and tests.
