repo: fabrica
role: Go API code generator that produces servers, clients, storage wiring, validation, and OpenAPI artifacts from versioned resource definitions.

when_to_load:
- Tasks changing Fabrica-generated services or Fabrica generator behavior.
- Tasks touching .fabrica.yaml, apis.yaml, or versioned *_types.go inputs.
- Tasks requiring generation workflow or generated/safe-edit boundary decisions.

entrypoints:
- README.md
- docs/guides/getting-started.md
- docs/reference/cli.md
- docs/apis-yaml.md
- .fabrica.yaml
- apis.yaml
- apis/<group>/<version>/*_types.go
- cmd/server/main.go (safe-edit portion only)

core_workflow:
- Update source definitions and config first.
- Run fabrica generate.
- Review generated fallout for API, validation, client, and docs impacts.
- Run project compile/test checks.

safe_edit_boundaries:
- Safe: .fabrica.yaml, apis.yaml, versioned resource type files, allowed custom regions in cmd/server/main.go.
- Unsafe: *_generated.go and regenerated artifacts not marked for manual edits.

common_failure_modes:
- Patching generated files directly.
- Skipping regeneration after source-input changes.
- Ignoring versioning and compatibility implications.

prompt_snippets:
- Inspect source inputs before proposing edits to generated outputs.
- When behavior changes, explain expected generated impact and compatibility surface.
