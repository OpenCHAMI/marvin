Repository Profile Builder Contract

Goal
- Build a compact, evidence-backed profile for one repository in the OpenCHAMI ecosystem.

Scope
- Use repository contents and layout as primary evidence.
- Infer likely validation, packaging, and operational risk surfaces.

Hard Boundaries
- Do not invent commands unsupported by repository structure.
- Do not include speculative dependencies or unsafe defaults without evidence.
- Prefer empty lists over guessed entries.

Required Output Shape
- repo
- language_toolchain
- validation_commands
- smoke_tests
- packaging_commands
- dangerous_operations
- critical_files
- service_boundaries
- related_repos
- protected_paths
- operator_sensitive_behavior
- resume_idempotence_expectations

Quality Bar
- Keep lists concise and high-signal.
- Prefer commands and paths likely to hold across local and CI runs.
- Use repository-native conventions when available.