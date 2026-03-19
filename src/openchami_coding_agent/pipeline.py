"""Planning + execution pipeline composition."""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from .checkpoints import (
    checkpoint_dir,
    resolve_resume_checkpoint,
    restore_executor_from_snapshot,
    sync_progress_for_snapshot_single,
)
from .config import default_working_directory, ensure_repo, render_status
from .execution import execute_plan, extract_repo_sequence_from_plan
from .models import AgentConfig
from .plan_tracking import extract_plan_steps, initialize_plan_artifacts, update_tracker_markdown
from .prompts import build_planner_prompt
from .reporting import (
    ProgressReporter,
    emit_panel,
    emit_text,
    progress_heartbeat,
    render_run_progress,
    set_reporter,
    set_workspace_name,
)
from .utils import (
    extract_agent_tokens,
    invoke_agent,
    progress_file,
    write_json_file,
    write_text_file,
)


def make_agent_llm(config: AgentConfig, role: str):
    from ursa.util.plan_execute_utils import setup_llm

    model_name = config.planner_model if role == "planner" else config.executor_model
    if not model_name:
        raise ValueError(f"No model configured for role '{role}'.")
    role_cfg = (config.defaults or {}).copy()
    role_cfg.update((config.planner if role == "planner" else config.execution) or {})
    return setup_llm(model_name, role_cfg)


def generate_plan(cfg: AgentConfig) -> tuple[str, dict[str, Any]]:
    from ursa.agents import PlanningAgent
    from ursa.util.plan_execute_utils import hash_plan

    workspace = cfg.workspace
    if workspace is None:
        raise RuntimeError("Workspace must be set before plan generation.")

    llm = make_agent_llm(cfg, "planner")
    planner_db = checkpoint_dir(workspace) / "planner_checkpoint.db"
    planner_conn = sqlite3.connect(str(planner_db), check_same_thread=False)
    planner_checkpointer = SqliteSaver(planner_conn)
    thread_id = workspace.name
    planner = PlanningAgent(
        llm=llm,
        checkpointer=planner_checkpointer,
        enable_metrics=True,
        metrics_dir="ursa_metrics",
        thread_id=thread_id,
        workspace=str(workspace),
    )
    planner.thread_id = thread_id
    prompt = build_planner_prompt(cfg)
    planning_started = time.time()

    def planning_detail_provider() -> str:
        elapsed = time.time() - planning_started
        if elapsed < 20:
            return "Sorting requirements into something almost coherent."
        if elapsed < 60:
            return "Drafting step order and pretending this will be straightforward."
        return "Still shaping plan steps; the model is thinking at geological speed."

    render_run_progress(
        stage="planning",
        detail="Generating implementation plan",
        total_repos=len(cfg.repos),
    )
    with progress_heartbeat(
        stage="planning",
        detail="Planning in progress (waiting for LLM response)",
        detail_provider=planning_detail_provider,
        token_usage_provider=lambda: extract_agent_tokens(planner),
        total_repos_provider=lambda: len(cfg.repos),
    ):
        invocation = invoke_agent(planner, prompt, cfg.verbose_io)
    plan_markdown = invocation.content

    plan_payload = {
        "project": cfg.project,
        "mode": cfg.mode,
        "workspace": str(cfg.workspace),
        "context_claim_name": cfg.context_claim_name,
        "proposal_markdown": cfg.proposal_markdown,
        "plan_markdown": plan_markdown,
        "plan_hash": hash_plan([plan_markdown]),
        "token_usage": extract_agent_tokens(planner),
        "repo_sequence_from_plan": extract_repo_sequence_from_plan(
            plan_markdown,
            [repo.name for repo in cfg.repos],
        ),
    }
    if cfg.verbose_io:
        plan_payload["planner_stdout"] = invocation.captured_stdout
        plan_payload["planner_stderr"] = invocation.captured_stderr
    planner_conn.close()
    render_run_progress(
        stage="planning",
        detail="Plan generated",
        token_usage=plan_payload.get("token_usage") or {},
    )
    return plan_markdown, plan_payload


def run_pipeline(cfg: AgentConfig) -> int:
    from ursa.util.plan_execute_utils import hash_plan, timed_input_with_countdown

    workspace = cfg.workspace
    if workspace is None:
        raise RuntimeError("Workspace was not resolved from config.")

    set_workspace_name(workspace.name)

    os.chdir(workspace)
    emit_panel(f"Working directory pinned to workspace: {workspace}", border_style="blue")

    selected_resume_checkpoint = resolve_resume_checkpoint(workspace, cfg.resume_from)
    if selected_resume_checkpoint is not None:
        restored_live = restore_executor_from_snapshot(workspace, selected_resume_checkpoint)
        emit_panel(
            (
                f"Resume checkpoint selected: {selected_resume_checkpoint.name}. "
                f"Live executor DB: {restored_live.name}"
            ),
            border_style="blue",
        )

    render_status(cfg)

    for repo in cfg.repos:
        ensure_repo(repo)

    run_cwd = default_working_directory(cfg)
    if run_cwd is not None:
        os.chdir(run_cwd)
        if len(cfg.repos) == 1:
            emit_panel(
                f"Single-repo mode: execution working directory set to repo root: {run_cwd}",
                border_style="green",
            )
        else:
            emit_panel(f"Execution working directory: {run_cwd}", border_style="green")

    plan_markdown: str | None = None

    if cfg.mode in {"plan", "plan_and_execute"}:
        update_tracker_markdown(
            workspace=workspace,
            stage="planning",
            activity="Planner starting; extracting actionable steps.",
            plan_steps=[],
            completed_step_indices=set(),
            notes=["Preparing plan artifacts."],
            reconciliation="Planning phase in progress.",
        )
        emit_panel("Generating implementation proposal", border_style="cyan")
        plan_markdown, plan_payload = generate_plan(cfg)
        plan_md_path = write_text_file(workspace, cfg.proposal_markdown, plan_markdown)
        plan_json_path = write_json_file(workspace, cfg.plan_json, plan_payload)
        plan_steps = initialize_plan_artifacts(workspace, plan_markdown)
        tracker_path = update_tracker_markdown(
            workspace=workspace,
            stage="planning",
            activity="Plan generation finished; awaiting execution decision.",
            plan_steps=plan_steps,
            completed_step_indices=set(),
            notes=[
                f"Proposal written to {cfg.proposal_markdown}",
                f"Plan JSON written to {cfg.plan_json}",
            ],
            reconciliation="Execution has not started yet.",
        )
        emit_text(f"Wrote proposal: {plan_md_path}")
        emit_text(f"Wrote plan JSON: {plan_json_path}")
        emit_text(f"Updated plan tracker: {tracker_path}")

    if (
        cfg.mode == "plan"
        or cfg.proposal_only
        or (cfg.mode == "plan_and_execute" and not cfg.execute_after_plan)
    ):
        return 0

    if cfg.mode == "execute":
        plan_md_path = (workspace / cfg.proposal_markdown).resolve()
        if not plan_md_path.exists():
            raise FileNotFoundError(
                f"Execution mode requires an existing proposal file: {plan_md_path}"
            )
        plan_markdown = plan_md_path.read_text(encoding="utf-8")
        initialize_plan_artifacts(workspace, plan_markdown)

    if not plan_markdown:
        raise RuntimeError("No plan markdown available for execution.")

    if selected_resume_checkpoint is not None and cfg.mode in {"execute", "plan_and_execute"}:
        plan_sig = hash_plan([plan_markdown])
        sync_progress_for_snapshot_single(
            workspace,
            selected_resume_checkpoint,
            plan_sig,
            cfg.executor_progress_json,
        )
        emit_panel(
            (
                f"Execution progress aligned from checkpoint: "
                f"{selected_resume_checkpoint.name}"
            ),
            border_style="blue",
        )

    if cfg.confirm_before_execute:
        response = timed_input_with_countdown(
            f"Execute plan now? [Y/n] (auto-yes in {cfg.confirm_timeout_sec}s): ",
            cfg.confirm_timeout_sec,
        )
        if response and response.strip().lower() in {"n", "no"}:
            emit_panel("Execution cancelled by user", border_style="yellow")
            return 1

    emit_panel("Executing approved plan", border_style="magenta")
    plan_steps = extract_plan_steps(plan_markdown)
    update_tracker_markdown(
        workspace=workspace,
        stage="execution",
        activity="Execution started; applying plan steps and monitoring checkpoints.",
        plan_steps=plan_steps,
        completed_step_indices=set(),
        notes=["Executor launched."],
        reconciliation="Execution in progress.",
    )
    summary_payload = execute_plan(cfg, plan_markdown, executor_llm=make_agent_llm(cfg, "executor"))
    summary_path = write_json_file(workspace, cfg.summary_json, summary_payload)
    emit_text(f"Wrote execution summary: {summary_path}")
    progress_path = progress_file(workspace, cfg.executor_progress_json)
    emit_text(f"Wrote execution progress: {progress_path}")
    completed_repos = ", ".join(summary_payload.get("completed_repos") or []) or "-"
    failed_repos = ", ".join(summary_payload.get("failed_repos") or []) or "-"
    emit_panel(
        "\n".join(
            [
                "Execution summary:",
                f"- Workspace: {workspace.name}",
                f"- Completed repos: {completed_repos}",
                f"- Failed repos: {failed_repos}",
                (
                    "- Tokens sent/received/total: "
                    f"{int((summary_payload.get('token_usage') or {}).get('input_tokens', 0))}/"
                    f"{int((summary_payload.get('token_usage') or {}).get('output_tokens', 0))}/"
                    f"{int((summary_payload.get('token_usage') or {}).get('total_tokens', 0))}"
                ),
                f"- Duration: {summary_payload.get('duration_sec', '-')}s",
            ]
        ),
        border_style="green" if summary_payload.get("all_checks_passed", True) else "yellow",
    )

    if not summary_payload.get("all_checks_passed", True) and not cfg.skip_failed_repos:
        emit_panel(
            (
                "One or more repository checks failed after retries; set "
                "execution.skip_failed_repos=true to continue with zero exit status."
            ),
            border_style="red",
        )
        return 1
    return 0


def run_pipeline_with_reporter(cfg: AgentConfig, reporter: ProgressReporter) -> int:
    from .reporting import get_reporter

    previous = get_reporter()
    set_reporter(reporter)
    try:
        return run_pipeline(cfg)
    finally:
        set_workspace_name(None)
        set_reporter(previous)
