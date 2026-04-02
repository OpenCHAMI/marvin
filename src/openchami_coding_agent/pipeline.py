"""Planning + execution pipeline composition."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from typing import Any

import yaml

from langgraph.checkpoint.sqlite import SqliteSaver

from .checkpoints import (
    checkpoint_dir,
    list_executor_checkpoints,
    resolve_resume_checkpoint,
    restore_executor_from_snapshot,
    sync_progress_for_snapshot_hierarchical,
    sync_progress_for_snapshot_single,
)
from .config import default_working_directory, ensure_repo, render_status
from .execution import execute_plan, extract_repo_sequence_from_plan
from .git_activity import collect_repo_git_activity
from .models import AgentConfig, PlanStep
from .plan_tracking import (
    compress_structured_plan,
    extract_plan_steps,
    initialize_plan_artifacts,
    plan_step_names,
    structured_plan_from_agent_response,
    structured_plan_from_data,
    structured_plan_from_markdown,
    tracker_markdown_path,
    update_tracker_markdown,
)
from .prompts import (
    build_planner_prompt_from_prefix,
    build_planner_prompt_prefix,
    build_workspace_analysis_prompt,
)
from .reporting import (
    ProgressReporter,
    emit_panel,
    emit_text,
    progress_heartbeat,
    render_run_progress,
    set_reporter,
    set_workspace_name,
)
from .summary_view import build_compact_execution_summary_lines, build_partial_success_payload
from .summary_view import (
    build_operator_feedback_template,
    extract_operator_feedback_notes,
)
from .ursa_compat import (
    get_agent_class,
    hash_plan,
    instantiate_agent,
    load_json_file,
    setup_llm,
    timed_input_with_countdown,
)
from .utils import (
    build_token_cache_summary,
    extract_agent_status_message,
    extract_agent_tokens,
    extract_brief_model_message,
    format_cache_hit_ratio,
    format_runtime_environment_summary,
    invoke_agent,
    progress_file,
    render_yaml_text,
    write_json_file,
    write_text_file,
)

_MAX_EXECUTION_MAIN_STEPS = 10


def make_agent_llm(config: AgentConfig, role: str):
    model_name = config.planner_model if role == "planner" else config.executor_model
    if not model_name:
        raise ValueError(f"No model configured for role '{role}'.")
    role_cfg = (config.defaults or {}).copy()
    role_cfg.update((config.planner if role == "planner" else config.execution) or {})
    return setup_llm(model_name, role_cfg, agent_name=config.agent_name)


def _load_structured_steps_from_plan_payload(workspace: Any, rel_path: str) -> list[PlanStep]:
    payload = load_json_file((workspace / rel_path).resolve(), {})
    if not isinstance(payload, dict):
        return []
    structured = structured_plan_from_data(
        payload.get("structured_plan") or payload.get("steps") or {}
    )
    return structured.steps


def _read_optional_workspace_text(workspace: Any, rel_path: str, *, max_chars: int = 8000) -> str:
    path = (workspace / rel_path).resolve()
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _load_optional_workspace_json(workspace: Any, rel_path: str) -> dict[str, Any]:
    path = (workspace / rel_path).resolve()
    if not path.exists() or not path.is_file():
        return {}
    payload = load_json_file(path, {})
    return payload if isinstance(payload, dict) else {}


def _extract_section_lines(markdown: str, heading: str) -> list[str]:
    lines = markdown.splitlines()
    normalized_heading = heading.strip().lower()
    capture = False
    in_fence = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if capture:
                collected.append(line)
            in_fence = not in_fence
            continue
        if not in_fence and re.match(r"^#{1,6}\s+", stripped):
            heading_text = re.sub(r"^#{1,6}\s+", "", stripped).strip().lower()
            if capture:
                break
            capture = heading_text == normalized_heading
            continue
        if capture:
            collected.append(line)
    return collected


def _extract_clarification_questions(markdown: str) -> list[str]:
    questions: list[str] = []
    for line in _extract_section_lines(markdown, "Clarifications Needed"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower() in {"- none", "none"}:
            return []
        match = re.match(r"^(?:[-*]|\d+[.)])\s+(.+)$", stripped)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                questions.append(candidate)
    return questions


def _extract_yaml_code_block(markdown: str) -> str:
    match = re.search(r"```yaml\s*(.*?)```", markdown, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_markdown_code_block(markdown: str, heading: str) -> str:
    section = "\n".join(_extract_section_lines(markdown, heading)).strip()
    if not section or section.lower() in {"- none", "none"}:
        return ""
    match = re.search(
        r"```(?:markdown|md)?\s*(.*?)```",
        section,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return section


def _merge_config_patch(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = {key: value for key, value in base.items()}
        for key, value in patch.items():
            if key in merged:
                merged[key] = _merge_config_patch(merged[key], value)
            else:
                merged[key] = value
        return merged
    return patch


def _build_recommended_config(cfg: AgentConfig, yaml_snippet: str) -> tuple[dict[str, Any] | None, str | None]:
    if not yaml_snippet.strip():
        return None, "Analysis did not include a suggested YAML snippet."
    try:
        patch_payload = yaml.safe_load(yaml_snippet)
    except yaml.YAMLError as exc:
        return None, f"Suggested YAML snippet could not be parsed: {exc}"

    if not isinstance(patch_payload, dict):
        return None, "Suggested YAML snippet must parse to a mapping at the top level."

    base_config = dict(cfg.raw_config or {})
    if not base_config and cfg.config_path and cfg.config_path.exists():
        loaded = yaml.safe_load(cfg.config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            base_config = dict(loaded)
    if not base_config:
        return None, "Original config could not be reconstructed for merging."

    return _merge_config_patch(base_config, patch_payload), None


def _ask_workspace_clarifications(cfg: AgentConfig, questions: list[str]) -> dict[str, str]:
    if not questions or not cfg.allow_user_prompts:
        return {}

    emit_panel(
        "Workspace analysis needs clarification before it can stop guessing. Naturally.",
        border_style="yellow",
    )
    answers: dict[str, str] = {}
    for index, question in enumerate(questions, start=1):
        response = input(f"[{index}/{len(questions)}] {question}\n> ").strip()
        answers[question] = response or "No clarification provided."
    return answers


def _workspace_analysis_evidence(cfg: AgentConfig) -> dict[str, Any]:
    workspace = cfg.workspace
    if workspace is None:
        raise RuntimeError("Workspace must be set before analysis.")

    summary_payload = _load_optional_workspace_json(workspace, cfg.summary_json)
    partial_success_payload = _load_optional_workspace_json(workspace, cfg.partial_success_json)
    if not partial_success_payload and isinstance(summary_payload.get("partial_success"), dict):
        partial_success_payload = dict(summary_payload["partial_success"])
    operator_feedback_path = (workspace / cfg.operator_feedback_markdown).resolve()
    operator_feedback_markdown = ""
    if operator_feedback_path.exists() and operator_feedback_path.is_file():
        operator_feedback_markdown = operator_feedback_path.read_text(encoding="utf-8").strip()
    progress_payload = _load_optional_workspace_json(workspace, cfg.executor_progress_json)
    plan_payload = _load_optional_workspace_json(workspace, cfg.plan_json)
    proposal_markdown = _read_optional_workspace_text(
        workspace, cfg.proposal_markdown, max_chars=12000
    )
    tracker_markdown = ""
    tracker_path = tracker_markdown_path(workspace)
    if tracker_path.exists():
        tracker_markdown = tracker_path.read_text(encoding="utf-8").strip()
        if len(tracker_markdown) > 12000:
            tracker_markdown = tracker_markdown[-12000:]

    checkpoints = [path.name for path in list_executor_checkpoints(workspace)]
    repo_activity = []
    for repo in cfg.repos:
        if not repo.path.exists():
            repo_activity.append(
                {
                    "repo": repo.name,
                    "role": repo.role_label,
                    "exists": False,
                    "note": f"Expected at {repo.path}, but the path is absent.",
                }
            )
            continue
        activity = collect_repo_git_activity(repo.name, repo.path)
        repo_activity.append(
            {
                "repo": repo.name,
                "role": repo.role_label,
                "exists": True,
                "is_git_repo": activity.is_git_repo,
                "branch": activity.branch,
                "changed_files": activity.changed_files,
                "added_lines": activity.added_lines,
                "deleted_lines": activity.deleted_lines,
                "last_commit": activity.last_commit,
                "recent_files": activity.recent_files,
            }
        )

    current_config = {
        "mode": cfg.mode,
        "planning": {"mode": cfg.planning_mode},
        "task": {
            "execute_after_plan": cfg.execute_after_plan,
            "confirm_before_execute": cfg.confirm_before_execute,
            "confirm_timeout_sec": cfg.confirm_timeout_sec,
        },
        "execution": {
            "max_parallel_checks": cfg.max_parallel_checks,
            "max_check_retries": cfg.max_check_retries,
            "skip_failed_repos": cfg.skip_failed_repos,
            "resume_execution_state": cfg.resume_execution_state,
            "commit_each_step": cfg.commit_each_step,
            "repo_order": cfg.repo_order,
            "repo_dependencies": cfg.repo_dependencies,
            "executor_agent": (cfg.execution or {}).get("executor_agent"),
        },
        "repos": [
            {
                "name": repo.name,
                "checkout": repo.checkout,
                "read_only": repo.read_only,
                "branch": repo.branch,
                "language": repo.language,
                "checks": repo.checks,
            }
            for repo in cfg.repos
        ],
    }
    run_trace_payload = summary_payload.get("run_trace") if isinstance(summary_payload, dict) else {}

    evidence_text = "\n\n".join(
        [
            "Current config excerpt:\n" + render_yaml_text(current_config).strip(),
            "Partial-success artifact:\n"
            + (
                render_yaml_text(partial_success_payload).strip()
                if partial_success_payload
                else "<missing>"
            ),
            "Structured run trace:\n"
            + (render_yaml_text(run_trace_payload).strip() if run_trace_payload else "<missing>"),
            "Operator feedback artifact:\n"
            + (operator_feedback_markdown or "<missing>"),
            "Execution summary JSON:\n"
            + (render_yaml_text(summary_payload).strip() if summary_payload else "<missing>"),
            "Execution progress JSON:\n"
            + (render_yaml_text(progress_payload).strip() if progress_payload else "<missing>"),
            "Plan JSON:\n"
            + (render_yaml_text(plan_payload).strip() if plan_payload else "<missing>"),
            "Plan tracker tail:\n" + (tracker_markdown or "<missing>"),
            "Proposal markdown tail:\n" + (proposal_markdown or "<missing>"),
            "Executor checkpoints:\n"
            + ("\n".join(f"- {name}" for name in checkpoints) if checkpoints else "<none>"),
            "Repository activity:\n"
            + render_yaml_text({"repos": repo_activity}).strip(),
        ]
    )

    return {
        "summary_payload": summary_payload,
        "run_trace": run_trace_payload,
        "partial_success_payload": partial_success_payload,
        "operator_feedback_markdown": operator_feedback_markdown,
        "progress_payload": progress_payload,
        "plan_payload": plan_payload,
        "tracker_markdown": tracker_markdown,
        "proposal_markdown": proposal_markdown,
        "checkpoints": checkpoints,
        "repo_activity": repo_activity,
        "current_config": current_config,
        "evidence_text": evidence_text,
    }


def analyze_workspace(cfg: AgentConfig) -> dict[str, Any]:
    workspace = cfg.workspace
    if workspace is None:
        raise RuntimeError("Workspace must be set before analysis.")

    started = time.time()
    evidence = _workspace_analysis_evidence(cfg)
    llm = make_agent_llm(cfg, "planner")
    planner_db = checkpoint_dir(workspace) / "planner_analysis_checkpoint.db"
    planner_conn = sqlite3.connect(str(planner_db), check_same_thread=False)
    planner_checkpointer = SqliteSaver(planner_conn)
    thread_id = f"{workspace.name}::analysis"
    planning_agent_class = get_agent_class("PlanningAgent")
    planner = instantiate_agent(
        planning_agent_class,
        llm=llm,
        checkpointer=planner_checkpointer,
        enable_metrics=True,
        metrics_dir="ursa_metrics",
        thread_id=thread_id,
        workspace=str(workspace),
    )
    planner.thread_id = thread_id

    clarification_answers: dict[str, str] = {}
    attempts: list[dict[str, Any]] = []

    def invoke_analysis(current_answers: dict[str, str]) -> str:
        latest_feedback = {"text": ""}
        prompt = build_workspace_analysis_prompt(
            cfg,
            workspace_evidence=evidence["evidence_text"],
            clarification_answers=(
                "\n".join(f"- {question}: {answer}" for question, answer in current_answers.items())
            ),
        )

        def planning_detail_provider() -> str:
            if latest_feedback["text"]:
                return latest_feedback["text"]
            return extract_agent_status_message(planner, fallback="Workspace analysis in progress.")

        planner_baseline = extract_agent_tokens(planner)
        render_run_progress(
            stage="planning",
            detail="Analyzing prior workspace and YAML config",
            planning_mode="hierarchical",
            total_repos=len(cfg.repos),
        )
        with progress_heartbeat(
            stage="planning",
            detail="Analyzing prior workspace and YAML config",
            planning_mode="hierarchical",
            detail_provider=planning_detail_provider,
            token_usage_provider=lambda: extract_agent_tokens(planner),
            total_repos_provider=lambda: len(cfg.repos),
            start_time=started,
            interval_sec=2.0,
        ) as emit_progress_update:
            def on_planner_feedback(text: str) -> None:
                latest_feedback["text"] = text
                emit_progress_update(text, agent_feedback_override=text)

            invocation = invoke_agent(
                planner,
                prompt,
                cfg.verbose_io,
                feedback_callback=on_planner_feedback,
            )

        delta = extract_agent_tokens(planner)
        attempts.append(
            {
                "prompt": prompt,
                "response": invocation.content,
                "token_usage": delta,
                "token_delta": {
                    key: max(0, int(delta.get(key, 0)) - int(planner_baseline.get(key, 0)))
                    for key in {"input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"}
                },
                "clarification_answers": dict(current_answers),
            }
        )
        return invocation.content

    analysis_markdown = invoke_analysis(clarification_answers)
    clarification_questions = _extract_clarification_questions(analysis_markdown)
    if clarification_questions and cfg.allow_user_prompts:
        clarification_answers = _ask_workspace_clarifications(cfg, clarification_questions)
        if clarification_answers:
            analysis_markdown = invoke_analysis(clarification_answers)
            clarification_questions = _extract_clarification_questions(analysis_markdown)

    duration_sec = round(time.time() - started, 2)
    yaml_snippet = _extract_yaml_code_block(analysis_markdown)
    operator_feedback_snippet = _extract_markdown_code_block(
        analysis_markdown, "Suggested Operator Feedback"
    )
    recommended_config, recommended_config_error = _build_recommended_config(cfg, yaml_snippet)
    payload = {
        "project": cfg.project,
        "workspace": str(workspace),
        "mode": cfg.mode,
        "analysis_planning_mode": "hierarchical",
        "clarification_questions": clarification_questions,
        "clarification_answers": clarification_answers,
        "suggested_yaml_snippet": yaml_snippet,
        "suggested_operator_feedback_markdown": operator_feedback_snippet,
        "recommended_config": recommended_config,
        "recommended_config_error": recommended_config_error,
        "analysis_markdown": analysis_markdown,
        "attempts": attempts,
        "evidence": {
            "summary_payload": evidence["summary_payload"],
            "progress_payload": evidence["progress_payload"],
            "plan_payload": evidence["plan_payload"],
            "checkpoints": evidence["checkpoints"],
            "repo_activity": evidence["repo_activity"],
        },
        "duration_sec": duration_sec,
        "token_usage": extract_agent_tokens(planner),
    }
    planner_conn.close()
    return payload


def generate_plan(cfg: AgentConfig) -> tuple[str, dict[str, Any]]:
    workspace = cfg.workspace
    if workspace is None:
        raise RuntimeError("Workspace must be set before plan generation.")

    llm = make_agent_llm(cfg, "planner")
    planner_db = checkpoint_dir(workspace) / "planner_checkpoint.db"
    planner_conn = sqlite3.connect(str(planner_db), check_same_thread=False)
    planner_checkpointer = SqliteSaver(planner_conn)
    thread_id = workspace.name
    planning_agent_class = get_agent_class("PlanningAgent")
    planner = instantiate_agent(
        planning_agent_class,
        llm=llm,
        checkpointer=planner_checkpointer,
        enable_metrics=True,
        metrics_dir="ursa_metrics",
        thread_id=thread_id,
        workspace=str(workspace),
    )
    planner.thread_id = thread_id
    planner_prompt_prefix = build_planner_prompt_prefix(cfg)
    prompt = build_planner_prompt_from_prefix(planner_prompt_prefix, cfg=cfg)
    latest_planner_feedback = {"text": ""}

    def planning_detail_provider() -> str:
        if latest_planner_feedback["text"]:
            return latest_planner_feedback["text"]
        return extract_agent_status_message(
            planner,
            fallback="Planning in progress.",
        )

    render_run_progress(
        stage="planning",
        detail="Generating implementation plan",
        total_repos=len(cfg.repos),
    )
    with progress_heartbeat(
        stage="planning",
        detail="Planning in progress.",
        detail_provider=planning_detail_provider,
        token_usage_provider=lambda: extract_agent_tokens(planner),
        total_repos_provider=lambda: len(cfg.repos),
    ) as emit_progress_update:
        def on_planner_feedback(text: str) -> None:
            latest_planner_feedback["text"] = text
            emit_progress_update(text, agent_feedback_override=text)

        invocation = invoke_agent(
            planner,
            prompt,
            cfg.verbose_io,
            feedback_callback=on_planner_feedback,
        )
    plan_markdown = invocation.content
    structured_plan = structured_plan_from_agent_response(
        invocation.raw_response,
        fallback_markdown=plan_markdown,
        source="planner",
    )
    compressed_plan = compress_structured_plan(
        structured_plan,
        max_steps=_MAX_EXECUTION_MAIN_STEPS,
        source="planner-compressed",
    )
    planner_message = extract_brief_model_message(
        plan_markdown,
        fallback="Plan generated.",
    )
    plan_hash_source = [step.to_payload() for step in compressed_plan.steps] or [plan_markdown]

    plan_payload = {
        "project": cfg.project,
        "mode": cfg.mode,
        "planning_mode": cfg.planning_mode,
        "workspace": str(cfg.workspace),
        "proposal_markdown": cfg.proposal_markdown,
        "plan_markdown": plan_markdown,
        "plan_hash": hash_plan(plan_hash_source),
        "structured_plan": compressed_plan.to_payload(),
        "steps": [step.to_payload() for step in compressed_plan.steps],
        "planner_step_count": len(structured_plan.steps),
        "execution_step_count": len(compressed_plan.steps),
        "plan_compression": {
            "applied": len(compressed_plan.steps) < len(structured_plan.steps),
            "original_step_count": len(structured_plan.steps),
            "compressed_step_count": len(compressed_plan.steps),
        },
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
        detail=planner_message,
        token_usage=plan_payload.get("token_usage") or {},
    )
    return plan_markdown, plan_payload


def run_pipeline(cfg: AgentConfig) -> int:
    workspace = cfg.workspace
    if workspace is None:
        raise RuntimeError("Workspace was not resolved from config.")

    set_workspace_name(workspace.name)

    os.chdir(workspace)
    emit_panel(format_runtime_environment_summary(), border_style="blue")
    emit_panel(
        f"Workspace containment engaged. Working directory pinned to: {workspace}",
        border_style="blue",
    )

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

    if cfg.mode == "analyze_workspace":
        cfg.planning_mode = "hierarchical"

    render_status(cfg)

    if cfg.raw_config:
        source_config_path = write_text_file(
            workspace,
            "artifacts/marvin_source_config.yaml",
            render_yaml_text(
                {
                    "config_path": str(cfg.config_path) if cfg.config_path else None,
                    "raw_config": cfg.raw_config,
                }
            ),
        )
        emit_text(f"Wrote source config snapshot: {source_config_path}")

    if cfg.mode == "analyze_workspace":
        emit_panel(
            "Analyzing the previous workspace run before anyone makes the YAML worse.",
            border_style="cyan",
        )
        analysis_payload = analyze_workspace(cfg)
        analysis_md_path = write_text_file(
            workspace,
            cfg.workspace_analysis_markdown,
            analysis_payload.get("analysis_markdown") or "",
        )
        analysis_json_path = write_json_file(
            workspace,
            cfg.workspace_analysis_json,
            analysis_payload,
        )
        recommended_config_path: Path | None = None
        recommended_operator_feedback_path: Path | None = None
        recommended_config_payload = analysis_payload.get("recommended_config")
        if isinstance(recommended_config_payload, dict):
            recommended_config_path = write_text_file(
                workspace,
                cfg.recommended_config_yaml,
                render_yaml_text(recommended_config_payload),
            )
        recommended_operator_feedback_payload = analysis_payload.get(
            "suggested_operator_feedback_markdown"
        )
        if isinstance(recommended_operator_feedback_payload, str) and recommended_operator_feedback_payload.strip():
            recommended_operator_feedback_path = write_text_file(
                workspace,
                cfg.recommended_operator_feedback_markdown,
                recommended_operator_feedback_payload.strip() + "\n",
            )
        emit_text(f"Wrote workspace analysis: {analysis_md_path}")
        emit_text(f"Wrote workspace analysis JSON: {analysis_json_path}")
        if recommended_config_path is not None:
            emit_text(f"Wrote recommended config: {recommended_config_path}")
        elif analysis_payload.get("recommended_config_error"):
            emit_panel(
                str(analysis_payload["recommended_config_error"]),
                border_style="yellow",
                title="Recommended config unavailable",
            )
        if recommended_operator_feedback_path is not None:
            emit_text(
                "Wrote recommended operator feedback: "
                f"{recommended_operator_feedback_path}"
            )
        if analysis_payload.get("clarification_questions") and not cfg.allow_user_prompts:
            emit_panel(
                "The analysis still has unanswered clarification questions. Non-interactive mode prevented asking them, because of course it did.",
                border_style="yellow",
            )
        return 0

    for repo in cfg.repos:
        ensure_repo(repo)

    run_cwd = default_working_directory(cfg)
    execution_repos = cfg.execution_repos
    if run_cwd is not None:
        os.chdir(run_cwd)
        if len(execution_repos) == 1:
            emit_panel(
                (
                    "Single-repo mode engaged. Execution working directory set "
                    f"to repo root: {run_cwd}"
                ),
                border_style="green",
            )
        else:
            emit_panel(
                f"Execution working directory selected: {run_cwd}",
                border_style="green",
            )

    plan_markdown: str | None = None
    structured_steps: list[PlanStep] = []

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
        emit_panel("Generating implementation proposal. Joy is optional.", border_style="cyan")
        plan_markdown, plan_payload = generate_plan(cfg)
        plan_md_path = write_text_file(workspace, cfg.proposal_markdown, plan_markdown)
        plan_json_path = write_json_file(workspace, cfg.plan_json, plan_payload)
        structured_plan = structured_plan_from_data(
            plan_payload.get("structured_plan") or plan_payload.get("steps") or {}
        )
        structured_steps = structured_plan.steps
        compression_info = plan_payload.get("plan_compression") or {}
        plan_steps = initialize_plan_artifacts(
            workspace,
            plan_markdown,
            structured_plan=structured_steps,
        )
        tracker_path = update_tracker_markdown(
            workspace=workspace,
            stage="planning",
            activity=(
                "Planner update: "
                f"{extract_brief_model_message(plan_markdown, 'Plan generated.')}"
            ),
            plan_steps=plan_steps,
            completed_step_indices=set(),
            notes=[
                f"Proposal written to {cfg.proposal_markdown}",
                f"Plan JSON written to {cfg.plan_json}",
                (
                    "Execution plan compressed from "
                    f"{compression_info.get('original_step_count')} to "
                    f"{compression_info.get('compressed_step_count')} review units."
                )
                if compression_info.get("applied")
                else "Execution plan kept planner step count unchanged.",
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
        structured_steps = _load_structured_steps_from_plan_payload(workspace, cfg.plan_json)
        if not structured_steps:
            structured_steps = structured_plan_from_markdown(plan_markdown).steps
        initialize_plan_artifacts(
            workspace,
            plan_markdown,
            structured_plan=structured_steps,
        )

    if not plan_markdown:
        raise RuntimeError("No plan markdown available for execution.")

    if selected_resume_checkpoint is not None and cfg.mode in {"execute", "plan_and_execute"}:
        plan_sig = hash_plan([step.to_payload() for step in structured_steps] or [plan_markdown])
        if cfg.planning_mode == "hierarchical":
            sync_progress_for_snapshot_hierarchical(
                workspace,
                selected_resume_checkpoint,
                plan_sig,
                cfg.executor_progress_json,
            )
        else:
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
            (
                f"Shall we proceed with execution? [Y/n] "
                f"(auto-yes in {cfg.confirm_timeout_sec}s): "
            ),
            cfg.confirm_timeout_sec,
        )
        if response and response.strip().lower() in {"n", "no"}:
            emit_panel(
                "Execution cancelled by user. Sensible caution noted.",
                border_style="yellow",
            )
            return 1

    emit_panel("Executing approved plan with resigned precision.", border_style="magenta")
    plan_steps = plan_step_names(structured_steps) or extract_plan_steps(plan_markdown)
    update_tracker_markdown(
        workspace=workspace,
        stage="execution",
        activity="Execution started; applying plan steps and monitoring checkpoints.",
        plan_steps=plan_steps,
        completed_step_indices=set(),
        notes=["Executor launched."],
        reconciliation="Execution in progress.",
    )
    summary_payload = execute_plan(
        cfg,
        plan_markdown,
        executor_llm=make_agent_llm(cfg, "executor"),
        planner_llm=make_agent_llm(cfg, "planner"),
        structured_plan=structured_steps,
    )
    partial_success_payload = summary_payload.get("partial_success") or build_partial_success_payload(
        summary_payload
    )
    operator_feedback_path = (workspace / cfg.operator_feedback_markdown).resolve()
    existing_operator_feedback = (
        operator_feedback_path.read_text(encoding="utf-8")
        if operator_feedback_path.exists() and operator_feedback_path.is_file()
        else ""
    )
    operator_feedback_payload = existing_operator_feedback
    if not extract_operator_feedback_notes(existing_operator_feedback):
        operator_feedback_payload = build_operator_feedback_template(
            workspace.name,
            partial_success_payload,
        )
    cache_summary = summary_payload.get("token_cache_summary") or build_token_cache_summary(
        summary_payload.get("token_usage") or {}
    )
    summary_path = write_json_file(workspace, cfg.summary_json, summary_payload)
    partial_success_path = write_json_file(
        workspace,
        cfg.partial_success_json,
        partial_success_payload,
    )
    operator_feedback_written_path = write_text_file(
        workspace,
        cfg.operator_feedback_markdown,
        operator_feedback_payload,
    )
    emit_text(f"Wrote execution summary: {summary_path}")
    emit_text(f"Wrote partial-success artifact: {partial_success_path}")
    emit_text(f"Wrote operator feedback template: {operator_feedback_written_path}")
    progress_path = progress_file(workspace, cfg.executor_progress_json)
    emit_text(f"Wrote execution progress: {progress_path}")
    emit_panel(
        "\n".join(build_compact_execution_summary_lines(workspace.name, summary_payload)),
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
