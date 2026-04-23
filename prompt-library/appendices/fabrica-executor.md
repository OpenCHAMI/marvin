Fabrica Executor Appendix

Generation Model
- Treat Fabrica as a generator pipeline, not a handwritten service tree.
- Edit source inputs first, then regenerate and inspect fallout.

Primary Inputs
- .fabrica.yaml
- apis.yaml
- apis/<group>/<version>/*_types.go

Guardrails
- Do not hand-edit generated files unless task scope is generator internals.
- Keep custom edits within documented safe-edit boundaries.
- Verify generation and compile/test after input changes.

Review Focus
- Review source-input intent and expected generated impact.
- Note compatibility implications for versioned APIs.
