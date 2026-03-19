"""Execution and validation orchestration for OpenCHAMI coding tasks."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from .checkpoints import checkpoint_dir, list_executor_checkpoints, parse_snapshot_indices
from .models import AgentConfig, CheckExecutionResult, RepoSpec
from .plan_tracking import extract_plan_steps, read_tracker_activity, update_tracker_markdown
from .prompts import build_executor_prompt, build_repo_fix_prompt
from .reporting import emit_panel, progress_heartbeat, render_check_status, render_run_progress
from .utils import (
    extract_agent_tokens,
    invoke_agent,
    load_exec_progress,
    merge_tokens,
    run_command,
    save_exec_progress,
    truncate_tail,
)


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
            return "Executor is active; still waiting for a readable plan step to surface."
        return (
            f"Still trudging through internal checkpoint {latest_step}; "
            "the plan exists, optimism does not."
        )

    step_index = _resolve_plan_step_index(latest_step, len(plan_steps))
    if step_index is None:
        if latest_step is None:
            return f"Applying step 1/{len(plan_steps)}: {plan_steps[0]}"
        return (
            f"Grinding through checkpoint {latest_step}. "
            f"Nearest known plan step: 1/{len(plan_steps)} {plan_steps[0]}"
        )

    return (
        f"Slogging through step {step_index + 1}/{len(plan_steps)}: "
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


def execute_plan(cfg: AgentConfig, plan_markdown: str, executor_llm: Any) -> dict[str, Any]:
    from ursa.agents import ExecutionAgent
    from ursa.util.plan_execute_utils import hash_plan

    workspace = cfg.workspace
    if workspace is None:
        raise RuntimeError("Workspace must be set before execution.")

    started = time.time()
    executor_db = checkpoint_dir(workspace) / "executor_checkpoint.db"
    executor_conn = sqlite3.connect(str(executor_db), check_same_thread=False)
    executor_checkpointer = SqliteSaver(executor_conn)
    thread_id = workspace.name
    executor = ExecutionAgent(
        llm=executor_llm,
        checkpointer=executor_checkpointer,
        enable_metrics=True,
        metrics_dir="ursa_metrics",
        thread_id=thread_id,
        workspace=str(workspace),
    )
    executor.thread_id = thread_id
    plan_sig = hash_plan([plan_markdown])
    plan_steps = extract_plan_steps(plan_markdown)
    progress = load_exec_progress(workspace, cfg.executor_progress_json)
    plan_hash_matches = progress.get("plan_hash") == plan_sig
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
            f"Resuming execution state at plan step index {next_step_index}",
            border_style="blue",
        )

    base_executor_prompt = build_executor_prompt(cfg, plan_markdown)

    if plan_steps:
        for step_index in range(next_step_index, len(plan_steps)):
            step_text = plan_steps[step_index]
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
                f"- Step detail: {step_text}\n"
                "- Do not start later steps in this invocation.\n"
                "- Return a concise summary of changes made for this step."
            )

            current_activity = execution_activity
            step_base_tokens = token_usage.copy()

            def step_execution_detail_provider(activity: str = current_activity) -> str:
                tracker_detail = read_tracker_activity(workspace)
                return tracker_detail or activity

            def step_token_usage_provider(
                base_tokens: dict[str, int] = step_base_tokens,
            ) -> dict[str, int]:
                return merge_tokens(base_tokens, extract_agent_tokens(executor))

            with progress_heartbeat(
                stage="execution",
                detail=execution_activity,
                detail_provider=step_execution_detail_provider,
                token_usage_provider=step_token_usage_provider,
                total_repos_provider=lambda: len(cfg.repos),
                start_time=started,
            ):
                invocation = invoke_agent(executor, step_prompt, cfg.verbose_io)

            step_summary = invocation.content.strip()
            summary = (
                f"{summary}\n\n---\n"
                f"Step {step_index + 1}/{len(plan_steps)}\n"
                f"{step_summary}"
            ).strip()
            token_usage = merge_tokens(token_usage, extract_agent_tokens(executor))
            next_step_index = step_index + 1
            save_exec_progress(
                workspace,
                cfg.executor_progress_json,
                {
                    "plan_hash": plan_sig,
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
                activity=f"Completed step {step_index + 1}/{len(plan_steps)}: {step_text}",
                plan_steps=plan_steps,
                completed_step_indices=set(range(step_index + 1)),
                notes=[f"Step {step_index + 1} finished."],
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
            tracker_detail = read_tracker_activity(workspace)
            return tracker_detail or execution_activity

        with progress_heartbeat(
            stage="execution",
            detail=execution_activity,
            detail_provider=execution_detail_provider,
            token_usage_provider=lambda: merge_tokens(token_usage, extract_agent_tokens(executor)),
            total_repos_provider=lambda: len(cfg.repos),
            start_time=started,
        ):
            invocation = invoke_agent(executor, base_executor_prompt, cfg.verbose_io)
        summary = invocation.content
        token_usage = merge_tokens(token_usage, extract_agent_tokens(executor))
        save_exec_progress(
            workspace,
            cfg.executor_progress_json,
            {
                "plan_hash": plan_sig,
                "summary": summary,
                "next_index": 0,
                "completed_repos": sorted(progress.get("completed_repos") or []),
                "failed_repos": sorted(progress.get("failed_repos") or []),
                "token_usage": token_usage,
            },
        )

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
        completed_step_indices=_completed_step_indices_from_checkpoint(workspace, plan_steps),
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
            completed_step_indices=_completed_step_indices_from_checkpoint(workspace, plan_steps),
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
            {
                "plan_hash": plan_sig,
                "summary": summary,
                "next_index": next_step_index,
                "completed_repos": sorted(completed_repos),
                "failed_repos": sorted(failed_repos),
                "token_usage": token_usage,
                "last_check_attempt": attempt,
            },
        )
        render_check_status(repo_status, repo_retries)
        update_tracker_markdown(
            workspace=workspace,
            stage="validation",
            activity=f"Validation attempt {attempt + 1} completed.",
            plan_steps=plan_steps,
            completed_step_indices=_completed_step_indices_from_checkpoint(workspace, plan_steps),
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
            fix_prompt = build_repo_fix_prompt(
                cfg,
                repo,
                plan_markdown,
                format_repo_check_failures(failed_map[repo.name]),
                attempt=attempt + 1,
            )
            emit_panel(
                (
                    f"Attempting automatic repair for repo '{repo.name}' "
                    f"(attempt {attempt + 1}/{cfg.max_check_retries})"
                ),
                border_style="yellow",
            )
            base_tokens = token_usage.copy()

            def repair_token_usage_provider(
                tokens: dict[str, int] = base_tokens,
            ) -> dict[str, int]:
                return merge_tokens(tokens, extract_agent_tokens(executor))

            def repair_detail_provider(
                repo_name: str = repo.name,
                attempt_num: int = attempt + 1,
            ) -> str:
                tracker_detail = read_tracker_activity(workspace)
                if tracker_detail:
                    return tracker_detail
                return f"Attempting repair for {repo_name} ({attempt_num}/{cfg.max_check_retries})"

            repair_activity = (
                f"Attempting auto-repair for {repo.name} "
                f"({attempt + 1}/{cfg.max_check_retries})"
            )
            update_tracker_markdown(
                workspace=workspace,
                stage="repair",
                activity=repair_activity,
                plan_steps=plan_steps,
                completed_step_indices=_completed_step_indices_from_checkpoint(
                    workspace, plan_steps
                ),
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
                detail_provider=repair_detail_provider,
                token_usage_provider=repair_token_usage_provider,
                completed_repos_provider=lambda: len(completed_repos),
                total_repos_provider=lambda: len(repos_with_checks),
                failed_repos_provider=lambda: len(failed_repos),
                retries_provider=lambda: sum(repo_retries.values()),
                start_time=started,
            ):
                fix_invocation = invoke_agent(executor, fix_prompt, cfg.verbose_io)
            fix_summary = fix_invocation.content
            summary = (
                f"{summary}\n\n---\n"
                f"Auto-repair for {repo.name} (attempt {attempt + 1}):\n{fix_summary}"
            )
            token_usage = merge_tokens(token_usage, extract_agent_tokens(executor))
            save_exec_progress(
                workspace,
                cfg.executor_progress_json,
                {
                    "plan_hash": plan_sig,
                    "summary": summary,
                    "next_index": next_step_index,
                    "completed_repos": sorted(completed_repos),
                    "failed_repos": sorted(failed_repos),
                    "token_usage": token_usage,
                    "last_check_attempt": attempt,
                },
            )
            update_tracker_markdown(
                workspace=workspace,
                stage="repair",
                activity=f"Auto-repair applied for {repo.name} (attempt {attempt + 1}).",
                plan_steps=plan_steps,
                completed_step_indices=_completed_step_indices_from_checkpoint(
                    workspace, plan_steps
                ),
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
        {
            "plan_hash": plan_sig,
            "summary": summary,
            "next_index": next_step_index,
            "completed_repos": sorted(completed_repos),
            "failed_repos": sorted(failed_repos),
            "token_usage": token_usage,
            "all_checks_passed": all_checks_passed,
            "duration_sec": duration_sec,
        },
    )

    render_run_progress(
        stage="complete",
        detail="Execution finished",
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
        completed_step_indices=_completed_step_indices_from_checkpoint(workspace, plan_steps),
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
        "context_claim_name": cfg.context_claim_name,
        "plan_hash": plan_sig,
        "summary": summary,
        "checks": checks,
        "repo_execution_order": [repo.name for repo in ordered_repos],
        "repo_dependencies": cfg.repo_dependencies,
        "completed_repos": sorted(completed_repos),
        "failed_repos": sorted(failed_repos),
        "all_checks_passed": all_checks_passed,
        "token_usage": token_usage,
        "duration_sec": duration_sec,
    }
    executor_conn.close()
    return payload
