"""CLI entrypoint for OpenCHAMI coding agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import build_workspace_analysis_config, parse_config
from .config_init import add_init_arguments, run_init_command
from .constants import AGENT_NAME
from .pipeline import run_pipeline_with_reporter
from .reporting import RichProgressReporter
from .tui import run_textual_tui
from .ursa_compat import load_yaml_config
from .utils import to_plain_data


def build_root_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=f"{AGENT_NAME}: YAML-driven coding agent",
        usage=(
            "%(prog)s <config> [run options]\n"
            "       %(prog)s init [wizard options]\n"
            "       %(prog)s analyze-workspace <workspace> [analysis options]"
        ),
        epilog=(
            "Run commands:\n"
            "  <config>    Execute Marvin with an existing YAML config.\n"
            "  init        Interactively create a new Marvin YAML config file.\n"
            "  analyze-workspace  Inspect a previous workspace and recommend YAML updates."
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
        "--planning-mode",
        choices=["single", "hierarchical"],
        help="Override the planning mode from the YAML config for this run.",
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


def build_workspace_analysis_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{AGENT_NAME}: inspect a previous workspace and recommend YAML updates"
    )
    parser.add_argument("workspace", help="Path to an existing Marvin workspace")
    parser.add_argument(
        "--config",
        help="Optional original task YAML. If omitted, Marvin uses the workspace snapshot when available.",
    )
    parser.add_argument(
        "--model",
        default="openai:gpt-5.4",
        help="Planner model to use for workspace analysis.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable clarification prompts during workspace analysis.",
    )
    parser.add_argument(
        "--verbose-io",
        action="store_true",
        help="Print full underlying tool stdout/stderr from agent execution.",
    )
    return parser


def run_with_config(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    raw = to_plain_data(load_yaml_config(str(config_path)))
    mode = str(raw.get("mode") or "plan_and_execute").strip().lower()
    if mode == "analyze_workspace" and not (
        args.workspace or raw.get("workspace") or raw.get("restart_workspace")
    ):
        raise ValueError(
            "Workspace analysis mode requires an existing workspace via --workspace, "
            "workspace, or restart_workspace."
        )

    cfg = parse_config(
        config_path,
        cli_workspace=args.workspace,
        resume=args.resume or mode == "analyze_workspace",
    )

    if args.non_interactive:
        cfg.confirm_before_execute = False
        cfg.allow_user_prompts = False
    if args.confirm_before_execute:
        cfg.confirm_before_execute = True
    if args.no_resume_state:
        cfg.resume_execution_state = False
    if args.verbose_io:
        cfg.verbose_io = True
    if args.resume_from:
        cfg.resume_from = args.resume_from
    if args.planning_mode:
        cfg.planning_mode = args.planning_mode

    if args.tui:
        return run_textual_tui(cfg)

    return run_pipeline_with_reporter(cfg, RichProgressReporter())


def run_workspace_analysis(args: argparse.Namespace) -> int:
    cfg = build_workspace_analysis_config(
        Path(args.workspace),
        config_path=Path(args.config).resolve() if args.config else None,
        model_name=args.model,
    )

    if args.non_interactive:
        cfg.allow_user_prompts = False
    if args.verbose_io:
        cfg.verbose_io = True

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

    if argv[0] == "analyze-workspace":
        parser = build_workspace_analysis_parser()
        return run_workspace_analysis(parser.parse_args(argv[1:]))

    parser = build_run_parser()
    return run_with_config(parser.parse_args(argv))
