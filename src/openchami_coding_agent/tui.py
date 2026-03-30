"""Optional Textual UI dashboard for OpenCHAMI agent runs."""

from __future__ import annotations

import importlib
import json
import os
import queue
import random
import re
import threading
import time
import traceback
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.table import Table

from .constants import AGENT_NAME
from .git_activity import collect_repo_git_activity
from .models import AgentConfig, ProgressSnapshot
from .pipeline import run_pipeline_with_reporter
from .progress_view import build_progress_display, repo_status_label, stage_label
from .reporting import ProgressReporter
from .utils import (
    build_token_cache_summary,
    format_cache_hit_ratio,
    format_compact_count,
    format_elapsed_runtime,
    format_runtime_environment_summary,
    token_delta,
)

PROGRESS_REPEAT_COOLDOWN_SEC = 15.0
BODY_RESIZE_KEY_STEP_PERCENT = 4.0

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


def _cache_summary_text(summary: dict[str, Any]) -> str:
    sent = int(summary.get("input_tokens", 0) or 0)
    cached = int(summary.get("cached_input_tokens", 0) or 0)
    uncached = int(summary.get("uncached_input_tokens", 0) or 0)
    ratio = format_cache_hit_ratio(summary.get("cache_hit_ratio", 0.0))
    if sent <= 0:
        return "Cache effectiveness: no prompt tokens recorded yet."
    return (
        "Cache effectiveness: "
        f"cached {format_compact_count(cached)} of {format_compact_count(sent)} sent "
        f"tokens ({ratio} hit rate, {format_compact_count(uncached)} uncached)."
    )


def _progress_focus_phrase(snapshot: ProgressSnapshot) -> str:
    parts: list[str] = []
    if snapshot.current_main_step is not None and snapshot.current_main_total:
        parts.append(
            f"main {snapshot.current_main_step}/{snapshot.current_main_total}"
        )
    if snapshot.current_sub_step is not None and snapshot.current_sub_total:
        parts.append(f"sub {snapshot.current_sub_step}/{snapshot.current_sub_total}")
    if snapshot.current_repo:
        parts.append(f"repo {snapshot.current_repo}")
    if snapshot.checkpoint_label:
        parts.append(f"checkpoint {snapshot.checkpoint_label}")
    return ", ".join(parts)


def _clip_commentary_text(text: str, *, max_chars: int = 260) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def nudge_body_split(plan_width_percent: float, delta_percent: float) -> float:
    return plan_width_percent + delta_percent


def raw_commentary_log_path(workspace: Path | None, summary_json: str) -> Path | None:
    if workspace is None:
        return None

    summary_path = (workspace / summary_json).resolve()
    stem = summary_path.stem
    if stem.endswith("-summary"):
        stem = stem[: -len("-summary")] + "-raw-commentary"
    elif stem.endswith("_summary"):
        stem = stem[: -len("_summary")] + "_raw_commentary"
    else:
        stem = stem + "_raw_commentary"
    return summary_path.with_name(f"{stem}.log")


def format_commentary_log_entry(text: str, *, timestamp: str | None = None) -> str:
    rendered_text = (text or "").strip() or "<empty commentary>"
    stamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"[{stamp}] {rendered_text}\n"


def _progress_pressure_phrase(snapshot: ProgressSnapshot) -> str:
    total_tokens = int(snapshot.token_usage.get("total_tokens", 0) or 0)
    if snapshot.failed_repos > 0:
        return f"{snapshot.failed_repos} repo failure(s) are currently objecting"
    if snapshot.retries > 0:
        return f"retry count is up to {snapshot.retries}, which is rarely a compliment"
    if total_tokens >= 20_000:
        return "token expenditure is entering the theatrical range"
    if total_tokens >= 8_000:
        return "token usage is climbing in a way finance would dislike"
    if snapshot.total_repos and snapshot.completed_repos:
        return (
            f"repo progress is {snapshot.completed_repos}/{snapshot.total_repos}, "
            "which passes for momentum"
        )
    return "the machinery continues to impersonate order"


def _completion_personality_line(payload: dict[str, Any]) -> str:
    failed = payload.get("failed_repos") or []
    total_tokens = int((payload.get("token_usage") or {}).get("total_tokens", 0) or 0)
    if failed:
        return "Run complete, in the technical sense that nothing further happened voluntarily."
    if total_tokens >= 20_000:
        return "Run complete. We spent an extravagant amount of cognition getting here."
    if total_tokens >= 8_000:
        return "Run complete. The token bill has survived to tell its story."
    return "Run complete. The universe remains largely unimpressed."


def _token_observation(
    *,
    stage: str,
    token_usage: dict[str, int],
    token_delta_usage: dict[str, int],
    payload: dict[str, Any],
) -> str:
    total_tokens = int(token_usage.get("total_tokens", 0) or 0)
    delta_total = int(token_delta_usage.get("total_tokens", 0) or 0)
    rollups = payload.get("token_usage_by_stage") or {}
    execution_total = int(((rollups.get("execution") or {}).get("total_tokens", 0)) or 0)
    repair_total = int(((rollups.get("repair") or {}).get("total_tokens", 0)) or 0)

    if repair_total and repair_total >= execution_total and repair_total > 0:
        return (
            "Observation: repair work is consuming as much thought as delivery, "
            "which is unflattering."
        )
    if delta_total >= 1_500:
        return "Observation: the latest update was expensive enough to leave a mark."
    if total_tokens >= 20_000:
        return "Observation: cumulative token usage is now openly dramatic."
    if stage == "planning":
        return "Observation: planning remains cheaper than improvising damage control later."
    if stage == "validation":
        return "Observation: validation is where optimism is audited."
    return "Observation: the token budget is still intact, if not cheerful."


def build_marvin_commentary_from_progress(
    snapshot: ProgressSnapshot,
    cycle_index: int = 0,
) -> str:
    stage = snapshot.stage.lower()
    detail = (snapshot.detail or "").strip()
    focus = _progress_focus_phrase(snapshot)
    pressure = _progress_pressure_phrase(snapshot)
    variants: dict[str, list[str]] = {
        "planning": [
            "I am distilling ambition into something with numbered edges",
            "I am translating intent into sequenced regret",
            "I am arranging requirements into a structure reality might tolerate",
        ],
        "execution": [
            "I am executing the plan despite obvious cosmic objections",
            "I am applying changes with the enthusiasm of a supervised meteor",
            "I am advancing through the queue because avoidance has poor throughput",
        ],
        "validation": [
            "I am comparing our changes with reality, which remains discouragingly specific",
            "I am checking whether tests agree with our fiction",
            "I am validating outcomes before confidence embarrasses us in public",
        ],
        "repair": [
            "I am repairing the latest collapse with professional irritation",
            "I am patching consequences one avoidable failure at a time",
            "I am in repair mode, where optimism goes to be disproven",
        ],
        "complete": [
            "The run has concluded, improbably",
            "Execution is complete; the universe has not objected yet",
            "This pass is done. Please try not to wake another bug",
        ],
        "failed": [
            "The run has failed, as entropy prefers",
            "We have reached an unscheduled lesson in humility",
            "Failure is confirmed; at least it is now measurable",
        ],
        "default": [
            "Progress continues with predictable melancholy",
            "Activity persists while certainty remains unavailable",
            "Another update arrives from the abyss",
        ],
    }
    stage_variants = variants.get(stage, variants["default"])
    preface = stage_variants[cycle_index % len(stage_variants)]
    clauses = [preface]
    if focus:
        clauses.append(f"Current focus: {focus}")
    if pressure:
        clauses.append(pressure)
    if detail:
        clauses.append(detail)

    return ". ".join(clause.rstrip(" .") for clause in clauses if clause).strip() + "."


def build_operational_context(snapshot: ProgressSnapshot) -> str:
    lines: list[str] = []
    focus = _progress_focus_phrase(snapshot)
    pressure = _progress_pressure_phrase(snapshot)
    primary_detail = (snapshot.base_detail or snapshot.detail or "").strip()

    if focus:
        lines.append(f"Focus: {focus}")
    if pressure:
        lines.append(pressure)
    if primary_detail:
        lines.append(_clip_commentary_text(primary_detail, max_chars=320))
    if snapshot.detail:
        detail = snapshot.detail.strip()
        if detail and detail not in {primary_detail, snapshot.agent_feedback.strip()}:
            lines.append(_clip_commentary_text(detail, max_chars=320))

    return "\n".join(line for line in lines if line)


def build_commentary_tabs(snapshot: ProgressSnapshot, cycle_index: int = 0) -> dict[str, str]:
    raw_commentary = (
        snapshot.agent_feedback or snapshot.detail or snapshot.base_detail or ""
    ).strip()
    if not raw_commentary:
        raw_commentary = "Waiting for direct agent commentary."

    return {
        "raw": raw_commentary,
        "marvin": build_marvin_commentary_from_progress(snapshot, cycle_index),
        "context": build_operational_context(snapshot),
    }


def build_commentary_entry(snapshot: ProgressSnapshot, cycle_index: int = 0) -> str:
    tabs = build_commentary_tabs(snapshot, cycle_index)
    ordered_sections = [tabs["raw"], tabs["marvin"], tabs["context"]]
    return "\n\n".join(section for section in ordered_sections if section.strip())


def load_summary_payload(workspace: Path | None, summary_json: str) -> dict[str, Any]:
    summary_path = (workspace / summary_json).resolve() if workspace else None
    if summary_path is None or not summary_path.exists():
        return {}
    try:
        loaded = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


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


def build_completion_summary_text(workspace_name: str, payload: dict[str, Any]) -> str:
    completed = payload.get("completed_repos") or []
    failed = payload.get("failed_repos") or []
    tokens = payload.get("token_usage") or {}
    duration = payload.get("duration_sec")
    summary = str(payload.get("summary") or "<no summary available>")
    summary_tail = summary[-900:] if len(summary) > 900 else summary
    cache_summary = payload.get("token_cache_summary") or build_token_cache_summary(tokens)

    return "\n".join(
        [
            _completion_personality_line(payload),
            "",
            f"Workspace: {workspace_name}",
            f"Completed repos: {', '.join(completed) if completed else '-'}",
            f"Failed repos: {', '.join(failed) if failed else '-'}",
            f"Tokens {_token_triplet_label(tokens)}: {_format_token_triplet(tokens)}",
            _cache_summary_text(cache_summary),
            f"Duration: {format_elapsed_runtime(duration)}",
            "",
            "Token stage breakdown:",
            *token_stage_report_lines(payload),
            "",
            "Highest-cost invocations:",
            *token_hotspot_lines(payload, limit=5),
            "",
            "Summary tail (the useful part):",
            summary_tail,
            "",
            "Press c to copy summary. Press Enter / Esc / q to close modal.",
        ]
    )


def build_token_report_text(
    *,
    workspace_name: str,
    stage: str,
    planning_mode: str,
    current_step: str,
    current_repo: str,
    checkpoint_label: str,
    repo_progress: str,
    failures: int,
    retries: int,
    token_usage: dict[str, int],
    token_delta_usage: dict[str, int],
    elapsed_sec: float | None,
    payload: dict[str, Any],
) -> str:
    lines = [
        "Token Report",
        "",
        _token_observation(
            stage=stage,
            token_usage=token_usage,
            token_delta_usage=token_delta_usage,
            payload=payload,
        ),
        "",
        f"Workspace: {workspace_name}",
        f"Stage: {stage_label(stage)}    Planning: {planning_mode}",
        f"Focus: {current_step or '-'}",
        f"Repo: {current_repo or '-'}    Checkpoint: {checkpoint_label or '-'}",
        f"Repo progress: {repo_progress}    Failures: {failures}    Retries: {retries}",
        (
            f"Cumulative {_token_triplet_label(token_usage)}: "
            f"{_format_token_triplet(token_usage)}"
        ),
        (
            f"Last delta {_token_triplet_label(token_delta_usage)}: "
            f"{_format_token_triplet(token_delta_usage)}"
        ),
        f"Elapsed: {format_elapsed_runtime(elapsed_sec)}",
        "",
        "Per-stage rollups:",
        *token_stage_report_lines(payload),
        "",
        "Highest-cost invocations:",
        *token_hotspot_lines(payload, limit=5),
    ]
    return "\n".join(lines)


def run_textual_tui(cfg: AgentConfig) -> int:
    try:
        textual_app = importlib.import_module("textual.app")
        textual_containers = importlib.import_module("textual.containers")
        textual_screen = importlib.import_module("textual.screen")
        textual_widgets = importlib.import_module("textual.widgets")
    except Exception as exc:
        raise RuntimeError(
            "Textual is not available. Sync project dependencies with: "
            "uv sync --frozen --extra dev"
        ) from exc

    AppBase = textual_app.App
    Container = textual_containers.Container
    Horizontal = textual_containers.Horizontal
    ModalScreenBase = textual_screen.ModalScreen
    VerticalScroll = textual_containers.VerticalScroll

    DataTable = textual_widgets.DataTable
    Footer = textual_widgets.Footer
    Header = textual_widgets.Header
    Markdown = textual_widgets.Markdown
    RichLog = textual_widgets.RichLog
    Static = textual_widgets.Static
    TabbedContent = textual_widgets.TabbedContent
    TabPane = textual_widgets.TabPane

    def marvin_avatar(mood: str = "neutral") -> str:
        face_map = {
            "neutral": "-_-",
            "blink": "- -",
            "planning": "o_o",
            "executing": "._.",
            "validating": "-.-",
            "repairing": "x_x",
            "frowning": ">_<",
            "complete": "^_^",
            "failed": "T_T",
        }
        eyes = face_map.get(mood, face_map["neutral"])
        return "\n".join(
            [
                "   .-''''-.",
                "  /  .--.  \\",
                f" |  ( {eyes} ) |   Marvin",
                "  \\  '--'  /",
                "   '-.__.-'",
            ]
        )

    def marvin_commentary_from_progress(
        snapshot: ProgressSnapshot,
        cycle_index: int = 0,
    ) -> str:
        return build_marvin_commentary_from_progress(snapshot, cycle_index)

    def normalize_progress_detail(detail: str) -> str:
        normalized = re.sub(r"\s+", " ", (detail or "").strip())
        normalized = normalized.rstrip(".:")
        return normalized.lower()

    class TextualProgressReporter(ProgressReporter):
        def __init__(self, app_ref: Any):
            self.app_ref = app_ref

        def emit_panel(
            self, message: str, border_style: str = "blue", title: str | None = None
        ) -> None:
            del border_style, title
            self.app_ref.event_queue.put(("panel", message))

        def emit_text(self, message: str) -> None:
            self.app_ref.event_queue.put(("text", message))

        def emit_table(self, table: Table) -> None:
            self.app_ref.event_queue.put(("text", str(table)))

        def emit_progress(self, snapshot: ProgressSnapshot) -> None:
            self.app_ref.event_queue.put(("progress", snapshot))

        def emit_check_status(self, status: dict[str, str], retries: dict[str, int]) -> None:
            self.app_ref.event_queue.put(("check_status", (status, retries)))

    class CompletionModal(ModalScreenBase):  # type: ignore[misc, valid-type]
        CSS = """
        CompletionModal {
            align: center middle;
        }
        #completion_box {
            width: 88;
            height: auto;
            border: round $accent;
            background: $surface;
            padding: 1 2;
        }
        """

        def __init__(self, summary_text: str) -> None:
            super().__init__()
            self.summary_text = summary_text
            self._copied = False

        def compose(self) -> Any:
            yield Static(self.summary_text, id="completion_box")

        def on_key(self, event) -> None:
            key = str(getattr(event, "key", "")).lower()
            if key == "c":
                app_ref = getattr(self, "app", None)
                copy_fn = getattr(app_ref, "copy_to_clipboard", None)
                if callable(copy_fn):
                    try:
                        copy_fn(self.summary_text)
                    except Exception:
                        pass
                if not self._copied:
                    self._copied = True
                    box = self.query_one("#completion_box", Static)
                    box.update(
                        self.summary_text
                        + "\n\nSummary copied (attempted) to clipboard via 'c'."
                    )
                return
            if key in {"enter", "escape", "q"}:
                self.dismiss()

    class ExecutionApprovalModal(ModalScreenBase):  # type: ignore[misc, valid-type]
        CSS = """
        ExecutionApprovalModal {
            align: center middle;
        }
        #approval_box {
            width: 88;
            height: auto;
            border: round $warning;
            background: $surface;
            padding: 1 2;
        }
        """

        def __init__(self, prompt_text: str) -> None:
            super().__init__()
            self.prompt_text = prompt_text

        def compose(self) -> Any:
            yield Static(self.prompt_text, id="approval_box")

        def on_key(self, event) -> None:
            key = str(getattr(event, "key", "")).lower()
            app_ref = getattr(self, "app", None)
            if app_ref is None:
                return
            approve = getattr(app_ref, "approve_execution", None)
            deny = getattr(app_ref, "deny_execution", None)
            if key in {"y", "enter"} and callable(approve):
                approve()
                return
            if key in {"n", "escape", "q"} and callable(deny):
                deny()
                return

    class MarvinTUIApp(AppBase):  # type: ignore[misc, valid-type]
        BINDINGS = [
            ("left", "resize_split_left", "Pane: more commentary"),
            ("right", "resize_split_right", "Pane: more plan"),
            ("a", "filter_all", "Timeline: all"),
            ("e", "filter_errors", "Timeline: errors"),
            ("p", "filter_progress", "Timeline: progress"),
            ("k", "filter_checks", "Timeline: checks"),
            ("d", "filter_diagnostic", "Timeline: diagnostics"),
            ("u", "filter_approval", "Timeline: approvals"),
            ("g", "filter_git", "Timeline: git"),
            ("b", "plan_back", "Plan: back"),
            ("f", "cycle_event_filter", "Cycle filters"),
            ("?", "show_shortcuts", "Help"),
            ("q", "quit", "Quit"),
        ]

        CSS = """
        #header_row {
            height: 9;
            border: round $primary;
            margin: 0 0 1 0;
        }
        #avatar {
            width: 28;
            padding: 0 1;
            content-align: left middle;
        }
        #header_stats {
            width: 1fr;
            padding: 0 1;
            content-align: left middle;
        }
        #body_row {
            height: 1fr;
            margin: 0 0 1 0;
            min-height: 14;
        }
        #plan_panel {
            width: 39%;
            min-width: 34;
            border: round $secondary;
            padding: 0 1 0 1;
            overflow-y: auto;
        }
        #plan_markdown {
            width: 1fr;
            height: auto;
            min-height: 100%;
        }
        #body_resize_handle {
            width: 1;
            min-width: 1;
            margin: 0 1;
            color: $accent;
            background: $panel;
            content-align: center middle;
            text-style: bold;
        }
        #body_resize_handle.-dragging {
            background: $accent 20%;
            color: $text;
        }
        #commentary_panel {
            width: 1fr;
            min-width: 54;
            border: round $accent;
            padding: 0;
            overflow: hidden;
        }
        #commentary_tabs {
            height: 1fr;
        }
        #raw_commentary, #marvin_commentary, #context_commentary {
            height: 1fr;
        }
        #footer_tabs {
            height: 19;
            border: round $surface-lighten-1;
        }
        #timeline, #checks, #git_activity, #token_report {
            height: 1fr;
        }
        #report, #raw_log {
            height: 1fr;
        }
        """

        def __init__(self, runner, **kwargs):
            super().__init__(**kwargs)
            self.runner = runner
            self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
            self.exit_code: int = 1
            self.awaiting_execution_approval = False
            self.execution_approval_event = threading.Event()
            self.execution_approval_response: bool | None = None
            self.execution_approval_modal_visible = False

            self.started_at = time.time()
            self.last_progress_at = self.started_at
            self.workspace_name = cfg.workspace.name if cfg.workspace else "-"
            self.goal = re.sub(r"\s+", " ", cfg.problem.strip())
            self.goal = (self.goal[:140] + "…") if len(self.goal) > 140 else self.goal

            self.last_stage = "starting"
            self.current_step = "Booting"
            self.last_outcome: str = "neutral"
            self.blink_until: float = 0.0
            self.next_blink_at: float = self.started_at + random.uniform(2.0, 5.0)
            self.frown_until: float = 0.0
            self.next_frown_at: float = self.started_at + random.uniform(10.0, 22.0)
            self.last_token_usage: dict[str, int] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
            self.last_token_delta: dict[str, int] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
            self.plan_completed = 0
            self.plan_total = 0
            self.last_repo_progress = "0/0"
            self.last_failed_repos = 0
            self.last_retries = 0
            self.last_planning_mode = cfg.planning_mode
            self.last_current_repo = "-"
            self.last_checkpoint_label = "-"
            self.summary_payload: dict[str, Any] = {}

            self.event_history: list[tuple[str, str, str, bool]] = []
            self.event_filter_mode = "all"
            self.event_filter_order = [
                "all",
                "errors",
                "progress",
                "checks",
                "git",
                "diagnostic",
                "approval",
                "done",
                "event",
            ]
            self.git_activity_cache: dict[str, tuple[int, int, int, str, str]] = {}
            self.plan_doc_mtime: dict[Path, float] = {}
            self.last_commentary_key: tuple[str, str, str] | None = None
            self.last_timeline_progress_key: tuple[Any, ...] | None = None
            self.last_commentary_seen_at: dict[tuple[str, str, str], float] = {}
            self.last_timeline_seen_at: dict[tuple[Any, ...], float] = {}
            self.commentary_cycle_index: dict[str, int] = {}
            self.body_resize_dragging = False
            self.body_resize_origin_screen_x = 0
            self.body_resize_origin_percent = 39.0
            self.body_plan_width_percent = 39.0
            self.raw_commentary_log_path = raw_commentary_log_path(
                cfg.workspace,
                cfg.summary_json,
            )

            self.plan_root_path: Path | None = None
            self.plan_current_path: Path | None = None
            self.plan_history: list[Path] = []
            if cfg.workspace is not None:
                self.plan_root_path = (cfg.workspace / "plan" / "marvin.md").resolve()
                self.plan_current_path = self.plan_root_path

        def compose(self) -> Any:
            yield Header(show_clock=True)

            with Horizontal(id="header_row"):
                yield Static(marvin_avatar(), id="avatar")
                yield Static("Starting Marvin dashboard…", id="header_stats")

            with Horizontal(id="body_row"):
                with VerticalScroll(id="plan_panel", can_focus=True):
                    yield Markdown(
                        "# Plan Tracker\n\nWaiting for `plan/marvin.md`…",
                        id="plan_markdown",
                    )
                yield Static("||", id="body_resize_handle")
                with Container(id="commentary_panel"):
                    with TabbedContent(
                        initial="tab_raw_commentary",
                        id="commentary_tabs",
                    ):
                        with TabPane("Raw Commentary", id="tab_raw_commentary"):
                            yield RichLog(id="raw_commentary", wrap=True, highlight=False)
                        with TabPane("Marvin View", id="tab_marvin_commentary"):
                            yield RichLog(id="marvin_commentary", wrap=True, highlight=False)
                        with TabPane("Run Context", id="tab_context_commentary"):
                            yield RichLog(id="context_commentary", wrap=True, highlight=False)

            with TabbedContent(id="footer_tabs"):
                with TabPane("Timeline", id="tab_timeline"):
                    yield DataTable(id="timeline")
                with TabPane("Checks", id="tab_checks"):
                    yield DataTable(id="checks")
                with TabPane("Git", id="tab_git"):
                    yield DataTable(id="git_activity")
                with TabPane("Tokens", id="tab_tokens"):
                    yield Static("Preparing token report…", id="token_report", markup=False)
                with TabPane("Diagnostics", id="tab_diag"):
                    yield RichLog(id="report", wrap=True, highlight=False)
                with TabPane("Raw Log", id="tab_raw"):
                    yield RichLog(id="raw_log", wrap=True, highlight=False)

            yield Footer()

        def on_mount(self) -> None:
            timeline = self.query_one("#timeline", DataTable)
            timeline.add_columns("Time", "Type", "Update")

            checks = self.query_one("#checks", DataTable)
            checks.add_columns("Repo", "Status", "Retries")

            git_activity = self.query_one("#git_activity", DataTable)
            git_activity.add_columns("Repo", "Branch", "Files", "+", "-", "Last Commit")

            plan_panel = self.query_one("#plan_panel", VerticalScroll)
            commentary_panel = self.query_one("#commentary_panel", Container)
            commentary_tabs = self.query_one("#commentary_tabs", TabbedContent)
            raw_commentary = self.query_one("#raw_commentary", RichLog)
            marvin_commentary = self.query_one("#marvin_commentary", RichLog)
            context_commentary = self.query_one("#context_commentary", RichLog)
            report = self.query_one("#report", RichLog)
            plan_markdown = self.query_one("#plan_markdown", Markdown)
            timeline = self.query_one("#timeline", DataTable)
            checks = self.query_one("#checks", DataTable)
            git_activity = self.query_one("#git_activity", DataTable)
            token_report = self.query_one("#token_report", Static)

            plan_panel.border_title = "Plan (plan/marvin.md)"
            commentary_panel.border_title = "Commentary"
            commentary_tabs.active = "tab_raw_commentary"
            timeline.border_title = "Timeline"
            checks.border_title = "Checks"
            git_activity.border_title = "Git Activity"
            token_report.border_title = "Token Report"
            plan_markdown.show_vertical_scrollbar = False

            api_key_present = bool(os.getenv("OPENAI_API_KEY"))
            report.write("Diagnostics")
            report.write("- Agent: " + AGENT_NAME)
            report.write("- Project: " + cfg.project)
            report.write("- Mode: " + cfg.mode)
            report.write("- Planning mode: " + cfg.planning_mode)
            report.write("- Workspace: " + (str(cfg.workspace) if cfg.workspace else "<not set>"))
            report.write("- Planner model: " + (cfg.planner_model or "<not set>"))
            report.write("- Executor model: " + (cfg.executor_model or "<not set>"))
            report.write("- OPENAI_API_KEY: " + ("set" if api_key_present else "missing"))
            report.write(
                "- Raw commentary log: "
                + (
                    str(self.raw_commentary_log_path)
                    if self.raw_commentary_log_path is not None
                    else "<workspace not set>"
                )
            )
            report.write(
                "- Shortcuts: left/right resize panes, a/e/p/k/d/u/g filters, "
                "f cycle filter, ? help, q quit"
            )
            runtime_summary = format_runtime_environment_summary()
            report.write("\nRuntime environment:\n" + runtime_summary)
            raw_commentary.write(runtime_summary)
            self._write_raw_commentary_log(runtime_summary)
            raw_commentary.write("Waiting for direct agent commentary.")
            self._write_raw_commentary_log("Waiting for direct agent commentary.")
            marvin_commentary.write(
                "Commentary online. I will provide the emotional damage when progress arrives."
            )
            context_commentary.write(
                runtime_summary
                + "\n\nRun context will accumulate here once the pipeline starts moving."
            )
            token_report.update(self._token_report_text())
            self._apply_body_split(self.body_plan_width_percent)

            self.set_interval(0.2, self.drain_events)
            self.set_interval(1.0, self.refresh_header_stats)
            self.set_interval(1.0, self.refresh_plan_tracker)
            self.set_interval(2.0, self.refresh_git_activity)
            threading.Thread(target=self.run_pipeline, daemon=True).start()

        def _set_plan_border_title(self) -> None:
            plan_panel = self.query_one("#plan_panel", VerticalScroll)
            workspace = cfg.workspace
            if workspace is None or self.plan_current_path is None:
                plan_panel.border_title = "Plan"
                return
            try:
                rel = self.plan_current_path.resolve().relative_to(workspace.resolve())
                label = str(rel)
            except Exception:
                label = str(self.plan_current_path)
            suffix = "  [b: back]" if self.plan_history else ""
            plan_panel.border_title = f"Plan ({label}){suffix}"

        def _apply_body_split(self, plan_width_percent: float) -> None:
            body_row = self.query_one("#body_row", Horizontal)
            plan_panel = self.query_one("#plan_panel", VerticalScroll)
            commentary_panel = self.query_one("#commentary_panel", Container)
            handle = self.query_one("#body_resize_handle", Static)

            available_width = max(1, body_row.size.width - handle.size.width - 2)
            min_plan_percent = min(70.0, max(18.0, (34 / available_width) * 100))
            max_plan_percent = max(
                min_plan_percent,
                min(82.0, 100.0 - ((54 / available_width) * 100)),
            )
            clamped = max(min_plan_percent, min(plan_width_percent, max_plan_percent))

            self.body_plan_width_percent = clamped
            plan_panel.styles.width = f"{clamped:.2f}%"
            commentary_panel.styles.width = "1fr"

        def on_mouse_down(self, event) -> None:
            if getattr(event, "button", 0) != 1:
                return
            widget = getattr(event, "widget", None)
            if getattr(widget, "id", None) != "body_resize_handle":
                return

            handle = self.query_one("#body_resize_handle", Static)
            self.body_resize_dragging = True
            self.body_resize_origin_screen_x = int(getattr(event, "screen_x", 0))
            self.body_resize_origin_percent = self.body_plan_width_percent
            handle.capture_mouse()
            handle.add_class("-dragging")
            stop = getattr(event, "stop", None)
            if callable(stop):
                stop()

        def on_mouse_move(self, event) -> None:
            if not self.body_resize_dragging:
                return

            body_row = self.query_one("#body_row", Horizontal)
            handle = self.query_one("#body_resize_handle", Static)
            available_width = max(1, body_row.size.width - handle.size.width - 2)
            delta_x = int(getattr(event, "screen_x", 0)) - self.body_resize_origin_screen_x
            updated_percent = self.body_resize_origin_percent + (delta_x / available_width) * 100
            self._apply_body_split(updated_percent)

            stop = getattr(event, "stop", None)
            if callable(stop):
                stop()

        def on_mouse_up(self, event) -> None:
            if not self.body_resize_dragging:
                return

            self.body_resize_dragging = False
            handle = self.query_one("#body_resize_handle", Static)
            handle.release_mouse()
            handle.remove_class("-dragging")
            stop = getattr(event, "stop", None)
            if callable(stop):
                stop()

        def action_resize_split_left(self) -> None:
            self._apply_body_split(
                nudge_body_split(
                    self.body_plan_width_percent,
                    -BODY_RESIZE_KEY_STEP_PERCENT,
                )
            )

        def action_resize_split_right(self) -> None:
            self._apply_body_split(
                nudge_body_split(
                    self.body_plan_width_percent,
                    BODY_RESIZE_KEY_STEP_PERCENT,
                )
            )

        def _write_raw_commentary_log(self, text: str) -> None:
            path = self.raw_commentary_log_path
            if path is None:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(format_commentary_log_entry(text))

        def _update_plan_markdown(self, force: bool = False) -> None:
            markdown = self.query_one("#plan_markdown", Markdown)
            path = self.plan_current_path
            if path is None:
                markdown.update("# Plan Tracker\n\nWorkspace is not set.")
                self._set_plan_border_title()
                return

            if not path.exists():
                markdown.update(f"# Plan Tracker\n\nFile not found: `{path}`")
                self._set_plan_border_title()
                return

            stat = path.stat()
            last_mtime = self.plan_doc_mtime.get(path)
            if not force and last_mtime is not None and stat.st_mtime <= last_mtime:
                self._set_plan_border_title()
                return

            self.plan_doc_mtime[path] = stat.st_mtime
            markdown.update(path.read_text(encoding="utf-8"))
            self._set_plan_border_title()

        def _resolve_plan_link_target(self, href: str) -> Path | None:
            workspace = cfg.workspace
            current = self.plan_current_path
            if workspace is None or current is None:
                return None

            target_ref = href.strip()
            if not target_ref:
                return None
            if target_ref.startswith(("http://", "https://", "mailto:")):
                return None

            target_ref = target_ref.split("#", 1)[0].strip()
            if not target_ref:
                return None

            target_path = Path(target_ref)
            if not target_path.is_absolute():
                target_path = (current.parent / target_path).resolve()

            try:
                target_path.relative_to(workspace.resolve())
            except Exception:
                return None

            if target_path.suffix.lower() != ".md":
                return None
            if not target_path.exists():
                return None
            return target_path

        def action_plan_back(self) -> None:
            if not self.plan_history:
                return
            self.plan_current_path = self.plan_history.pop()
            self._update_plan_markdown(force=True)
            self.add_event("event", "Plan view: back")

        def on_markdown_link_clicked(self, event) -> None:
            href = str(getattr(event, "href", "") or "").strip()
            target_path = self._resolve_plan_link_target(href)
            if target_path is None:
                return

            if self.plan_current_path is not None and self.plan_current_path != target_path:
                self.plan_history.append(self.plan_current_path)
            self.plan_current_path = target_path
            self._update_plan_markdown(force=True)
            self.add_event("event", f"Plan view: opened {target_path.name}")

            stop = getattr(event, "stop", None)
            if callable(stop):
                stop()

        def _event_matches_filter(self, event_type: str, is_error: bool) -> bool:
            mode = self.event_filter_mode
            if mode == "all":
                return True
            if mode == "errors":
                return is_error
            return event_type == mode

        def add_event(self, event_type: str, message: str) -> None:
            timestamp = datetime.now().strftime("%H:%M:%S")
            lowered = message.lower()
            is_error = (
                "failed" in lowered
                or "error" in lowered
                or "traceback" in lowered
                or "cancelled" in lowered
                or event_type == "diagnostic"
            )
            self.event_history.append((timestamp, event_type, message, is_error))
            if len(self.event_history) > 1200:
                self.event_history = self.event_history[-1200:]
            self.refresh_timeline()

        def refresh_timeline(self) -> None:
            timeline = self.query_one("#timeline", DataTable)
            timeline.clear()
            filtered = [
                item for item in self.event_history if self._event_matches_filter(item[1], item[3])
            ]
            for timestamp, event_type, message, _ in filtered[-300:]:
                timeline.add_row(timestamp, event_type, message)
            try:
                timeline.scroll_end(animate=False)
            except Exception:
                pass

        def _set_event_filter(self, mode: str) -> None:
            self.event_filter_mode = mode
            self.refresh_timeline()
            self.add_event("event", f"Timeline filter set to '{mode}'")

        def action_filter_all(self) -> None:
            self._set_event_filter("all")

        def action_filter_errors(self) -> None:
            self._set_event_filter("errors")

        def action_filter_progress(self) -> None:
            self._set_event_filter("progress")

        def action_filter_checks(self) -> None:
            self._set_event_filter("checks")

        def action_filter_diagnostic(self) -> None:
            self._set_event_filter("diagnostic")

        def action_filter_approval(self) -> None:
            self._set_event_filter("approval")

        def action_filter_git(self) -> None:
            self._set_event_filter("git")

        def action_cycle_event_filter(self) -> None:
            index = self.event_filter_order.index(self.event_filter_mode)
            next_index = (index + 1) % len(self.event_filter_order)
            self._set_event_filter(self.event_filter_order[next_index])

        def action_show_shortcuts(self) -> None:
            report = self.query_one("#report", RichLog)
            report.write(
                "\nKeyboard shortcuts:\n"
                "- left/right: resize commentary vs. plan\n"
                "- a: timeline all\n"
                "- e: timeline errors\n"
                "- p: timeline progress\n"
                "- k: timeline checks\n"
                "- d: timeline diagnostics\n"
                "- u: timeline approvals\n"
                "- g: timeline git\n"
                "- b: back in plan markdown view\n"
                "- drag the center divider to resize the plan and commentary panes\n"
                "- f: cycle timeline filters\n"
                "- q: quit"
            )
            try:
                report.scroll_end(animate=False)
            except Exception:
                pass

        def refresh_header_stats(self) -> None:
            avatar = self.query_one("#avatar", Static)
            header_stats = self.query_one("#header_stats", Static)
            now_ts = time.time()
            elapsed = int(now_ts - self.started_at)
            since_update = int(now_ts - self.last_progress_at)
            now = datetime.now().strftime("%H:%M:%S")

            progress_pct = 0
            if self.plan_total > 0:
                progress_pct = int((self.plan_completed / self.plan_total) * 100)
            filled = int(progress_pct / 10)
            bar = "█" * filled + "░" * (10 - filled)

            label = stage_label(self.last_stage)
            mood = "neutral"
            if self.last_outcome in {"complete", "failed"}:
                mood = self.last_outcome
            elif self.last_stage == "planning":
                mood = "planning"
            elif self.last_stage == "execution":
                mood = "executing"
            elif self.last_stage == "validation":
                mood = "validating"
            elif self.last_stage == "repair":
                mood = "repairing"

            if now_ts >= self.next_blink_at:
                self.blink_until = now_ts + 0.18
                self.next_blink_at = now_ts + random.uniform(2.0, 6.0)

            if (
                self.last_outcome not in {"complete", "failed"}
                and now_ts >= self.next_frown_at
            ):
                self.frown_until = now_ts + random.uniform(1.0, 2.2)
                self.next_frown_at = now_ts + random.uniform(12.0, 30.0)

            if self.last_outcome not in {"complete", "failed"} and now_ts < self.frown_until:
                mood = "frowning"
            if now_ts < self.blink_until:
                mood = "blink"

            avatar.update(marvin_avatar(mood))

            header_stats.update(
                "\n".join(
                    [
                        f"Workspace: {self.workspace_name}    Wall clock: {now}",
                        f"Goal: {self.goal or '-'}",
                        f"Stage: {label}    Focus: {self.current_step}",
                        (
                            f"Planning: {self.last_planning_mode}    "
                            f"Repo progress: {self.last_repo_progress}    "
                            f"Failures: {self.last_failed_repos}    Retries: {self.last_retries}"
                        ),
                        (
                            f"Repo: {self.last_current_repo}    "
                            f"Checkpoint: {self.last_checkpoint_label}"
                        ),
                        (
                            f"Tokens {_token_triplet_label(self.last_token_usage)}: "
                            f"{_format_token_triplet(self.last_token_usage)}    "
                            f"Last delta {_token_triplet_label(self.last_token_delta)}: "
                            f"{_format_token_triplet(self.last_token_delta)}    "
                            f"Elapsed: {format_elapsed_runtime(float(elapsed))}    "
                            f"Last update: {format_elapsed_runtime(float(since_update))} ago"
                        ),
                        (
                            f"Plan progress: [{bar}] {progress_pct}% "
                            f"({self.plan_completed}/{self.plan_total or 0})    "
                            f"Timeline filter: {self.event_filter_mode}"
                        ),
                    ]
                )
            )

        def _token_report_text(self) -> str:
            return build_token_report_text(
                workspace_name=self.workspace_name,
                stage=self.last_stage,
                planning_mode=self.last_planning_mode,
                current_step=self.current_step,
                current_repo=self.last_current_repo,
                checkpoint_label=self.last_checkpoint_label,
                repo_progress=self.last_repo_progress,
                failures=self.last_failed_repos,
                retries=self.last_retries,
                token_usage=self.last_token_usage,
                token_delta_usage=self.last_token_delta,
                elapsed_sec=time.time() - self.started_at,
                payload=self.summary_payload,
            )

        def refresh_plan_tracker(self) -> None:
            workspace = cfg.workspace
            if workspace is None:
                self.plan_current_path = None
                self._update_plan_markdown(force=True)
                return

            root_path = (workspace / "plan" / "marvin.md").resolve()
            if self.plan_root_path is None:
                self.plan_root_path = root_path
            if self.plan_current_path is None:
                self.plan_current_path = self.plan_root_path

            if not root_path.exists():
                markdown = self.query_one("#plan_markdown", Markdown)
                markdown.update("# Plan Tracker\n\nWaiting for `plan/marvin.md` to appear…")
                self._set_plan_border_title()
                return

            root_content = root_path.read_text(encoding="utf-8")

            activity_match = re.search(
                r"^- Current activity:\s*(.+)$",
                root_content,
                flags=re.MULTILINE,
            )
            if activity_match:
                self.current_step = activity_match.group(1).strip()

            completed = re.findall(r"^- \[x\]", root_content, flags=re.MULTILINE)
            remaining = re.findall(r"^- \[ \]", root_content, flags=re.MULTILINE)
            self.plan_completed = len(completed)
            self.plan_total = len(completed) + len(remaining)

            self._update_plan_markdown(force=False)

        def refresh_git_activity(self) -> None:
            table = self.query_one("#git_activity", DataTable)
            table.clear()
            for repo in cfg.repos:
                activity = collect_repo_git_activity(repo.name, repo.path)
                table.add_row(
                    activity.repo_name,
                    activity.branch,
                    str(activity.changed_files),
                    str(activity.added_lines),
                    str(activity.deleted_lines),
                    activity.last_commit,
                )

                if not activity.is_git_repo:
                    continue

                key = (
                    activity.changed_files,
                    activity.added_lines,
                    activity.deleted_lines,
                    activity.last_commit,
                    activity.recent_files,
                )
                previous = self.git_activity_cache.get(activity.repo_name)
                if previous != key:
                    self.git_activity_cache[activity.repo_name] = key
                    self.add_event(
                        "git",
                        (
                            f"{activity.repo_name}: {activity.changed_files} changed file(s), "
                            f"+{activity.added_lines}/-{activity.deleted_lines}, "
                            f"last commit: {activity.last_commit}, files: {activity.recent_files}"
                        ),
                    )

        def _completion_summary_text(self) -> str:
            self.summary_payload = load_summary_payload(cfg.workspace, cfg.summary_json)
            return build_completion_summary_text(self.workspace_name, self.summary_payload)

        def run_pipeline(self) -> None:
            reporter = TextualProgressReporter(self)
            try:
                approval_between_plan_and_execute = (
                    cfg.confirm_before_execute
                    and cfg.mode == "plan_and_execute"
                    and cfg.execute_after_plan
                    and not cfg.proposal_only
                )
                approval_before_execute = cfg.confirm_before_execute and cfg.mode == "execute"

                if approval_between_plan_and_execute:
                    plan_cfg = replace(
                        cfg,
                        mode="plan",
                        execute_after_plan=False,
                        confirm_before_execute=False,
                    )
                    code = run_pipeline_with_reporter(plan_cfg, reporter)
                    if code != 0:
                        self.event_queue.put(("done", code))
                        return

                    self.event_queue.put(("approval_needed", "execute_plan"))
                    self.execution_approval_event.wait()
                    approved = bool(self.execution_approval_response)
                    self.event_queue.put(("approval_received", approved))
                    if not approved:
                        self.event_queue.put(("done", 1))
                        return

                    execute_cfg = replace(
                        cfg,
                        mode="execute",
                        confirm_before_execute=False,
                    )
                    code = run_pipeline_with_reporter(execute_cfg, reporter)
                elif approval_before_execute:
                    self.event_queue.put(("approval_needed", "execute_mode"))
                    self.execution_approval_event.wait()
                    approved = bool(self.execution_approval_response)
                    self.event_queue.put(("approval_received", approved))
                    if not approved:
                        self.event_queue.put(("done", 1))
                        return

                    execute_cfg = replace(cfg, confirm_before_execute=False)
                    code = run_pipeline_with_reporter(execute_cfg, reporter)
                else:
                    code = self.runner(reporter)
            except Exception as exc:
                reporter.emit_panel(f"Pipeline failed: {exc}", border_style="red")
                api_status = "set" if os.getenv("OPENAI_API_KEY") else "missing"
                self.event_queue.put(
                    (
                        "report_text",
                        "\n".join(
                            [
                                "",
                                "Failure diagnostics:",
                                f"- Type: {type(exc).__name__}",
                                f"- Message: {str(exc) or '<no message>'}",
                                f"- OPENAI_API_KEY: {api_status}",
                                "",
                                "Traceback:",
                                traceback.format_exc(),
                            ]
                        ),
                    )
                )
                code = 1
            self.event_queue.put(("done", code))

        def on_key(self, event) -> None:
            if not self.awaiting_execution_approval:
                return

            key = str(getattr(event, "key", "")).lower()
            if key in {"y", "enter"}:
                self.approve_execution()
            elif key in {"n", "escape", "q"}:
                self.deny_execution()

        def _dismiss_approval_modal(self) -> None:
            if not self.execution_approval_modal_visible:
                return
            try:
                self.pop_screen()
            except Exception:
                pass
            self.execution_approval_modal_visible = False

        def approve_execution(self) -> None:
            self.execution_approval_response = True
            self.awaiting_execution_approval = False
            self.execution_approval_event.set()
            self._dismiss_approval_modal()

        def deny_execution(self) -> None:
            self.execution_approval_response = False
            self.awaiting_execution_approval = False
            self.execution_approval_event.set()
            self._dismiss_approval_modal()

        def drain_events(self) -> None:
            checks = self.query_one("#checks", DataTable)
            report = self.query_one("#report", RichLog)
            raw_log = self.query_one("#raw_log", RichLog)
            raw_commentary = self.query_one("#raw_commentary", RichLog)
            marvin_commentary = self.query_one("#marvin_commentary", RichLog)
            context_commentary = self.query_one("#context_commentary", RichLog)
            token_report = self.query_one("#token_report", Static)

            while not self.event_queue.empty():
                kind, payload = self.event_queue.get()
                if kind == "panel":
                    message = str(payload)
                    self.last_progress_at = time.time()
                    raw_log.write(message)
                    self.add_event("event", message)
                    try:
                        raw_log.scroll_end(animate=False)
                    except Exception:
                        pass
                elif kind == "text":
                    raw_log.write(str(payload))
                    try:
                        raw_log.scroll_end(animate=False)
                    except Exception:
                        pass
                elif kind == "report_text":
                    report.write(str(payload))
                    self.add_event("diagnostic", "Failure diagnostics captured")
                    try:
                        report.scroll_end(animate=False)
                    except Exception:
                        pass
                elif kind == "progress":
                    snapshot: ProgressSnapshot = payload
                    display = build_progress_display(snapshot)
                    self.last_progress_at = time.time()
                    self.last_stage = snapshot.stage
                    previous_tokens = self.last_token_usage
                    current_tokens = snapshot.token_usage or {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    }
                    self.last_token_delta = token_delta(previous_tokens, current_tokens)
                    self.last_token_usage = current_tokens
                    self.last_planning_mode = snapshot.planning_mode
                    self.last_repo_progress = display.repo_progress
                    self.last_failed_repos = snapshot.failed_repos
                    self.last_retries = snapshot.retries
                    self.last_current_repo = display.current_repo
                    self.last_checkpoint_label = display.checkpoint_label
                    focus_bits = [display.step_progress]
                    if snapshot.current_repo:
                        focus_bits.append(f"repo {snapshot.current_repo}")
                    if snapshot.checkpoint_label:
                        focus_bits.append(snapshot.checkpoint_label)
                    focus_summary = " | ".join(bit for bit in focus_bits if bit and bit != "-")
                    self.current_step = focus_summary or snapshot.detail or self.current_step
                    normalized_detail = normalize_progress_detail(snapshot.detail)
                    normalized_feedback = normalize_progress_detail(snapshot.agent_feedback)
                    progress_key = (
                        snapshot.stage,
                        normalized_detail,
                        normalized_feedback,
                        snapshot.completed_repos,
                        snapshot.total_repos,
                        snapshot.failed_repos,
                        snapshot.retries,
                    )
                    now_ts = time.time()
                    timeline_allowed = (
                        progress_key != self.last_timeline_progress_key
                        or now_ts - self.last_timeline_seen_at.get(progress_key, 0.0)
                        >= PROGRESS_REPEAT_COOLDOWN_SEC
                    )
                    if timeline_allowed:
                        message = f"{stage_label(snapshot.stage)}"
                        if display.step_progress != "-":
                            message += f" [{display.step_progress}]"
                        if snapshot.current_repo:
                            message += f" [{snapshot.current_repo}]"
                        if display.repo_progress != "0/0":
                            message += f" [repos {display.repo_progress}]"
                        if self.last_token_delta.get("total_tokens", 0):
                            delta_sent = format_compact_count(
                                int(self.last_token_delta.get("input_tokens", 0))
                            )
                            delta_received = format_compact_count(
                                int(self.last_token_delta.get("output_tokens", 0))
                            )
                            delta_total = format_compact_count(
                                int(self.last_token_delta.get("total_tokens", 0))
                            )
                            message += (
                                " [tok +"
                                f"{delta_sent}/{delta_received}/{delta_total}]"
                            )
                        message += f" — {snapshot.detail}"
                        self.add_event(
                            "progress",
                            message,
                        )
                        self.last_timeline_seen_at[progress_key] = now_ts
                    self.last_timeline_progress_key = progress_key
                    cycle = self.commentary_cycle_index.get(snapshot.stage, 0)
                    key = (snapshot.stage, normalized_detail, normalized_feedback)
                    commentary_allowed = (
                        key != self.last_commentary_key
                        or now_ts - self.last_commentary_seen_at.get(key, 0.0)
                        >= PROGRESS_REPEAT_COOLDOWN_SEC
                    )
                    if commentary_allowed:
                        commentary_tabs = build_commentary_tabs(snapshot, cycle)
                        raw_commentary.write(commentary_tabs["raw"])
                        self._write_raw_commentary_log(commentary_tabs["raw"])
                        marvin_commentary.write(commentary_tabs["marvin"])
                        if commentary_tabs["context"]:
                            context_commentary.write(commentary_tabs["context"])
                        self.last_commentary_key = key
                        self.last_commentary_seen_at[key] = now_ts
                        self.commentary_cycle_index[snapshot.stage] = cycle + 1
                        for log in (raw_commentary, marvin_commentary, context_commentary):
                            try:
                                log.scroll_end(animate=False)
                            except Exception:
                                pass
                    token_report.update(self._token_report_text())
                elif kind == "check_status":
                    checks.clear()
                    status, retry_map = payload
                    for repo_name in sorted(status):
                        checks.add_row(
                            repo_name,
                            repo_status_label(status[repo_name]),
                            str(retry_map.get(repo_name, 0)),
                        )
                    active = [
                        name for name in sorted(status) if status[name] in {"checking", "failed"}
                    ]
                    if active:
                        self.add_event("checks", "Active repo checks: " + ", ".join(active))
                    token_report.update(self._token_report_text())
                elif kind == "approval_needed":
                    self.awaiting_execution_approval = True
                    self.execution_approval_response = None
                    self.execution_approval_event.clear()
                    approval_mode = str(payload or "execute")
                    prompt_text = (
                        "Execution approval required.\n\n"
                        f"Mode: {approval_mode}\n"
                        "Press Y or Enter to proceed with execution.\n"
                        "Press N, Esc, or Q to cancel execution."
                    )
                    report.write(
                        "\nExecution approval required, apparently. "
                        "Press 'y' to proceed or 'n' to cancel."
                    )
                    if not self.execution_approval_modal_visible:
                        self.push_screen(ExecutionApprovalModal(prompt_text))
                        self.execution_approval_modal_visible = True
                    self.add_event("approval", "Execution approval requested (y/n)")
                elif kind == "approval_received":
                    approved = bool(payload)
                    self._dismiss_approval_modal()
                    if approved:
                        self.add_event("approval", "Execution approved")
                    else:
                        self.add_event("approval", "Execution cancelled")
                elif kind == "done":
                    self.exit_code = int(payload)
                    self.last_stage = "complete" if self.exit_code == 0 else "failed"
                    self.last_outcome = self.last_stage
                    self.summary_payload = load_summary_payload(cfg.workspace, cfg.summary_json)
                    token_report.update(self._token_report_text())
                    if self.summary_payload:
                        report.write("\nToken stage breakdown:")
                        for line in token_stage_report_lines(self.summary_payload):
                            report.write(line)
                        report.write("\nHighest-cost invocations:")
                        for line in token_hotspot_lines(self.summary_payload, limit=5):
                            report.write(line)
                    self.add_event(
                        "done",
                        (
                            "Run completed successfully"
                            if self.exit_code == 0
                            else "Run ended with failures"
                        ),
                    )
                    summary_text = self._completion_summary_text()
                    self.push_screen(CompletionModal(summary_text))

    app = MarvinTUIApp(runner=lambda reporter: run_pipeline_with_reporter(cfg, reporter))
    app.run()
    return int(getattr(app, "exit_code", 1))
