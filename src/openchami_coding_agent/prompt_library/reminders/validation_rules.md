Validation Rules Reminders

General
- Validate behavior, not just formatting or static checks.
- Use the fastest blocker checks first, then broaden.
- Treat a green test suite as context, not sole proof of correctness.

Verifier Semantics
- PASS: checks ran and evidence supports correctness.
- FAIL: checks ran and evidence shows defects.
- PARTIAL: checks could not be completed due to missing env/tools/inputs.

Reporting
- Include exact failing command or verifier evidence.
- State what was not verified and why.
- Provide targeted rerun scope, not generic rerun advice.
