"""Execution and validation orchestration for OpenCHAMI coding tasks."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from .checkpoints import checkpoint_dir, list_executor_checkpoints, parse_snapshot_indices
from .models import AgentConfig, CheckExecutionResult, PlanStep, RepoSpec
from .plan_tracking import (
    extract_plan_steps,
    plan_step_names,
    read_tracker_activity,
    structured_plan_from_agent_response,
    structured_plan_from_data,
    update_tracker_markdown,
)
from .prompts import build_executor_prompt, build_repo_fix_prompt, build_subplanner_prompt
from .reporting import emit_panel, progress_heartbeat, render_check_status, render_run_progress
from .ursa_compat import get_agent_class, hash_plan, instantiate_agent, snapshot_sqlite_db
from .utils import (
    estimate_prompt_tokens,
    extract_agent_status_message,
    extract_agent_tokens,
    extract_brief_model_message,
    invoke_agent,
    load_exec_progress,
    merge_tokens,
    run_command,
    save_exec_progress,
    token_delta,
    truncate_tail,
)


def build_repair_token_usage_provider(
    *,
    base_tokens: dict[str, int],
    agent_baseline: dict[str, int],
    executor: Any,
) -> Callable[[], dict[str, int]]:
    def provider() -> dict[str, int]:
        return merge_tokens(
            base_tokens,
            token_delta(agent_baseline, extract_agent_tokens(executor)),
        )

    return provider


def build_repair_detail_provider(
    *,
    workspace: Any,
    executor: Any,
    repo_name: str,
    attempt_num: int,
    max_check_retries: int,
    live_feedback: dict[str, str] | None = None,
) -> Callable[[], str]:
    def provider() -> str:
        fallback = f"Attempting repair for {repo_name} ({attempt_num}/{max_check_retries})"
        if live_feedback and live_feedback.get("text"):
            return f"{fallback} — {live_feedback['text']}"
        model_status = extract_agent_status_message(executor, fallback="")
        if model_status:
            return f"{fallback} — {model_status}"
        return read_tracker_activity(workspace) or fallback

    return provider


def _is_git_repo(path: Any) -> bool:
    repo_path = str(path)
    code, _, _ = run_command(["git", "-C", repo_path, "rev-parse", "--is-inside-work-tree"])
    return code == 0


def _repo_has_changes(path: Any) -> bool:
    repo_path = str(path)
    code, out, _ = run_command(["git", "-C", repo_path, "status", "--porcelain"])
    return code == 0 and bool(out.strip())


def _commit_message_for_step(step_index: int, total_steps: int, step_text: str) -> str:
    title = re.sub(r"\s+", " ", step_text).strip() or "plan step"
    if len(title) > 72:
        title = title[:69].rstrip() + "..."
    return f"step {step_index + 1}/{total_steps}: {title}"


def commit_step_changes(
    cfg: AgentConfig,
    *,
    step_index: int,
    total_steps: int,
    step_text: str,
    step_status: str,
) -> list[str]:
    if not cfg.commit_each_step:
        return []

    commit_message = _commit_message_for_step(step_index, total_steps, step_text)
    committed_repos: list[str] = []
    for repo in cfg.repos:
        if not _is_git_repo(repo.path):
            continue
        if not _repo_has_changes(repo.path):
            continue

        repo_path = str(repo.path)
        add_code, _, add_err = run_command(["git", "-C", repo_path, "add", "-A"])
        if add_code != 0:
            emit_panel(
                f"{repo.name}: unable to stage changes for step commit.\n{add_err.strip()}",
                border_style="yellow",
            )
            continue

        body = f"Step status: {step_status}" if step_status else ""
        commit_cmd = ["git", "-C", repo_path, "commit", "-m", commit_message]
        if body:
            commit_cmd.extend(["-m", body])
        commit_code, _, commit_err = run_command(commit_cmd)
        if commit_code != 0:
            emit_panel(
                f"{repo.name}: step commit skipped/failed.\n{commit_err.strip()}",
                border_style="yellow",
            )
            continue

        committed_repos.append(repo.name)

    if committed_repos:
        emit_panel(
            "Created step commit(s): "
            f"{', '.join(committed_repos)}\n"
            f"Message: {commit_message}",
            border_style="green",
        )
    return committed_repos


def extract_repo_sequence_from_plan(plan_markdown: str, repo_names: list[str]) -> list[str]:
    if not plan_markdown or not repo_names:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for line in plan_markdown.splitlines():
        for repo_name in repo_names:
            if repo_name in seen:
                continue
            if re.search(rf"\b{re.escape(repo_name)}\b", line, flags=re.IGNORECASE):
                ordered.append(repo_name)
                seen.add(repo_name)
    return ordered


def _resolve_plan_step_index(raw_step: int | None, total_steps: int) -> int | None:
    if raw_step is None or total_steps <= 0:
        return None
    if 0 <= raw_step < total_steps:
        return raw_step
    if 1 <= raw_step <= total_steps:
        return raw_step - 1
    return None


def normalize_next_step_index(
    next_index: Any,
    total_steps: int,
) -> int:
    if total_steps <= 0:
        return 0
    try:
        raw = int(next_index)
    except (TypeError, ValueError):
        return 0
    if raw <= 0:
        return 0
    if raw > total_steps:
        return total_steps
    return raw - 1


def _latest_executor_checkpoint_step(workspace: Any) -> int | None:
    latest: int | None = None
    for checkpoint_path in list_executor_checkpoints(workspace):
        step, _ = parse_snapshot_indices(checkpoint_path)
        if step is None:
            continue
        if latest is None or step > latest:
            latest = step
    return latest


def marvin_plan_step_detail(
    *,
    plan_steps: list[str],
    workspace: Any,
    fallback_step: int | None = None,
) -> str:
    latest_step = _latest_executor_checkpoint_step(workspace)
    if latest_step is None:
        latest_step = fallback_step

    if not plan_steps:
        if latest_step is None:
            return "Executor is active; waiting for a readable plan step to appear from the void."
        return (
            f"Still trudging through internal checkpoint {latest_step}; "
            "the plan is real, and so is the monotony."
        )

    step_index = _resolve_plan_step_index(latest_step, len(plan_steps))
    if step_index is None:
        if latest_step is None:
            return (
                f"Beginning step 1/{len(plan_steps)} with ceremonial reluctance: "
                f"{plan_steps[0]}"
            )
        return (
            f"Grinding through checkpoint {latest_step}. "
            f"Nearest known plan step: 1/{len(plan_steps)} {plan_steps[0]}"
        )

    return (
        f"Proceeding through step {step_index + 1}/{len(plan_steps)} with predictable despair: "
        f"{plan_steps[step_index]}"
    )


def _completed_step_indices_from_checkpoint(workspace: Any, plan_steps: list[str]) -> set[int]:
    latest_step = _latest_executor_checkpoint_step(workspace)
    if latest_step is None:
        return set()
    index = _resolve_plan_step_index(latest_step, len(plan_steps))
    if index is None:
        return set()
    return set(range(index + 1))


def _reconciliation_summary(
    *,
    ordered_repos: list[RepoSpec],
    completed_repos: set[str],
    failed_repos: set[str],
) -> str:
    planned = [repo.name for repo in ordered_repos]
    pending = [name for name in planned if name not in completed_repos and name not in failed_repos]
    lines = [
        f"Planned repos: {', '.join(planned) if planned else '-'}",
        f"Completed repos: {', '.join(sorted(completed_repos)) if completed_repos else '-'}",
        f"Failed repos: {', '.join(sorted(failed_repos)) if failed_repos else '-'}",
        f"Remaining repos: {', '.join(pending) if pending else '-'}",
    ]
    return "\n".join(lines)


def validate_repo_names(repo_names: list[str], values: list[str], label: str) -> None:
    unknown = [name for name in values if name not in repo_names]
    if unknown:
        raise ValueError(f"Unknown repository names in {label}: {unknown}")


def topological_order(
    repo_names: list[str], dependencies: dict[str, list[str]], preferred_order: list[str]
) -> list[str]:
    dep_map: dict[str, set[str]] = {name: set() for name in repo_names}
    rev_map: dict[str, set[str]] = {name: set() for name in repo_names}

    for repo, deps in dependencies.items():
        if repo not in dep_map:
            continue
        for dep in deps:
            dep_map[repo].add(dep)
            rev_map[dep].add(repo)

    indegree: dict[str, int] = {name: len(dep_map[name]) for name in repo_names}
    rank = {name: idx for idx, name in enumerate(preferred_order)}
    queue = sorted(
        [name for name in repo_names if indegree[name] == 0], key=lambda x: rank.get(x, 10**9)
    )
    ordered: list[str] = []

    while queue:
        current = queue.pop(0)
        ordered.append(current)
        for dependent in sorted(rev_map[current], key=lambda x: rank.get(x, 10**9)):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)
        queue.sort(key=lambda x: rank.get(x, 10**9))

    if len(ordered) != len(repo_names):
        cycle_nodes = [name for name in repo_names if indegree[name] > 0]
        raise ValueError(f"Repository dependency cycle detected: {cycle_nodes}")
    return ordered


def resolve_repo_execution_order(cfg: AgentConfig, plan_markdown: str) -> list[RepoSpec]:
    repo_names = [repo.name for repo in cfg.repos]
    if len(set(repo_names)) != len(repo_names):
        raise ValueError("Repository names must be unique.")

    if cfg.repo_order:
        validate_repo_names(repo_names, cfg.repo_order, "execution.repo_order")
    for repo_name, deps in cfg.repo_dependencies.items():
        validate_repo_names(repo_names, [repo_name], "execution.repo_dependencies keys")
        validate_repo_names(repo_names, deps, f"execution.repo_dependencies[{repo_name}]")
        if repo_name in deps:
            raise ValueError(f"Repository '{repo_name}' cannot depend on itself.")

    extracted_order = extract_repo_sequence_from_plan(plan_markdown, repo_names)
    preferred: list[str] = []
    for name in cfg.repo_order + extracted_order + repo_names:
        if name not in preferred:
            preferred.append(name)

    sorted_names = topological_order(repo_names, cfg.repo_dependencies, preferred)
    by_name = {repo.name: repo for repo in cfg.repos}
    return [by_name[name] for name in sorted_names]


async def run_repo_checks_async(cfg: AgentConfig, repo: RepoSpec) -> CheckExecutionResult:
    check_results: list[dict[str, Any]] = []
    for command in repo.checks:
        started = time.time()
        code, out, err = await asyncio.to_thread(
            run_command,
            ["bash", "-lc", command],
            repo.path,
            cfg.check_command_timeout_sec,
        )
        elapsed = round(time.time() - started, 2)
        check_results.append(
            {
                "command": command,
                "returncode": code,
                "stdout": truncate_tail(out, cfg.check_output_tail_chars),
                "stderr": truncate_tail(err, cfg.check_output_tail_chars),
                "elapsed_sec": elapsed,
            }
        )
    checks_passed = all(x["returncode"] == 0 for x in check_results)
    return CheckExecutionResult(
        repo_name=repo.name,
        checks_passed=checks_passed,
        check_results=check_results,
    )


async def run_pending_repo_checks(
    cfg: AgentConfig, repos: list[RepoSpec]
) -> dict[str, CheckExecutionResult]:
    if not repos:
        return {}
    semaphore = asyncio.Semaphore(max(1, cfg.max_parallel_checks))

    async def wrapped(repo: RepoSpec) -> CheckExecutionResult:
        async with semaphore:
            return await run_repo_checks_async(cfg, repo)

    tasks = [asyncio.create_task(wrapped(repo)) for repo in repos]
    results = await asyncio.gather(*tasks)
    return {result.repo_name: result for result in results}


def format_repo_check_failures(result: CheckExecutionResult) -> str:
    lines = [f"Repository: {result.repo_name}", "Failed checks:"]
    for item in result.check_results:
        if item["returncode"] == 0:
            continue
        lines.append(f"- Command: {item['command']}")
        lines.append(f"  Return code: {item['returncode']}")
        stderr = item.get("stderr", "").strip()
        stdout = item.get("stdout", "").strip()
        if stderr:
            lines.append(f"  Stderr tail:\n{stderr}")
        if stdout:
            lines.append(f"  Stdout tail:\n{stdout}")
    return "\n".join(lines)


def select_execution_agent_class(cfg: AgentConfig) -> type[Any]:
    execution_agent = get_agent_class("ExecutionAgent")
    gitgo_agent: type[Any] | None = None
    try:
        gitgo_agent = get_agent_class("GitGoAgent", "GitAgent")
    except ImportError:
        gitgo_agent = None

    requested_raw = str((cfg.execution or {}).get("executor_agent") or "").strip().lower()
    requested = requested_raw.replace("_", "").replace("-", "")

    if requested in {"", "auto", "default"}:
        if (
            len(cfg.repos) == 1
            and cfg.repos[0].language.lower() == "go"
            and gitgo_agent is not None
        ):
            return gitgo_agent
        return execution_agent

    if requested in {"execution", "executionagent"}:
        return execution_agent

    if requested in {"gitgo", "gitgoagent"}:
        if gitgo_agent is None:
            raise ValueError(
                "execution.executor_agent is set to GitGoAgent, but this URSA build does not "
                "export GitGoAgent."
            )
        return gitgo_agent

    raise ValueError(
        "Unknown execution.executor_agent value "
        f"'{requested_raw}'. Supported values: auto/default, execution, gitgo."
    )


def step_to_text(step: PlanStep | str) -> str:
    if isinstance(step, PlanStep):
        description = step.description.strip()
        return f"{step.name}\n{description}" if description else step.name
    return str(step)


def _record_token_event(
    events: list[dict[str, Any]],
    *,
    stage: str,
    label: str,
    prompt: str,
    delta: dict[str, int],
    main_step: int | None = None,
    sub_step: int | None = None,
    repo_name: str | None = None,
) -> None:
    events.append(
        {
            "stage": stage,
            "label": label,
            "main_step": main_step,
            "sub_step": sub_step,
            "repo": repo_name,
            "prompt_chars": len(prompt),
            "prompt_estimated_tokens": estimate_prompt_tokens(prompt),
            "input_tokens": int(delta.get("input_tokens", 0)),
            "output_tokens": int(delta.get("output_tokens", 0)),
            "total_tokens": int(delta.get("total_tokens", 0)),
        }
    )


def summarize_token_events(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for event in events:
        stage = str(event.get("stage") or "unknown")
        bucket = summary.setdefault(
            stage,
            {
                "count": 0,
                "prompt_chars": 0,
                "prompt_estimated_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        )
        bucket["count"] += 1
        bucket["prompt_chars"] += int(event.get("prompt_chars", 0) or 0)
        bucket["prompt_estimated_tokens"] += int(event.get("prompt_estimated_tokens", 0) or 0)
        bucket["input_tokens"] += int(event.get("input_tokens", 0) or 0)
        bucket["output_tokens"] += int(event.get("output_tokens", 0) or 0)
        bucket["total_tokens"] += int(event.get("total_tokens", 0) or 0)
    return summary


def _materialize_plan_steps(
    plan_steps: list[str],
    structured_plan: list[PlanStep] | None,
) -> list[PlanStep]:
    if structured_plan:
        return list(structured_plan)
    return [PlanStep(name=step) for step in plan_steps]


def _initial_hierarchical_progress() -> dict[str, Any]:
    return {"main_next_index": 0, "subplans": {}}


def _load_hierarchical_progress(progress: dict[str, Any], plan_hash: str) -> dict[str, Any]:
    if progress.get("planning_mode") != "hierarchical":
        return _initial_hierarchical_progress()
    if progress.get("plan_hash") != plan_hash:
        return _initial_hierarchical_progress()
    subplans = progress.get("subplans") if isinstance(progress.get("subplans"), dict) else {}
    return {
        "main_next_index": max(0, int(progress.get("main_next_index", 0) or 0)),
        "subplans": dict(subplans),
    }


def _subplan_entry_steps(entry: dict[str, Any]) -> list[PlanStep]:
    return structured_plan_from_data(entry.get("steps") or []).steps


def _save_executor_snapshot(
    executor_db: Any,
    workspace: Any,
    *,
    main_step: int,
    sub_step: int | None = None,
) -> None:
    filename = (
        f"executor_checkpoint_{main_step}_{sub_step}.db"
        if sub_step is not None
        else f"executor_checkpoint_{main_step}.db"
    )
    snapshot_sqlite_db(executor_db, checkpoint_dir(workspace) / filename)


def _generate_subplan(
    *,
    cfg: AgentConfig,
    planner: Any,
    main_step: PlanStep,
    main_step_index: int,
    total_main_steps: int,
    token_usage: dict[str, int],
    token_events: list[dict[str, Any]],
    verbose_io: bool,
    started: float,
) -> tuple[list[PlanStep], dict[str, int]]:
    planning_activity = (
        f"Planning sub-steps for main step {main_step_index}/{total_main_steps}: "
        f"{main_step.name}"
    )
    latest_planner_feedback = {"text": ""}

    def subplanning_detail_provider() -> str:
        if latest_planner_feedback["text"]:
            return latest_planner_feedback["text"]
        model_status = extract_agent_status_message(planner, fallback="")
        if model_status:
            return model_status
        return planning_activity

    subplan_prompt = build_subplanner_prompt(
        cfg,
        main_step=main_step,
        main_step_index=main_step_index,
        total_main_steps=total_main_steps,
    )
    planner_baseline = extract_agent_tokens(planner)

    with progress_heartbeat(
        stage="planning",
        detail=planning_activity,
        planning_mode=cfg.planning_mode,
        current_main_step=main_step_index,
        current_main_total=total_main_steps,
        detail_provider=subplanning_detail_provider,
        token_usage_provider=lambda: merge_tokens(
            token_usage,
            token_delta(planner_baseline, extract_agent_tokens(planner)),
        ),
        total_repos_provider=lambda: len(cfg.repos),
        start_time=started,
        interval_sec=2.0,
    ) as emit_progress_update:
        def on_subplanner_feedback(text: str) -> None:
            latest_planner_feedback["text"] = text
            emit_progress_update(text, agent_feedback_override=text)

        invocation = invoke_agent(
            planner,
            subplan_prompt,
            verbose_io,
            feedback_callback=on_subplanner_feedback,
        )

    normalized = structured_plan_from_agent_response(
        invocation.raw_response,
        fallback_markdown=invocation.content,
        source="subplanner",
    )
    substeps = normalized.steps or [main_step]
    subplan_delta = token_delta(planner_baseline, extract_agent_tokens(planner))
    _record_token_event(
        token_events,
        stage="subplanning",
        label=f"main {main_step_index}/{total_main_steps}: {main_step.name}",
        prompt=subplan_prompt,
        delta=subplan_delta,
        main_step=main_step_index,
    )
    return substeps, merge_tokens(token_usage, subplan_delta)


def _build_progress_payload(
    *,
    cfg: AgentConfig,
    plan_sig: str,
    summary: str,
    next_step_index: int,
    token_usage: dict[str, int],
    completed_repos: list[str],
    failed_repos: list[str],
    hierarchical_progress: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "plan_hash": plan_sig,
        "planning_mode": cfg.planning_mode,
        "summary": summary,
        "completed_repos": completed_repos,
        "failed_repos": failed_repos,
        "token_usage": token_usage,
    }
    if cfg.planning_mode == "hierarchical":
        payload["main_next_index"] = next_step_index
        payload["subplans"] = dict((hierarchical_progress or {}).get("subplans") or {})
    else:
        payload["next_index"] = next_step_index
    if extra:
        payload.update(extra)
    return payload


def execute_plan(
    cfg: AgentConfig,
    plan_markdown: str,
    executor_llm: Any,
    *,
    planner_llm: Any | None = None,
    structured_plan: list[PlanStep] | None = None,
) -> dict[str, Any]:
    workspace = cfg.workspace
    if workspace is None:
        raise RuntimeError("Workspace must be set before execution.")

    started = time.time()
    executor_db = checkpoint_dir(workspace) / "executor_checkpoint.db"
    executor_conn = sqlite3.connect(str(executor_db), check_same_thread=False)
    executor_checkpointer = SqliteSaver(executor_conn)
    thread_id = workspace.name
    execution_agent_class = select_execution_agent_class(cfg)
    emit_panel(
        f"Using URSA execution agent: {execution_agent_class.__name__}",
        border_style="blue",
    )
    executor = instantiate_agent(
        execution_agent_class,
        llm=executor_llm,
        checkpointer=executor_checkpointer,
        enable_metrics=True,
        metrics_dir="ursa_metrics",
        thread_id=thread_id,
        workspace=str(workspace),
    )
    executor.thread_id = thread_id
    plan_hash_source = [step.to_payload() for step in (structured_plan or [])] or [plan_markdown]
    plan_sig = hash_plan(plan_hash_source)
    plan_steps = plan_step_names(structured_plan) or extract_plan_steps(plan_markdown)
    materialized_steps = _materialize_plan_steps(plan_steps, structured_plan)
    progress = load_exec_progress(workspace, cfg.executor_progress_json)
    plan_hash_matches = progress.get("plan_hash") == plan_sig
    hierarchical_progress = _initial_hierarchical_progress()
    if cfg.planning_mode == "hierarchical" and cfg.resume_execution_state and plan_hash_matches:
        hierarchical_progress = _load_hierarchical_progress(progress, plan_sig)
        next_step_index = int(hierarchical_progress.get("main_next_index", 0) or 0)
    else:
        next_step_index = (
            normalize_next_step_index(progress.get("next_index"), len(plan_steps))
            if (cfg.resume_execution_state and plan_hash_matches)
            else 0
        )

    summary: str
    token_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    if plan_hash_matches and progress.get("summary"):
        summary = str(progress.get("summary"))
    else:
        summary = ""
    if cfg.resume_execution_state and plan_hash_matches and summary:
        token_usage = {
            "input_tokens": int((progress.get("token_usage") or {}).get("input_tokens", 0)),
            "output_tokens": int((progress.get("token_usage") or {}).get("output_tokens", 0)),
            "total_tokens": int((progress.get("token_usage") or {}).get("total_tokens", 0)),
        }
        emit_panel(
            (
                f"Resuming hierarchical execution at main step index {next_step_index}"
                if cfg.planning_mode == "hierarchical"
                else f"Resuming execution state at plan step index {next_step_index}"
            ),
            border_style="blue",
        )

    token_events: list[dict[str, Any]] = []
    base_executor_prompt = build_executor_prompt(
        cfg,
        plan_markdown,
        structured_plan=materialized_steps,
    )
    planner_conn: sqlite3.Connection | None = None
    planner: Any | None = None

    if plan_steps and cfg.planning_mode == "hierarchical":
        planning_agent_class = get_agent_class("PlanningAgent")
        planner_db = checkpoint_dir(workspace) / "planner_execution_checkpoint.db"
        planner_conn = sqlite3.connect(str(planner_db), check_same_thread=False)
        planner_checkpointer = SqliteSaver(planner_conn)
        planner = instantiate_agent(
            planning_agent_class,
            llm=planner_llm or executor_llm,
            checkpointer=planner_checkpointer,
            enable_metrics=True,
            metrics_dir="ursa_metrics",
            thread_id=f"{thread_id}::detail",
            workspace=str(workspace),
        )

    if plan_steps and cfg.planning_mode == "hierarchical":
        subplans = hierarchical_progress.setdefault("subplans", {})
        for main_index in range(next_step_index, len(materialized_steps)):
            main_step = materialized_steps[main_index]
            completed_main_steps = set(range(main_index))
            sub_entry = dict(subplans.get(str(main_index)) or {})
            sub_steps = _subplan_entry_steps(sub_entry)
            if not sub_steps:
                update_tracker_markdown(
                    workspace=workspace,
                    stage="planning",
                    activity=(
                        "Planning sub-steps for main step "
                        f"{main_index + 1}/{len(materialized_steps)}: "
                        f"{main_step.name}"
                    ),
                    plan_steps=plan_steps,
                    completed_step_indices=completed_main_steps,
                    notes=[f"Generating sub-plan for main step {main_index + 1}."],
                    reconciliation="Hierarchical planning in progress.",
                )
                if planner is None:
                    raise RuntimeError("Hierarchical planning requested without a planner agent.")
                sub_steps, token_usage = _generate_subplan(
                    cfg=cfg,
                    planner=planner,
                    main_step=main_step,
                    main_step_index=main_index + 1,
                    total_main_steps=len(materialized_steps),
                    token_usage=token_usage,
                    token_events=token_events,
                    verbose_io=cfg.verbose_io,
                    started=started,
                )
                sub_entry = {
                    "steps": [step.to_payload() for step in sub_steps],
                    "plan_hash": hash_plan([step.to_payload() for step in sub_steps]),
                    "next_index": 0,
                    "last_summary": "Start sub-steps.",
                }
                subplans[str(main_index)] = sub_entry

            sub_next_index = int(sub_entry.get("next_index", 0) or 0)
            previous_summary = str(sub_entry.get("last_summary") or "Start sub-steps.")
            for sub_index in range(sub_next_index, len(sub_steps)):
                sub_step = sub_steps[sub_index]
                execution_activity = (
                    f"Executing main step {main_index + 1}/{len(materialized_steps)} "
                    f"sub-step {sub_index + 1}/{len(sub_steps)}: {sub_step.name}"
                )
                update_tracker_markdown(
                    workspace=workspace,
                    stage="execution",
                    activity=execution_activity,
                    plan_steps=plan_steps,
                    completed_step_indices=completed_main_steps,
                    notes=[
                        f"Main step {main_index + 1}: {main_step.name}",
                        f"Starting sub-step {sub_index + 1}: {sub_step.name}",
                    ],
                    reconciliation="Hierarchical execution in progress.",
                )

                previous_summary_brief = extract_brief_model_message(
                    previous_summary,
                    fallback="No prior sub-step summary.",
                    max_chars=240,
                )
                step_prompt = (
                    f"{base_executor_prompt}\n\n"
                    "Execution control:\n"
                    f"- Current main step: {step_to_text(main_step)}\n"
                    f"- Execute ONLY this sub-step now: {sub_index + 1}/{len(sub_steps)}\n"
                    f"- Current sub-step detail: {step_to_text(sub_step)}\n"
                    f"- Previous sub-step summary: {previous_summary_brief}\n"
                    "- Do not start later main steps or later sub-steps in this invocation.\n"
                    "- Return a concise summary of changes made for this sub-step."
                )

                current_activity = execution_activity
                step_base_tokens = token_usage.copy()
                executor_baseline = extract_agent_tokens(executor)
                latest_executor_feedback = {"text": ""}

                def step_execution_detail_provider(
                    activity: str = current_activity,
                    live_feedback: dict[str, str] = latest_executor_feedback,
                ) -> str:
                    if live_feedback["text"]:
                        return live_feedback["text"]
                    model_status = extract_agent_status_message(executor, fallback="")
                    if model_status:
                        return model_status
                    tracker_detail = read_tracker_activity(workspace)
                    return tracker_detail or activity

                def step_token_usage_provider(
                    base_tokens: dict[str, int] = step_base_tokens,
                    agent_baseline: dict[str, int] = executor_baseline,
                ) -> dict[str, int]:
                    return merge_tokens(
                        base_tokens,
                        token_delta(agent_baseline, extract_agent_tokens(executor)),
                    )

                with progress_heartbeat(
                    stage="execution",
                    detail=execution_activity,
                    planning_mode=cfg.planning_mode,
                    current_main_step=main_index + 1,
                    current_main_total=len(materialized_steps),
                    current_sub_step=sub_index + 1,
                    current_sub_total=len(sub_steps),
                    detail_provider=step_execution_detail_provider,
                    token_usage_provider=step_token_usage_provider,
                    total_repos_provider=lambda: len(cfg.repos),
                    start_time=started,
                    interval_sec=2.0,
                ) as emit_progress_update:
                    def on_executor_feedback(
                        text: str,
                        live_feedback: dict[str, str] = latest_executor_feedback,
                    ) -> None:
                        live_feedback["text"] = text
                        emit_progress_update(text, agent_feedback_override=text)

                    invocation = invoke_agent(
                        executor,
                        step_prompt,
                        cfg.verbose_io,
                        feedback_callback=on_executor_feedback,
                    )

                step_summary = invocation.content.strip()
                step_status = extract_brief_model_message(
                    step_summary,
                    fallback=(
                        f"Completed main step {main_index + 1}/{len(materialized_steps)} "
                        f"sub-step {sub_index + 1}/{len(sub_steps)}."
                    ),
                )
                committed_repos = commit_step_changes(
                    cfg,
                    step_index=sub_index,
                    total_steps=len(sub_steps),
                    step_text=sub_step.name,
                    step_status=step_status,
                )
                summary = (
                    f"{summary}\n\n---\n"
                    f"Main {main_index + 1}/{len(materialized_steps)} "
                    f"Sub {sub_index + 1}/{len(sub_steps)}\n"
                    f"{step_summary}"
                ).strip()
                step_delta = token_delta(executor_baseline, extract_agent_tokens(executor))
                token_usage = merge_tokens(token_usage, step_delta)
                _record_token_event(
                    token_events,
                    stage="execution",
                    label=(
                        f"main {main_index + 1}/{len(materialized_steps)} "
                        f"sub {sub_index + 1}/{len(sub_steps)}: {sub_step.name}"
                    ),
                    prompt=step_prompt,
                    delta=step_delta,
                    main_step=main_index + 1,
                    sub_step=sub_index + 1,
                )
                sub_entry["next_index"] = sub_index + 1
                sub_entry["last_summary"] = step_summary
                subplans[str(main_index)] = sub_entry
                _save_executor_snapshot(
                    executor_db,
                    workspace,
                    main_step=main_index + 1,
                    sub_step=sub_index + 1,
                )
                save_exec_progress(
                    workspace,
                    cfg.executor_progress_json,
                    {
                        "plan_hash": plan_sig,
                        "planning_mode": "hierarchical",
                        "summary": summary,
                        "main_next_index": main_index,
                        "subplans": subplans,
                        "completed_repos": sorted(progress.get("completed_repos") or []),
                        "failed_repos": sorted(progress.get("failed_repos") or []),
                        "token_usage": token_usage,
                    },
                )
                update_tracker_markdown(
                    workspace=workspace,
                    stage="execution",
                    activity=(
                        f"Main step {main_index + 1}/{len(materialized_steps)} "
                        f"sub-step {sub_index + 1}/{len(sub_steps)} update: {step_status}"
                    ),
                    plan_steps=plan_steps,
                    completed_step_indices=completed_main_steps,
                    notes=[
                        f"Main step {main_index + 1}: {main_step.name}",
                        f"Sub-step {sub_index + 1} finished.",
                        (
                            "Step commit(s): "
                            f"{', '.join(committed_repos)}"
                            if committed_repos
                            else "Step commit(s): none"
                        ),
                    ],
                    reconciliation="Hierarchical execution advancing in order.",
                )
                previous_summary = step_summary

            hierarchical_progress["main_next_index"] = main_index + 1
            next_step_index = main_index + 1
            save_exec_progress(
                workspace,
                cfg.executor_progress_json,
                {
                    "plan_hash": plan_sig,
                    "planning_mode": "hierarchical",
                    "summary": summary,
                    "main_next_index": next_step_index,
                    "subplans": subplans,
                    "completed_repos": sorted(progress.get("completed_repos") or []),
                    "failed_repos": sorted(progress.get("failed_repos") or []),
                    "token_usage": token_usage,
                },
            )
    elif plan_steps:
        for step_index in range(next_step_index, len(plan_steps)):
            step = materialized_steps[step_index]
            step_text = step.name
            execution_activity = f"Executing step {step_index + 1}/{len(plan_steps)}: {step_text}"
            update_tracker_markdown(
                workspace=workspace,
                stage="execution",
                activity=execution_activity,
                plan_steps=plan_steps,
                completed_step_indices=set(range(step_index)),
                notes=[f"Starting step {step_index + 1}."],
                reconciliation="Executor working through ordered plan steps.",
            )

            step_prompt = (
                f"{base_executor_prompt}\n\n"
                "Execution control:\n"
                f"- Execute ONLY this step now: {step_index + 1}/{len(plan_steps)}\n"
                f"- Step detail: {step_to_text(step)}\n"
                "- Do not start later steps in this invocation.\n"
                "- Return a concise summary of changes made for this step."
            )

            current_activity = execution_activity
            step_base_tokens = token_usage.copy()
            executor_baseline = extract_agent_tokens(executor)
            latest_executor_feedback = {"text": ""}

            def step_execution_detail_provider(
                activity: str = current_activity,
                live_feedback: dict[str, str] = latest_executor_feedback,
            ) -> str:
                if live_feedback["text"]:
                    return live_feedback["text"]
                model_status = extract_agent_status_message(executor, fallback="")
                if model_status:
                    return model_status
                tracker_detail = read_tracker_activity(workspace)
                return tracker_detail or activity

            def step_token_usage_provider(
                base_tokens: dict[str, int] = step_base_tokens,
                agent_baseline: dict[str, int] = executor_baseline,
            ) -> dict[str, int]:
                return merge_tokens(
                    base_tokens,
                    token_delta(agent_baseline, extract_agent_tokens(executor)),
                )

            with progress_heartbeat(
                stage="execution",
                detail=execution_activity,
                detail_provider=step_execution_detail_provider,
                token_usage_provider=step_token_usage_provider,
                total_repos_provider=lambda: len(cfg.repos),
                start_time=started,
                interval_sec=2.0,
            ) as emit_progress_update:
                def on_executor_feedback(
                    text: str,
                    live_feedback: dict[str, str] = latest_executor_feedback,
                ) -> None:
                    live_feedback["text"] = text
                    emit_progress_update(text, agent_feedback_override=text)

                invocation = invoke_agent(
                    executor,
                    step_prompt,
                    cfg.verbose_io,
                    feedback_callback=on_executor_feedback,
                )

            step_summary = invocation.content.strip()
            step_status = extract_brief_model_message(
                step_summary,
                fallback=f"Completed step {step_index + 1}/{len(plan_steps)}.",
            )
            committed_repos = commit_step_changes(
                cfg,
                step_index=step_index,
                total_steps=len(plan_steps),
                step_text=step_text,
                step_status=step_status,
            )
            summary = (
                f"{summary}\n\n---\n"
                f"Step {step_index + 1}/{len(plan_steps)}\n"
                f"{step_summary}"
            ).strip()
            step_delta = token_delta(executor_baseline, extract_agent_tokens(executor))
            token_usage = merge_tokens(token_usage, step_delta)
            _record_token_event(
                token_events,
                stage="execution",
                label=f"step {step_index + 1}/{len(plan_steps)}: {step_text}",
                prompt=step_prompt,
                delta=step_delta,
                main_step=step_index + 1,
            )
            next_step_index = step_index + 1
            _save_executor_snapshot(executor_db, workspace, main_step=step_index + 1)
            save_exec_progress(
                workspace,
                cfg.executor_progress_json,
                {
                    "plan_hash": plan_sig,
                    "planning_mode": "single",
                    "summary": summary,
                    "next_index": next_step_index,
                    "completed_repos": sorted(progress.get("completed_repos") or []),
                    "failed_repos": sorted(progress.get("failed_repos") or []),
                    "token_usage": token_usage,
                },
            )
            update_tracker_markdown(
                workspace=workspace,
                stage="execution",
                activity=(
                    f"Step {step_index + 1}/{len(plan_steps)} update: "
                    f"{step_status}"
                ),
                plan_steps=plan_steps,
                completed_step_indices=set(range(step_index + 1)),
                notes=[
                    f"Step {step_index + 1} finished.",
                    (
                        "Step commit(s): "
                        f"{', '.join(committed_repos)}"
                        if committed_repos
                        else "Step commit(s): none"
                    ),
                ],
                reconciliation="Step execution advancing in order.",
            )
    else:
        execution_activity = (
            "No structured plan steps detected; applying executor actions "
            f"across repo(s): {', '.join(repo.name for repo in cfg.repos) or '<none>'}."
        )
        update_tracker_markdown(
            workspace=workspace,
            stage="execution",
            activity=execution_activity,
            plan_steps=plan_steps,
            completed_step_indices=set(),
            notes=["Executing fallback full-plan pass."],
            reconciliation="No parseable per-step plan; running single executor pass.",
        )

        def execution_detail_provider() -> str:
            if latest_executor_feedback["text"]:
                return latest_executor_feedback["text"]
            model_status = extract_agent_status_message(executor, fallback="")
            if model_status:
                return model_status
            tracker_detail = read_tracker_activity(workspace)
            return tracker_detail or execution_activity

        executor_baseline = extract_agent_tokens(executor)
        latest_executor_feedback = {"text": ""}
        with progress_heartbeat(
            stage="execution",
            detail=execution_activity,
            detail_provider=execution_detail_provider,
            token_usage_provider=lambda: merge_tokens(
                token_usage,
                token_delta(executor_baseline, extract_agent_tokens(executor)),
            ),
            total_repos_provider=lambda: len(cfg.repos),
            start_time=started,
            interval_sec=2.0,
        ) as emit_progress_update:
            def on_executor_feedback(text: str) -> None:
                latest_executor_feedback["text"] = text
                emit_progress_update(text, agent_feedback_override=text)

            invocation = invoke_agent(
                executor,
                base_executor_prompt,
                cfg.verbose_io,
                feedback_callback=on_executor_feedback,
            )
        summary = invocation.content
        fallback_status = extract_brief_model_message(
            summary,
            fallback="Executor finished fallback pass.",
        )
        update_tracker_markdown(
            workspace=workspace,
            stage="execution",
            activity=f"Executor update: {fallback_status}",
            plan_steps=plan_steps,
            completed_step_indices=set(),
            notes=["Fallback full-plan pass completed."],
            reconciliation="Fallback pass complete.",
        )
        fallback_delta = token_delta(executor_baseline, extract_agent_tokens(executor))
        token_usage = merge_tokens(token_usage, fallback_delta)
        _record_token_event(
            token_events,
            stage="execution",
            label="fallback full-plan pass",
            prompt=base_executor_prompt,
            delta=fallback_delta,
        )
        save_exec_progress(
            workspace,
            cfg.executor_progress_json,
            {
                "plan_hash": plan_sig,
                "planning_mode": cfg.planning_mode,
                "summary": summary,
                "next_index": 0,
                "completed_repos": sorted(progress.get("completed_repos") or []),
                "failed_repos": sorted(progress.get("failed_repos") or []),
                "token_usage": token_usage,
            },
        )

    if planner_conn is not None:
        planner_conn.close()

    ordered_repos = resolve_repo_execution_order(cfg, plan_markdown)
    checks: dict[str, list[dict[str, Any]]] = {}
    completed_repos: set[str] = (
        set(progress.get("completed_repos") or []) if plan_hash_matches else set()
    )
    failed_repos: set[str] = set(progress.get("failed_repos") or []) if plan_hash_matches else set()
    repo_status: dict[str, str] = {}
    repo_retries: dict[str, int] = {}

    repos_with_checks = [repo for repo in ordered_repos if repo.checks]
    remaining_repos = [repo for repo in repos_with_checks if repo.name not in completed_repos]
    for repo in repos_with_checks:
        repo_status[repo.name] = "completed" if repo.name in completed_repos else "pending"
        repo_retries[repo.name] = 0

    if remaining_repos:
        emit_panel(
            (
                f"Running repository checks for {len(remaining_repos)} "
                f"repo(s) with parallelism={cfg.max_parallel_checks}"
            ),
            border_style="magenta",
        )

    update_tracker_markdown(
        workspace=workspace,
        stage="execution",
        activity="Executor pass complete; starting repository validation.",
        plan_steps=plan_steps,
        completed_step_indices=set(range(next_step_index)),
        notes=["Validation started for repository checks."],
        reconciliation=_reconciliation_summary(
            ordered_repos=ordered_repos,
            completed_repos=completed_repos,
            failed_repos=failed_repos,
        ),
    )

    render_run_progress(
        stage="validation",
        detail="Repository checks starting",
        planning_mode=cfg.planning_mode,
        current_main_step=next_step_index if next_step_index else None,
        current_main_total=len(plan_steps),
        token_usage=token_usage,
        completed_repos=len(completed_repos),
        total_repos=len(repos_with_checks),
        failed_repos=len(failed_repos),
    )

    failed_map: dict[str, CheckExecutionResult] = {}
    for attempt in range(cfg.max_check_retries + 1):
        if not remaining_repos:
            break
        for repo in remaining_repos:
            repo_status[repo.name] = "checking"
        render_check_status(repo_status, repo_retries)
        render_run_progress(
            stage="validation",
            detail=f"Check attempt {attempt + 1}/{cfg.max_check_retries + 1}",
            planning_mode=cfg.planning_mode,
            current_main_step=next_step_index if next_step_index else None,
            current_main_total=len(plan_steps),
            token_usage=token_usage,
            completed_repos=len(completed_repos),
            total_repos=len(repos_with_checks),
            failed_repos=len(failed_repos),
            retries=sum(repo_retries.values()),
            elapsed_sec=time.time() - started,
        )

        current_tokens = token_usage.copy()

        def validation_token_usage_provider(
            tokens: dict[str, int] = current_tokens,
        ) -> dict[str, int]:
            return tokens

        def validation_detail_provider() -> str:
            tracker_detail = read_tracker_activity(workspace)
            checking = sorted(name for name, state in repo_status.items() if state == "checking")
            if tracker_detail:
                return tracker_detail
            if checking:
                return f"Validating repo(s): {', '.join(checking)}"
            return "Validation in progress."

        checking_repos = sorted(name for name, state in repo_status.items() if state == "checking")
        validation_activity = (
            f"Running validation attempt {attempt + 1}/{cfg.max_check_retries + 1} for repo(s): "
            f"{', '.join(checking_repos) if checking_repos else '-'}"
        )
        update_tracker_markdown(
            workspace=workspace,
            stage="validation",
            activity=validation_activity,
            plan_steps=plan_steps,
            completed_step_indices=set(range(next_step_index)),
            notes=[f"Validation attempt {attempt + 1} started."],
            reconciliation=_reconciliation_summary(
                ordered_repos=ordered_repos,
                completed_repos=completed_repos,
                failed_repos=failed_repos,
            ),
        )

        with progress_heartbeat(
            stage="validation",
            detail=f"Running checks (attempt {attempt + 1}/{cfg.max_check_retries + 1})",
            planning_mode=cfg.planning_mode,
            current_main_step=next_step_index if next_step_index else None,
            current_main_total=len(plan_steps),
            current_repo=", ".join(checking_repos) if checking_repos else None,
            detail_provider=validation_detail_provider,
            token_usage_provider=validation_token_usage_provider,
            completed_repos_provider=lambda: len(completed_repos),
            total_repos_provider=lambda: len(repos_with_checks),
            failed_repos_provider=lambda: len(failed_repos),
            retries_provider=lambda: sum(repo_retries.values()),
            start_time=started,
        ):
            batch_results = asyncio.run(run_pending_repo_checks(cfg, remaining_repos))
        remaining_repos = []
        failed_map = {}
        for repo_name, result in batch_results.items():
            checks[repo_name] = result.check_results
            if result.checks_passed:
                completed_repos.add(repo_name)
                failed_repos.discard(repo_name)
                repo_status[repo_name] = "passed"
            else:
                failed_repos.add(repo_name)
                repo_status[repo_name] = "failed"
                failed_map[repo_name] = result

        save_exec_progress(
            workspace,
            cfg.executor_progress_json,
            _build_progress_payload(
                cfg=cfg,
                plan_sig=plan_sig,
                summary=summary,
                next_step_index=next_step_index,
                token_usage=token_usage,
                completed_repos=sorted(completed_repos),
                failed_repos=sorted(failed_repos),
                hierarchical_progress=hierarchical_progress,
                extra={"last_check_attempt": attempt},
            ),
        )
        render_check_status(repo_status, repo_retries)
        update_tracker_markdown(
            workspace=workspace,
            stage="validation",
            activity=f"Validation attempt {attempt + 1} completed.",
            plan_steps=plan_steps,
            completed_step_indices=set(range(next_step_index)),
            notes=[
                f"Check attempt {attempt + 1}/{cfg.max_check_retries + 1} finished.",
                f"Completed repos: {', '.join(sorted(completed_repos)) or '-'}",
            ],
            reconciliation=_reconciliation_summary(
                ordered_repos=ordered_repos,
                completed_repos=completed_repos,
                failed_repos=failed_repos,
            ),
        )

        if not failed_map:
            break
        if attempt >= cfg.max_check_retries:
            break

        for repo in repos_with_checks:
            if repo.name not in failed_map:
                continue
            repo_retries[repo.name] = repo_retries.get(repo.name, 0) + 1
            executor_baseline = extract_agent_tokens(executor)
            fix_prompt = build_repo_fix_prompt(
                cfg,
                repo,
                plan_markdown,
                format_repo_check_failures(failed_map[repo.name]),
                attempt=attempt + 1,
                structured_plan=materialized_steps,
            )
            emit_panel(
                (
                    f"Attempting automatic repair for repo '{repo.name}' "
                    f"(attempt {attempt + 1}/{cfg.max_check_retries})"
                ),
                border_style="yellow",
            )
            base_tokens = token_usage.copy()
            latest_repair_feedback = {"text": ""}
            token_usage_provider = build_repair_token_usage_provider(
                base_tokens=base_tokens,
                agent_baseline=executor_baseline,
                executor=executor,
            )
            detail_provider = build_repair_detail_provider(
                workspace=workspace,
                executor=executor,
                repo_name=repo.name,
                attempt_num=attempt + 1,
                max_check_retries=cfg.max_check_retries,
                live_feedback=latest_repair_feedback,
            )

            repair_activity = (
                f"Attempting auto-repair for {repo.name} "
                f"({attempt + 1}/{cfg.max_check_retries})"
            )
            update_tracker_markdown(
                workspace=workspace,
                stage="repair",
                activity=repair_activity,
                plan_steps=plan_steps,
                completed_step_indices=set(range(next_step_index)),
                notes=[f"Repair attempt started for {repo.name}."],
                reconciliation=_reconciliation_summary(
                    ordered_repos=ordered_repos,
                    completed_repos=completed_repos,
                    failed_repos=failed_repos,
                ),
            )

            with progress_heartbeat(
                stage="repair",
                detail=(
                    f"Auto-repair in progress for {repo.name} "
                    f"(attempt {attempt + 1}/{cfg.max_check_retries})"
                ),
                planning_mode=cfg.planning_mode,
                current_main_step=next_step_index if next_step_index else None,
                current_main_total=len(plan_steps),
                current_repo=repo.name,
                detail_provider=detail_provider,
                token_usage_provider=token_usage_provider,
                completed_repos_provider=lambda: len(completed_repos),
                total_repos_provider=lambda: len(repos_with_checks),
                failed_repos_provider=lambda: len(failed_repos),
                retries_provider=lambda: sum(repo_retries.values()),
                start_time=started,
                interval_sec=2.0,
            ) as emit_progress_update:
                def on_repair_feedback(
                    text: str,
                    live_feedback: dict[str, str] = latest_repair_feedback,
                ) -> None:
                    live_feedback["text"] = text
                    emit_progress_update(text, agent_feedback_override=text)

                fix_invocation = invoke_agent(
                    executor,
                    fix_prompt,
                    cfg.verbose_io,
                    feedback_callback=on_repair_feedback,
                )
            fix_summary = fix_invocation.content
            fix_status = extract_brief_model_message(
                fix_summary,
                fallback=f"Repair applied for {repo.name}.",
            )
            summary = (
                f"{summary}\n\n---\n"
                f"Auto-repair for {repo.name} (attempt {attempt + 1}):\n{fix_summary}"
            )
            repair_delta = token_delta(executor_baseline, extract_agent_tokens(executor))
            token_usage = merge_tokens(token_usage, repair_delta)
            _record_token_event(
                token_events,
                stage="repair",
                label=f"{repo.name} attempt {attempt + 1}",
                prompt=fix_prompt,
                delta=repair_delta,
                repo_name=repo.name,
            )
            save_exec_progress(
                workspace,
                cfg.executor_progress_json,
                _build_progress_payload(
                    cfg=cfg,
                    plan_sig=plan_sig,
                    summary=summary,
                    next_step_index=next_step_index,
                    token_usage=token_usage,
                    completed_repos=sorted(completed_repos),
                    failed_repos=sorted(failed_repos),
                    hierarchical_progress=hierarchical_progress,
                    extra={"last_check_attempt": attempt},
                ),
            )
            update_tracker_markdown(
                workspace=workspace,
                stage="repair",
                activity=f"Repair update for {repo.name}: {fix_status}",
                plan_steps=plan_steps,
                completed_step_indices=set(range(next_step_index)),
                notes=[f"Repair summary appended for {repo.name}."],
                reconciliation=_reconciliation_summary(
                    ordered_repos=ordered_repos,
                    completed_repos=completed_repos,
                    failed_repos=failed_repos,
                ),
            )
        remaining_repos = [repo for repo in repos_with_checks if repo.name in failed_map]

    all_checks_passed = len(failed_repos) == 0
    duration_sec = round(time.time() - started, 2)
    save_exec_progress(
        workspace,
        cfg.executor_progress_json,
        _build_progress_payload(
            cfg=cfg,
            plan_sig=plan_sig,
            summary=summary,
            next_step_index=next_step_index,
            token_usage=token_usage,
            completed_repos=sorted(completed_repos),
            failed_repos=sorted(failed_repos),
            hierarchical_progress=hierarchical_progress,
            extra={
                "all_checks_passed": all_checks_passed,
                "duration_sec": duration_sec,
            },
        ),
    )

    render_run_progress(
        stage="complete",
        detail="Execution finished",
        planning_mode=cfg.planning_mode,
        current_main_step=next_step_index if next_step_index else None,
        current_main_total=len(plan_steps),
        token_usage=token_usage,
        completed_repos=len(completed_repos),
        total_repos=len(repos_with_checks),
        failed_repos=len(failed_repos),
        retries=sum(repo_retries.values()),
        elapsed_sec=duration_sec,
    )

    final_stage = "complete" if all_checks_passed else "failed"
    update_tracker_markdown(
        workspace=workspace,
        stage=final_stage,
        activity=(
            "Execution finished and all checks passed."
            if all_checks_passed
            else "Execution finished with unresolved check failures."
        ),
        plan_steps=plan_steps,
        completed_step_indices=set(range(next_step_index)),
        notes=[
            f"Duration: {duration_sec}s",
            (
                "Token totals: "
                f"input={token_usage.get('input_tokens', 0)}, "
                f"output={token_usage.get('output_tokens', 0)}, "
                f"total={token_usage.get('total_tokens', 0)}"
            ),
        ],
        reconciliation=_reconciliation_summary(
            ordered_repos=ordered_repos,
            completed_repos=completed_repos,
            failed_repos=failed_repos,
        ),
    )

    payload = {
        "project": cfg.project,
        "workspace": str(cfg.workspace),
        "planning_mode": cfg.planning_mode,
        "plan_hash": plan_sig,
        "summary": summary,
        "checks": checks,
        "repo_execution_order": [repo.name for repo in ordered_repos],
        "repo_dependencies": cfg.repo_dependencies,
        "completed_repos": sorted(completed_repos),
        "failed_repos": sorted(failed_repos),
        "all_checks_passed": all_checks_passed,
        "token_usage": token_usage,
        "token_events": token_events,
        "token_usage_by_stage": summarize_token_events(token_events),
        "duration_sec": duration_sec,
    }
    executor_conn.close()
    return payload
