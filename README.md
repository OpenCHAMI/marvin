# Marvin (OpenCHAMI Coding Agent)

Python multi-agent coding runner for OpenCHAMI using [LANL URSA](https://github.com/lanl/ursa).

## Repository layout

- `OpenCHAMI_coding_agent.py`: compatibility wrapper entrypoint
- `src/openchami_coding_agent/`: package source code
- `tests/`: unit tests
- `.github/workflows/ci.yml`: CI checks for lint, typing, and tests
- `pyproject.toml`: packaging and tool configuration

## Development

Install:

```bash
pip install -e .[dev]
```

## Run behavior

- Marvin defaults to non-interactive execution progression (no per-step confirmation prompts).
- To require a pre-execution confirmation gate, pass `--confirm-before-execute`.
- To resume from an existing workspace checkpoint, pass `--workspace <path> --resume --resume-from <checkpoint>`.
- If `--resume-from` is omitted and checkpoints exist, Marvin resumes from the workspace live checkpoint.
- Marvin always creates `plan/marvin.md` plus per-step files (`plan/step-*.md`) in the workspace.
- `plan/marvin.md` is continuously updated with stage, completed/remaining steps, and reconciliation notes.
- The Textual TUI renders and refreshes the tracker file live during planning/execution.

## Config generation

Use `marvin init` to create a new YAML config from rough starting material such as a GitHub issue, a loose feature description, or an architectural proposal.

```bash
marvin init
```

If you already have a source document and output path, Marvin now uses an
agent-assisted draft generator without prompting:

```bash
python3 marvin.py init --source-file tokensmith-amsc.md --output tokensmith-amsc-task.yaml
```

Use `--interactive` to force the wizard and refine the generated values manually.

Optional flags:

- `--output path/to/config.yaml` to control where the config is written.
- `--source-file path/to/notes.md` to seed auto-generation or prefill the interactive wizard.
- `--model openai:gpt-5.4` to choose the model used by the config-generation agent.
- `--interactive` to force the wizard even when `--source-file` and `--output` are both present.
- `--force` to overwrite an existing output file without the overwrite prompt.

The wizard is CLI-only. It is intentionally not available inside the Textual TUI.

Auto-generation behavior:

- Marvin reads the source file, searches only GitHub pages under `github.com/OpenCHAMI`, and uses that context to draft a config.
- The generated YAML is normalized back into Marvin's schema so outputs and defaults stay consistent.
- If the generated draft is wrong, rerun with `--interactive` and edit the fields manually.

The generated config includes:

- A normalized `problem` section based on the source text you provide.
- Repository definitions, validation commands, and model selection.
- Default planning and execution requirements suitable for follow-on Marvin runs.
- Output artifact paths derived from the generated YAML filename.

## Agent customization

Marvin is configured through YAML. The existing `models` section chooses the planner and executor LLMs, and the optional `execution.executor_agent` setting chooses the URSA execution agent implementation.

Use the `agent` section to customize prompt behavior without changing code:

```yaml
agent:
	name: Marvin
	persona_instruction: |
		Keep the dry, skeptical tone, but optimize for concise operational updates.
	prompt_appendix: |
		Prefer minimal diffs and focused validation output.
	planner_prompt_appendix: |
		Call out migration risks and rollback points in the plan.
	executor_prompt_appendix: |
		Favor small commits and run the narrowest relevant tests first.
	repair_prompt_appendix: |
		When retrying failed checks, explain the root cause before patching.

execution:
	executor_agent: auto  # auto | execution | gitgo
	commit_each_step: true
```

Field behavior:

- `agent.name` changes the self-identification used inside planner/executor/repair prompts.
- `agent.persona_instruction` overrides the default Marvin persona block.
- `agent.prompt_appendix` is appended to all agent prompts.
- `agent.planner_prompt_appendix`, `agent.executor_prompt_appendix`, and `agent.repair_prompt_appendix` are appended only to those specific prompt types.
- `execution.executor_agent` selects the URSA execution agent class.
- `execution.commit_each_step` controls whether Marvin attempts a git commit after each completed plan step.

Run tests:

```bash
pytest
```

Lint:

```bash
ruff check .
```

Type-check:

```bash
mypy
```

## CI

Pull requests run:

- `ruff check .`
- `mypy`
- `pytest`
