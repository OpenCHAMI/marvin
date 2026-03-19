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
