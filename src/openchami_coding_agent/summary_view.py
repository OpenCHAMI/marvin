"""Shared execution summary, learning, and operator-feedback helpers."""

from __future__ import annotations

import re
from typing import Any

from .progress_view import stage_label
from .utils import (
    build_token_cache_summary,
    format_cache_hit_ratio,
    format_compact_count,
    format_elapsed_runtime,
)

_TOKEN_STAGE_ORDER = ["planning", "subplanning", "execution", "repair"]


def _format_token_triplet(tokens: dict[str, Any]) -> str:
    sent = format_compact_count(int(tokens.get("input_tokens", 0)))
    cached = int(tokens.get("cached_input_tokens", 0) or 0)
    received = format_compact_count(int(tokens.get("output_tokens", 0)))
    total = format_compact_count(int(tokens.get("total_tokens", 0)))
    if cached:
        return f"{sent}/{format_compact_count(cached)}/{received}/{total}"
    return f"{sent}/{received}/{total}"


def _token_triplet_label(tokens: dict[str, Any]) -> str:
    if int(tokens.get("cached_input_tokens", 0) or 0):
        return "sent/cached/received/total"
    return "sent/received/total"


def token_stage_report_lines(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("token_usage_by_stage")
    if not isinstance(raw, dict) or not raw:
        return ["- Stage rollups will appear after the execution summary is written."]

    ordered = [stage for stage in _TOKEN_STAGE_ORDER if stage in raw]
    ordered.extend(stage for stage in sorted(raw) if stage not in ordered)

    lines: list[str] = []
    for stage in ordered:
        values = raw.get(stage) or {}
        if not isinstance(values, dict):
            continue
        cache_summary = build_token_cache_summary(values)
        lines.append(
            "- "
            f"{stage_label(stage)}: calls={int(values.get('count', 0))} | "
            f"prompt~{format_compact_count(int(values.get('prompt_estimated_tokens', 0)))} | "
            f"tokens={_format_token_triplet(values)} | "
            f"cache={format_cache_hit_ratio(cache_summary.get('cache_hit_ratio', 0.0))}"
        )
    return lines or ["- Stage rollups will appear after the execution summary is written."]


def token_hotspot_lines(payload: dict[str, Any], *, limit: int = 5) -> list[str]:
    raw = payload.get("token_events")
    if not isinstance(raw, list) or not raw:
        return ["- Detailed token hotspots will appear after execution completes."]

    events = [event for event in raw if isinstance(event, dict)]
    if not events:
        return ["- Detailed token hotspots will appear after execution completes."]

    ranked = sorted(
        events,
        key=lambda event: (
            int(event.get("total_tokens", 0) or 0),
            int(event.get("prompt_estimated_tokens", 0) or 0),
        ),
        reverse=True,
    )[: max(1, limit)]

    lines: list[str] = []
    for index, event in enumerate(ranked, start=1):
        label = str(event.get("label") or "unnamed invocation")
        stage = stage_label(str(event.get("stage") or "unknown"))
        repo_name = str(event.get("repo") or "").strip()
        repo_suffix = f" | repo={repo_name}" if repo_name else ""
        cache_summary = build_token_cache_summary(event)
        cache_suffix = ""
        if int(cache_summary.get("input_tokens", 0) or 0) > 0:
            cache_suffix = (
                " | cache="
                f"{format_cache_hit_ratio(cache_summary.get('cache_hit_ratio', 0.0))}"
            )
        lines.append(
            f"{index}. {stage}: {label}{repo_suffix} | "
            f"prompt~{format_compact_count(int(event.get('prompt_estimated_tokens', 0)))} | "
            f"total={format_compact_count(int(event.get('total_tokens', 0)))}"
            f"{cache_suffix}"
        )
    return lines


def _run_trace_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    run_trace = payload.get("run_trace")
    if not isinstance(run_trace, dict):
        return []
    raw_events = run_trace.get("events")
    if not isinstance(raw_events, list):
        return []
    return [event for event in raw_events if isinstance(event, dict)]


def _resume_replan_scope_from_failure_classes(failure_classes: list[str]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if "repairs_exhausted" in failure_classes:
        reasons.append("repair attempts exhausted without clearing the failing repos")
    if "insufficient_progress" in failure_classes:
        reasons.append("the run stalled before completing any executable step")
    if reasons:
        return "pending", reasons

    if "validation_failures" in failure_classes:
        reasons.append("validation still failed after the latest executed step")
    if "unresolved_checks" in failure_classes:
        reasons.append("later work should wait until the current failing step is reconciled")
    if reasons:
        return "current", reasons
    return "none", []


def operator_feedback_requested_replan_scope(text: str) -> str:
    for raw_line in (text or "").splitlines():
        match = re.match(r"^\s*refresh_subplans:\s*(.+?)\s*$", raw_line, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip().lower()
        if value in {"no", "false", "0", "off", "none", "keep"}:
            return "none"
        if value in {
            "current",
            "current-step",
            "current_step",
            "current-subplan",
            "current_subplan",
            "subplan",
        }:
            return "current"
        if value in {"yes", "true", "1", "on", "pending", "all", "full"}:
            return "pending"
    return "none"


def build_partial_success_payload(payload: dict[str, Any]) -> dict[str, Any]:
    existing = payload.get("partial_success")
    if isinstance(existing, dict):
        return dict(existing)

    completed_repos = [str(value) for value in (payload.get("completed_repos") or [])]
    failed_repos = [str(value) for value in (payload.get("failed_repos") or [])]
    planning_mode = str(
        payload.get("planning_mode")
        or (payload.get("run_trace") or {}).get("planning_mode")
        or "single"
    )
    events = _run_trace_events(payload)

    completed_steps: list[dict[str, Any]] = []
    validation_failures: list[dict[str, Any]] = []
    repair_attempts: list[dict[str, Any]] = []
    generated_subplans: list[dict[str, Any]] = []

    for event in events:
        event_type = str(event.get("event_type") or "")
        status = str(event.get("status") or "")
        if event_type == "step_completed" and status == "completed":
            completed_steps.append(event)
        elif event_type == "validation_attempt_completed" and status != "completed":
            validation_failures.append(event)
        elif event_type == "repair_attempt_completed":
            repair_attempts.append(event)
        elif event_type == "subplan_generated":
            generated_subplans.append(event)

    unresolved_blockers: list[str] = []
    if failed_repos:
        unresolved_blockers.append(f"Remaining failed repos: {', '.join(failed_repos)}.")
    if validation_failures:
        latest_failure = validation_failures[-1]
        latest_failed_repos = [
            str(value)
            for value in (((latest_failure.get("metadata") or {}).get("failed_repos")) or [])
        ]
        if latest_failed_repos:
            unresolved_blockers.append(
                "Latest validation attempt still failed for: "
                + ", ".join(latest_failed_repos)
                + "."
            )
    if repair_attempts and failed_repos:
        unresolved_blockers.append(
            f"Automatic repair ran {len(repair_attempts)} time(s) without clearing all failures."
        )

    user_feedback_requests: list[str] = []
    if failed_repos:
        user_feedback_requests.append(
            "Clarify whether the remaining failing checks reflect intended behavior or stale expectations."
        )
        user_feedback_requests.append(
            "Provide missing environment, fixture, or service assumptions for the failing repos."
        )
    if not completed_steps:
        user_feedback_requests.append(
            "Tighten the task scope or repository context before the next run."
        )

    next_actions: list[str] = []
    if failed_repos:
        next_actions.append("Investigate unresolved validation failures before advancing later work.")
    if repair_attempts:
        next_actions.append(
            "Resume from the current workspace after clarifying blockers or adjusting config."
        )
    elif failed_repos:
        next_actions.append("Reuse the current workspace rather than restarting from scratch.")

    all_checks_passed = bool(payload.get("all_checks_passed", not failed_repos))
    status = "failed"
    if all_checks_passed and not failed_repos:
        status = "success"
    elif completed_steps or completed_repos or repair_attempts:
        status = "partial_success"

    failure_classes: list[str] = []
    if validation_failures:
        failure_classes.append("validation_failures")
    if failed_repos and completed_steps:
        failure_classes.append("unresolved_checks")
    if repair_attempts and failed_repos:
        failure_classes.append("repairs_exhausted")
    if failed_repos and not completed_steps:
        failure_classes.append("insufficient_progress")

    resume_replan_scope, resume_replan_reasons = ("none", [])
    if planning_mode == "hierarchical" and status != "success":
        resume_replan_scope, resume_replan_reasons = _resume_replan_scope_from_failure_classes(
            failure_classes
        )

    subplan_refresh_recommended = bool(
        planning_mode == "hierarchical" and resume_replan_scope != "none"
    )

    return {
        "status": status,
        "planning_mode": planning_mode,
        "subplan_refresh_recommended": subplan_refresh_recommended,
        "resume_replan_scope": resume_replan_scope,
        "resume_replan_reasons": resume_replan_reasons,
        "failure_classes": failure_classes,
        "completed_repos": completed_repos,
        "failed_repos": failed_repos,
        "completed_step_count": len(completed_steps),
        "completed_steps": completed_steps,
        "generated_subplan_count": len(generated_subplans),
        "validation_failures": validation_failures,
        "repair_attempts": repair_attempts,
        "unresolved_blockers": unresolved_blockers,
        "user_feedback_requests": user_feedback_requests,
        "next_actions": next_actions,
    }


def build_partial_success_learning_lines(
    partial_success: dict[str, Any], *, max_lines: int = 5
) -> list[str]:
    lines: list[str] = []
    status = str(partial_success.get("status") or "failed").replace("_", " ")
    lines.append(f"Run status: {status}.")

    completed_step_count = int(partial_success.get("completed_step_count", 0) or 0)
    if completed_step_count:
        lines.append(f"Completed steps so far: {completed_step_count}.")

    repair_attempts = partial_success.get("repair_attempts") or []
    if repair_attempts:
        lines.append(f"Automatic repairs attempted: {len(repair_attempts)}.")

    resume_replan_scope = str(partial_success.get("resume_replan_scope") or "none")
    if resume_replan_scope == "current":
        lines.append("Resume policy: regenerate only the current main-step subplan.")
    elif resume_replan_scope == "pending":
        lines.append("Resume policy: regenerate the current and later pending subplans.")

    for blocker in partial_success.get("unresolved_blockers") or []:
        lines.append(str(blocker))
    for request in partial_success.get("user_feedback_requests") or []:
        lines.append(f"Operator input requested: {request}")
    return lines[: max(1, max_lines)]


def build_completion_summary_lines(
    workspace_name: str,
    payload: dict[str, Any],
    *,
    personality_line: str,
    include_hotspots: bool = True,
    include_summary_tail: bool = True,
    include_footer: bool = True,
) -> list[str]:
    completed = payload.get("completed_repos") or []
    failed = payload.get("failed_repos") or []
    tokens = payload.get("token_usage") or {}
    duration = payload.get("duration_sec")
    summary = str(payload.get("summary") or "<no summary available>")
    summary_tail = summary[-900:] if len(summary) > 900 else summary
    cache_summary = payload.get("token_cache_summary") or build_token_cache_summary(tokens)
    partial_success = build_partial_success_payload(payload)
    learning_lines = build_partial_success_learning_lines(partial_success, max_lines=6)

    lines = [
        personality_line,
        "",
        f"Workspace: {workspace_name}",
        f"Planning mode: {partial_success.get('planning_mode', payload.get('planning_mode', '-'))}",
        f"Completed repos: {', '.join(completed) if completed else '-'}",
        f"Failed repos: {', '.join(failed) if failed else '-'}",
        f"Tokens {_token_triplet_label(tokens)}: {_format_token_triplet(tokens)}",
        (
            "Cache effectiveness: cached "
            f"{format_compact_count(int(cache_summary.get('cached_input_tokens', 0) or 0))} "
            f"of {format_compact_count(int(cache_summary.get('input_tokens', 0) or 0))} sent tokens "
            f"({format_cache_hit_ratio(cache_summary.get('cache_hit_ratio', 0.0))} hit rate, "
            f"{format_compact_count(int(cache_summary.get('uncached_input_tokens', 0) or 0))} uncached)."
        ),
        f"Duration: {format_elapsed_runtime(duration)}",
    ]

    if learning_lines:
        lines.extend(["", "Run learning artifact:"])
        lines.extend(f"- {line}" for line in learning_lines)

    lines.extend(["", "Token stage breakdown:", *token_stage_report_lines(payload)])
    if include_hotspots:
        lines.extend(["", "Highest-cost invocations:", *token_hotspot_lines(payload, limit=5)])
    if include_summary_tail:
        lines.extend(["", "Summary tail (the useful part):", summary_tail])
    if include_footer:
        lines.extend(["", "Press c to copy summary. Press Enter / Esc / q to close modal."])
    return lines


def build_compact_execution_summary_lines(workspace_name: str, payload: dict[str, Any]) -> list[str]:
    partial_success = build_partial_success_payload(payload)
    learning_lines = build_partial_success_learning_lines(partial_success, max_lines=4)
    lines = build_completion_summary_lines(
        workspace_name,
        payload,
        personality_line="Execution summary (survivable edition):",
        include_hotspots=False,
        include_summary_tail=False,
        include_footer=False,
    )
    if learning_lines:
        return lines[:8] + ["", "Most useful follow-up:", *[f"- {line}" for line in learning_lines[:3]]]
    return lines


def build_operator_feedback_template(
    workspace_name: str,
    partial_success: dict[str, Any],
) -> str:
    learning_lines = build_partial_success_learning_lines(partial_success, max_lines=6)
    refresh_default = "no"
    if partial_success.get("resume_replan_scope") == "current":
        refresh_default = "current"
    elif partial_success.get("subplan_refresh_recommended"):
        refresh_default = "yes"
    status = str(partial_success.get("status") or "failed").replace("_", " ")
    lines = [
        "# Marvin Operator Feedback",
        "",
        f"workspace: {workspace_name}",
        f"refresh_subplans: {refresh_default}",
        "",
        "Update this file before the next repair or resume if you learned something Marvin missed.",
        "Use `refresh_subplans: current` to regenerate only the current hierarchical subplan, or `yes` to refresh all pending subplans.",
        "Delete the placeholder bullets and replace them with concrete guidance.",
        "",
        "## Current Run Context",
        f"- Run status: {status}",
        *[f"- {line}" for line in learning_lines],
        "",
        "## Notes For Next Repair Or Resume",
        "- Replace this line with concrete clarifications, constraints, or environment facts.",
        "",
        "## Tooling Or Environment Notes",
        "- Add service, fixture, credentials, or command expectations here.",
        "",
        "## Config Adjustments To Try",
        "- Note YAML changes or repo ordering adjustments worth testing next.",
        "",
    ]
    return "\n".join(lines)


def extract_operator_feedback_notes(text: str) -> str:
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if re.match(r"^(workspace|refresh_subplans):", stripped, flags=re.IGNORECASE):
            continue
        placeholder_markers = (
            "Update this file before the next repair or resume",
            "Delete the placeholder bullets",
            "Replace this line with concrete clarifications",
            "Add service, fixture, credentials",
            "Note YAML changes or repo ordering adjustments",
        )
        if any(marker in stripped for marker in placeholder_markers):
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def operator_feedback_requests_subplan_refresh(text: str) -> bool:
    return operator_feedback_requested_replan_scope(text) != "none"