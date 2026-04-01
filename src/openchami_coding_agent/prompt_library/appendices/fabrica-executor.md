Fabrica execution guidance:
- Treat Fabrica as a Go API code generator, not as a deployment-config repository.
- Update source inputs first: `.fabrica.yaml`, `apis.yaml`, and versioned `apis/<group>/<version>/*_types.go` files.
- Do not patch `*_generated.go` files by hand unless the task is explicitly about the generator itself.
- After changing generator inputs, run or explicitly plan for `fabrica generate` and inspect the generated fallout.
- Respect the documented safe-edit boundary in `cmd/server/main.go`; generated sections can be rewritten.