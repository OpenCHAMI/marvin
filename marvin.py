#!/usr/bin/env python3
"""Compatibility wrapper for Marvin (OpenCHAMI coding agent) CLI."""

import sys
from importlib import import_module
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    module = import_module("openchami_coding_agent.cli")
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
