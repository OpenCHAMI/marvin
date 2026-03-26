"""Plan tracking artifacts for Marvin planning and execution."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import PlanStep, StructuredPlan


def plan_directory(workspace: Path) -> Path:
    directory = workspace / "plan"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def tracker_markdown_path(workspace: Path) -> Path:
    return plan_directory(workspace) / "marvin.md"


def read_tracker_activity(workspace: Path) -> str | None:
    path = tracker_markdown_path(workspace)
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- Current activity:"):
            activity = line.split(":", 1)[1].strip()
            return activity or None
    return None


def extract_plan_steps(plan_markdown: str) -> list[str]:
    json_steps = _extract_plan_steps_from_json(plan_markdown)
    if json_steps:
        return json_steps

    steps: list[str] = []
    seen: set[str] = set()
    patterns = [
        re.compile(r"^\s*\d+[.)]\s+(.+?)\s*$"),
        re.compile(r"^\s*(?:phase|step)\s+\d+\s*[:.)-]?\s+(.+?)\s*$", flags=re.IGNORECASE),
        re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(.+?)\s*$"),
        re.compile(r"^\s*[-*]\s+(.+?)\s*$"),
    ]
    for raw_line in plan_markdown.splitlines():
        line = raw_line.strip()
        if not line or re.match(r"^#{1,6}\s+", line):
            continue
        candidate = ""
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                candidate = match.group(1).strip()
                break
        if not candidate:
            continue
        candidate = re.sub(r"`", "", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip(" .-:")
        lowered = candidate.lower()
        if not candidate or lowered in seen:
            continue
        seen.add(lowered)
        steps.append(candidate)
    return steps


def structured_plan_from_data(data: object, *, source: str = "unknown") -> StructuredPlan:
    if isinstance(data, StructuredPlan):
        return data

    raw_steps: object = data
    if isinstance(data, dict):
        candidate_steps = data.get("steps")
        if isinstance(candidate_steps, list):
            raw_steps = candidate_steps
        source = str(data.get("source") or source)

    steps: list[PlanStep] = []
    for item in raw_steps if isinstance(raw_steps, list) else []:
        step = _coerce_plan_step(item)
        if step is not None:
            steps.append(step)
    return StructuredPlan(steps=steps, source=source)


def structured_plan_from_markdown(
    plan_markdown: str,
    *,
    source: str = "markdown-normalized",
) -> StructuredPlan:
    return StructuredPlan(
        steps=[PlanStep(name=name) for name in extract_plan_steps(plan_markdown)],
        source=source,
    )


def structured_plan_from_agent_response(
    response: object,
    *,
    fallback_markdown: str | None = None,
    source: str = "planner",
) -> StructuredPlan:
    if isinstance(response, dict):
        plan_obj = response.get("plan")
        raw_steps = getattr(plan_obj, "steps", None)
        structured = structured_plan_from_data(raw_steps, source=source)
        if structured.steps:
            return structured

    if fallback_markdown:
        return structured_plan_from_markdown(fallback_markdown)
    return StructuredPlan(source=source)


def _extract_plan_steps_from_json(raw_text: str) -> list[str]:
    candidate_texts: list[str] = []
    stripped = raw_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidate_texts.append(stripped)

    fenced = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw_text, flags=re.IGNORECASE)
    candidate_texts.extend(fenced)

    for candidate in candidate_texts:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        steps_raw = payload.get("steps")
        if not isinstance(steps_raw, list):
            continue
        extracted: list[str] = []
        for item in steps_raw:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    extracted.append(name)
            elif isinstance(item, str):
                value = item.strip()
                if value:
                    extracted.append(value)
        if extracted:
            return extracted
    return []


def plan_step_names(structured_plan: StructuredPlan | list[PlanStep] | None) -> list[str]:
    if structured_plan is None:
        return []
    if isinstance(structured_plan, StructuredPlan):
        steps = structured_plan.steps
    else:
        steps = structured_plan
    return [step.name for step in steps if step.name.strip()]


def _coerce_plan_step(item: object) -> PlanStep | None:
    if isinstance(item, PlanStep):
        return item
    if isinstance(item, str):
        name = item.strip()
        return PlanStep(name=name) if name else None

    if isinstance(item, dict):
        name = str(item.get("name") or item.get("title") or item.get("id") or "").strip()
        if not name:
            return None
        expected_outputs = item.get("expected_outputs") or []
        success_criteria = item.get("success_criteria") or []
        return PlanStep(
            name=name,
            description=str(item.get("description") or "").strip(),
            expected_outputs=[
                str(value).strip() for value in expected_outputs if str(value).strip()
            ],
            success_criteria=[
                str(value).strip() for value in success_criteria if str(value).strip()
            ],
            requires_code=bool(item.get("requires_code", True)),
        )

    name = str(getattr(item, "name", "") or "").strip()
    if not name:
        return None
    expected_outputs = getattr(item, "expected_outputs", None) or []
    success_criteria = getattr(item, "success_criteria", None) or []
    return PlanStep(
        name=name,
        description=str(getattr(item, "description", "") or "").strip(),
        expected_outputs=[str(value).strip() for value in expected_outputs if str(value).strip()],
        success_criteria=[str(value).strip() for value in success_criteria if str(value).strip()],
        requires_code=bool(getattr(item, "requires_code", True)),
    )


def _step_filename(index: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not slug:
        slug = f"step-{index:03d}"
    return f"step-{index:03d}-{slug}.md"


def initialize_plan_artifacts(
    workspace: Path,
    plan_markdown: str,
    *,
    structured_plan: StructuredPlan | list[PlanStep] | None = None,
) -> list[str]:
    plan_dir = plan_directory(workspace)
    steps = plan_step_names(structured_plan) or extract_plan_steps(plan_markdown)
    step_files: list[str] = []
    for index, step in enumerate(steps, start=1):
        filename = _step_filename(index, step)
        step_files.append(filename)
        step_path = plan_dir / filename
        step_path.write_text(
            "\n".join(
                [
                    f"# Step {index}: {step}",
                    "",
                    "## Status",
                    "pending",
                    "",
                    "## Notes",
                    "- Awaiting execution.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    update_tracker_markdown(
        workspace=workspace,
        stage="planning",
        activity="Plan captured and step files created.",
        plan_steps=steps,
        completed_step_indices=set(),
        notes=["Planner output persisted to plan directory."],
        reconciliation="Execution not started.",
    )
    return steps


def update_tracker_markdown(
    *,
    workspace: Path,
    stage: str,
    activity: str,
    plan_steps: list[str],
    completed_step_indices: set[int],
    notes: list[str] | None = None,
    reconciliation: str | None = None,
) -> Path:
    path = tracker_markdown_path(workspace)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017

    completed_lines = [
        f"- [x] {index + 1}. {plan_steps[index]}"
        for index in sorted(completed_step_indices)
        if 0 <= index < len(plan_steps)
    ]
    remaining_lines = [
        f"- [ ] {index + 1}. {step}"
        for index, step in enumerate(plan_steps)
        if index not in completed_step_indices
    ]

    if not plan_steps:
        remaining_lines = ["- [ ] No machine-readable steps extracted from current plan."]

    step_file_lines = []
    for index, step in enumerate(plan_steps, start=1):
        filename = _step_filename(index, step)
        step_file_lines.append(f"- Step {index}: [{filename}](./{filename})")

    note_lines = notes or []
    note_lines = note_lines[-8:]

    body = [
        "# Marvin Execution Tracker",
        "",
        f"- Last updated (UTC): {now}",
        f"- Stage: {stage}",
        f"- Current activity: {activity}",
        "",
        "## Plan Step Files",
        *(step_file_lines or ["- No step files available."]),
        "",
        "## Completed",
        *(completed_lines or ["- None yet."]),
        "",
        "## Remaining",
        *remaining_lines,
        "",
        "## Reconciliation",
        reconciliation or "No reconciliation data yet.",
        "",
        "## Recent Notes",
        *([f"- {line}" for line in note_lines] or ["- No notes recorded yet."]),
        "",
    ]

    path.write_text("\n".join(body), encoding="utf-8")
    return path
