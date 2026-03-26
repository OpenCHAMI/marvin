"""CLI entrypoint for OpenCHAMI coding agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import parse_config
from .config_init import add_init_arguments, run_init_command
from .constants import AGENT_NAME
from .pipeline import run_pipeline_with_reporter
from .reporting import RichProgressReporter
from .tui import run_textual_tui


def build_root_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=f"{AGENT_NAME}: YAML-driven coding agent",
        usage="%(prog)s <config> [run options]\n       %(prog)s init [wizard options]",
        epilog=(
            "Run commands:\n"
            "  <config>    Execute Marvin with an existing YAML config.\n"
            "  init        Interactively create a new Marvin YAML config file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def build_run_parser() -> argparse.ArgumentParser:
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
    return parser


def run_with_config(args: argparse.Namespace) -> int:
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        build_root_parser().print_help()
        return 0

    if argv[0] == "init":
        parser = argparse.ArgumentParser(
            description=f"{AGENT_NAME}: interactive YAML config generator"
        )
        add_init_arguments(parser)
        return run_init_command(parser.parse_args(argv[1:]))

    parser = build_run_parser()
    return run_with_config(parser.parse_args(argv))
