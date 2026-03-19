"""CLI entrypoint for OpenCHAMI coding agent."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import parse_config
from .constants import AGENT_NAME
from .pipeline import run_pipeline_with_reporter
from .reporting import RichProgressReporter
from .tui import run_textual_tui


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{AGENT_NAME}: YAML-driven coding agent")
    parser.add_argument("config", help="Path to YAML config")
    parser.add_argument(
        "--workspace",
        help="Reuse or create a specific workspace path. Useful for restart/resume.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Require the supplied workspace to already exist.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable execution confirmation prompt.",
    )
    parser.add_argument(
        "--confirm-before-execute",
        action="store_true",
        help="Require confirmation before execution starts.",
    )
    parser.add_argument(
        "--resume-from",
        help=(
            "Checkpoint filename or path to restore executor state from "
            "(for example: executor_checkpoint_5.db)."
        ),
    )
    parser.add_argument(
        "--no-resume-state",
        action="store_true",
        help="Ignore saved execution progress state and start execution fresh.",
    )
    parser.add_argument(
        "--verbose-io",
        action="store_true",
        help="Print full underlying tool stdout/stderr from agent execution.",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Use Textual TUI dashboard for run progress.",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    cfg = parse_config(config_path, cli_workspace=args.workspace, resume=args.resume)

    if args.non_interactive:
        cfg.confirm_before_execute = False
    if args.confirm_before_execute:
        cfg.confirm_before_execute = True
    if args.no_resume_state:
        cfg.resume_execution_state = False
    if args.verbose_io:
        cfg.verbose_io = True
    if args.resume_from:
        cfg.resume_from = args.resume_from

    if args.tui:
        return run_textual_tui(cfg)

    return run_pipeline_with_reporter(cfg, RichProgressReporter())
