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
