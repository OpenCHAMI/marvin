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
from .utils import format_compact_count, format_elapsed_runtime

PROGRESS_REPEAT_COOLDOWN_SEC = 15.0


def run_textual_tui(cfg: AgentConfig) -> int:
    try:
        textual_app = importlib.import_module("textual.app")
        textual_containers = importlib.import_module("textual.containers")
        textual_screen = importlib.import_module("textual.screen")
        textual_widgets = importlib.import_module("textual.widgets")
    except Exception as exc:
        raise RuntimeError("Textual is not available. Install with: pip install textual") from exc

    AppBase = textual_app.App
    Horizontal = textual_containers.Horizontal
    ModalScreenBase = textual_screen.ModalScreen

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
        stage = snapshot.stage.lower()
        detail = (snapshot.detail or "").strip()
        variants: dict[str, list[str]] = {
            "planning": [
                "I am distilling ambition into a plan",
                "I am translating intent into sequenced regret",
                "I am arranging requirements into something almost coherent",
            ],
            "execution": [
                "I am executing the plan despite obvious cosmic objections",
                "I am applying changes with the enthusiasm of a dying star",
                "I am progressing through steps because stalling solves nothing",
            ],
            "validation": [
                "I am comparing our changes with reality, which is rarely cooperative",
                "I am checking whether tests agree with our fiction",
                "I am validating outcomes before confidence embarrasses us",
            ],
            "repair": [
                "I am repairing the latest collapse",
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

        if detail:
            return f"{preface}: {detail}"
        return f"{preface}."

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
            height: 8;
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
        #plan_markdown {
            width: 2fr;
            min-width: 50;
            border: round $secondary;
            padding: 0 1;
            margin: 0 1 0 0;
            overflow-y: auto;
        }
        #commentary {
            width: 1fr;
            min-width: 36;
            border: round $accent;
            padding: 0 1;
            overflow-y: auto;
        }
        #footer_tabs {
            height: 19;
            border: round $surface-lighten-1;
        }
        #timeline, #checks, #git_activity {
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
            self.plan_completed = 0
            self.plan_total = 0

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
            self.last_commentary_key: tuple[str, str] | None = None
            self.last_timeline_progress_key: tuple[Any, ...] | None = None
            self.last_commentary_seen_at: dict[tuple[str, str], float] = {}
            self.last_timeline_seen_at: dict[tuple[Any, ...], float] = {}
            self.commentary_cycle_index: dict[str, int] = {}

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
                yield Markdown(
                    "# Plan Tracker\n\nWaiting for `plan/marvin.md`…",
                    id="plan_markdown",
                )
                yield RichLog(id="commentary", wrap=True, highlight=False)

            with TabbedContent(id="footer_tabs"):
                with TabPane("Timeline", id="tab_timeline"):
                    yield DataTable(id="timeline")
                with TabPane("Checks", id="tab_checks"):
                    yield DataTable(id="checks")
                with TabPane("Git", id="tab_git"):
                    yield DataTable(id="git_activity")
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

            report = self.query_one("#report", RichLog)
            commentary = self.query_one("#commentary", RichLog)
            plan_markdown = self.query_one("#plan_markdown", Markdown)
            timeline = self.query_one("#timeline", DataTable)
            checks = self.query_one("#checks", DataTable)
            git_activity = self.query_one("#git_activity", DataTable)

            plan_markdown.border_title = "Plan (plan/marvin.md)"
            commentary.border_title = "Marvin Commentary"
            timeline.border_title = "Timeline"
            checks.border_title = "Checks"
            git_activity.border_title = "Git Activity"

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
                "- Shortcuts: a/e/p/k/d/u/g filters, f cycle filter, ? help, q quit"
            )
            commentary.write(
                "Marvin commentary online. I will provide the emotional damage; "
                "the status panels will provide the facts."
            )

            self.set_interval(0.2, self.drain_events)
            self.set_interval(1.0, self.refresh_header_stats)
            self.set_interval(1.0, self.refresh_plan_tracker)
            self.set_interval(2.0, self.refresh_git_activity)
            threading.Thread(target=self.run_pipeline, daemon=True).start()

        def _set_plan_border_title(self) -> None:
            markdown = self.query_one("#plan_markdown", Markdown)
            workspace = cfg.workspace
            if workspace is None or self.plan_current_path is None:
                markdown.border_title = "Plan"
                return
            try:
                rel = self.plan_current_path.resolve().relative_to(workspace.resolve())
                label = str(rel)
            except Exception:
                label = str(self.plan_current_path)
            suffix = "  [b: back]" if self.plan_history else ""
            markdown.border_title = f"Plan ({label}){suffix}"

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
                "- a: timeline all\n"
                "- e: timeline errors\n"
                "- p: timeline progress\n"
                "- k: timeline checks\n"
                "- d: timeline diagnostics\n"
                "- u: timeline approvals\n"
                "- g: timeline git\n"
                "- b: back in plan markdown view\n"
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
            sent = int(self.last_token_usage.get("input_tokens", 0))
            received = int(self.last_token_usage.get("output_tokens", 0))
            total = int(self.last_token_usage.get("total_tokens", 0))

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
                        f"Stage: {label}    Current step: {self.current_step}",
                        (
                            "Tokens sent/received/total: "
                            f"{format_compact_count(sent)}/"
                            f"{format_compact_count(received)}/"
                            f"{format_compact_count(total)}    "
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
            workspace = cfg.workspace
            summary_path = (workspace / cfg.summary_json).resolve() if workspace else None
            payload: dict[str, Any] = {}
            if summary_path and summary_path.exists():
                try:
                    payload = json.loads(summary_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}

            completed = payload.get("completed_repos") or []
            failed = payload.get("failed_repos") or []
            tokens = payload.get("token_usage") or {}
            duration = payload.get("duration_sec")
            summary = str(payload.get("summary") or "<no summary available>")
            summary_tail = summary[-900:] if len(summary) > 900 else summary

            return "\n".join(
                [
                    "Run complete. The universe remains largely unimpressed.",
                    "",
                    f"Workspace: {self.workspace_name}",
                    f"Completed repos: {', '.join(completed) if completed else '-'}",
                    f"Failed repos: {', '.join(failed) if failed else '-'}",
                    (
                        "Tokens sent/received/total: "
                        f"{format_compact_count(int(tokens.get('input_tokens', 0)))}/"
                        f"{format_compact_count(int(tokens.get('output_tokens', 0)))}/"
                        f"{format_compact_count(int(tokens.get('total_tokens', 0)))}"
                    ),
                    f"Duration: {format_elapsed_runtime(duration)}",
                    "",
                    "Summary tail (the useful part):",
                    summary_tail,
                    "",
                    "Press c to copy summary. Press Enter / Esc / q to close modal.",
                ]
            )

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
            commentary = self.query_one("#commentary", RichLog)

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
                    self.last_token_usage = snapshot.token_usage or {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    }
                    focus_bits = [display.step_progress]
                    if snapshot.current_repo:
                        focus_bits.append(f"repo {snapshot.current_repo}")
                    if snapshot.checkpoint_label:
                        focus_bits.append(snapshot.checkpoint_label)
                    focus_summary = " | ".join(bit for bit in focus_bits if bit and bit != "-")
                    self.current_step = focus_summary or snapshot.detail or self.current_step
                    normalized_detail = normalize_progress_detail(snapshot.detail)
                    progress_key = (
                        snapshot.stage,
                        normalized_detail,
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
                        message += f" — {snapshot.detail}"
                        self.add_event(
                            "progress",
                            message,
                        )
                        self.last_timeline_seen_at[progress_key] = now_ts
                    self.last_timeline_progress_key = progress_key
                    cycle = self.commentary_cycle_index.get(snapshot.stage, 0)
                    commentary_text = marvin_commentary_from_progress(snapshot, cycle)
                    key = (snapshot.stage, normalized_detail)
                    commentary_allowed = (
                        key != self.last_commentary_key
                        or now_ts - self.last_commentary_seen_at.get(key, 0.0)
                        >= PROGRESS_REPEAT_COOLDOWN_SEC
                    )
                    if commentary_allowed:
                        commentary.write(commentary_text)
                        self.last_commentary_key = key
                        self.last_commentary_seen_at[key] = now_ts
                        self.commentary_cycle_index[snapshot.stage] = cycle + 1
                        try:
                            commentary.scroll_end(animate=False)
                        except Exception:
                            pass
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
