# Contributing

## Development setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -e .[dev]
```

## Local checks

Run before opening a PR:

```bash
ruff check .
mypy
pytest
```

## Pull requests

- Keep changes focused and minimal.
- Add or update tests for behavior changes.
- Update `README.md` and `CHANGELOG.md` when user-facing behavior changes.

## Documentation

- Document Marvin as an OpenCHAMI-focused YAML-driven planning and execution tool, not a generic autonomous agent.
- Keep documentation direct, technically specific, and mildly sardonic when tone is appropriate.
- Prefer operational clarity over marketing language. If a behavior is constrained, say so plainly.
- When documenting new flags, artifacts, or workflow changes, describe defaults and failure modes, not just the happy path.
