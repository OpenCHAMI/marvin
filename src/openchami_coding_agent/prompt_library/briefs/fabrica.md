repo: fabrica
role: Code generation tool for Go REST APIs that turns versioned resource type definitions into production-ready servers, clients, storage layers, validation, and OpenAPI artifacts.
when_to_load:
- Tasks that modify a Fabrica-generated service or the Fabrica tool itself.
- Tasks involving `.fabrica.yaml`, `apis.yaml`, versioned resource types, or `*_generated.go` regeneration.
- Tasks that need the Fabrica CLI workflow, generated project layout, or safe-edit boundaries.
documentation:
- README.md in the Fabrica repo is the primary overview.
- Full docs live at https://openchami.github.io/fabrica/.
- Usage-oriented docs called out from the README include quickstart, getting started, CLI reference, resource model, architecture, and apis.yaml reference.
how_to_use:
- Install from a release binary, `go install github.com/openchami/fabrica/cmd/fabrica@<version>`, or `make install` from a clone.
- Verify installation with `fabrica version`.
- Define resources in `apis/<group>/<version>/*_types.go` plus API configuration in `apis.yaml`.
- Configure project features and settings in `.fabrica.yaml`.
- Regenerate generated code with `fabrica generate` after resource or config changes.
- Start learning from the quickstart and examples before editing advanced codegen or middleware behavior.
entrypoints:
- README.md and docs/guides/getting-started.md for the normal workflow.
- docs/reference/cli.md for command behavior and flags.
- docs/apis-yaml.md for API configuration structure.
- `.fabrica.yaml`, `apis.yaml`, and `apis/<group>/<version>/*_types.go` for project inputs.
- `cmd/server/main.go` for limited safe server customization before the generated marker.
config_surfaces:
- `.fabrica.yaml` for project settings and feature flags.
- `apis.yaml` for API group and version configuration.
- `apis/<group>/<version>/*_types.go` for spec and status structs that drive generation.
generated_project_shape:
- `cmd/server/` for the generated REST API server.
- `cmd/cli/` for generated command-line clients.
- `pkg/client/` for generated HTTP client code.
- `internal/storage/` and `internal/middleware/` for generated backend and middleware support.
- `docs/` for generated OpenAPI output.
invariants:
- Fabrica uses a Kubernetes-inspired resource envelope with generated `apiVersion`, `kind`, and `metadata`, while you define `spec` and `status`.
- `*_generated.go` files are regenerated and should not be hand-edited.
- `.fabrica.yaml`, `apis.yaml`, resource type definitions, and pre-generated portions of `cmd/server/main.go` are the intended customization points.
- After changing resource definitions or Fabrica config, regeneration is part of the normal workflow.
safe_edit_boundaries:
- Safe to edit: `apis/<group>/<version>/*_types.go`, `apis.yaml`, `.fabrica.yaml`, and `cmd/server/main.go` before the first generated marker.
- Do not hand-edit: files ending in `*_generated.go` or other purely generated outputs that Fabrica rewrites.
common_commands:
- `fabrica version`
- `fabrica generate`
- `make install` when developing Fabrica itself.
change_triggers:
- If `spec` or `status` structs change, inspect generated handlers, clients, validation, and OpenAPI output after regeneration.
- If `.fabrica.yaml` or `apis.yaml` changes, inspect generation results and compatibility across versions.
- If storage or middleware settings change, inspect generated storage, middleware, and server wiring.
common_failure_modes:
- Editing generated files instead of the source definitions that produce them.
- Forgetting to run `fabrica generate` after changing resource or config inputs.
- Treating `.fabrica.yaml` as the only source of truth while ignoring `apis.yaml` and versioned type definitions.
- Breaking the generated/non-generated boundary in `cmd/server/main.go`.
migration_hazards:
- Regeneration can rewrite large areas of generated code, so reviews should focus on source-input changes and expected generated fallout.
- Versioning and resource envelope changes can create compatibility issues even when local compilation succeeds.
evidence_sources:
- Fabrica README overview, installation section, architecture overview, resource model section, and regeneration guidance.
- Fabrica docs index referenced from the README.
prompt_snippets:
- Treat Fabrica as a code generator: inspect `.fabrica.yaml`, `apis.yaml`, and versioned `*_types.go` inputs before proposing edits.
- When Fabrica-related behavior changes, update the source definitions first, run or plan for `fabrica generate`, and review generated fallout rather than patching generated files manually.