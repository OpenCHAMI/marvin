"""Prompt generation for planning and execution agents."""

from __future__ import annotations

from .config import default_working_directory, repo_listing
from .models import AgentConfig, PlanStep, RepoSpec
from .plan_tracking import extract_plan_steps

_MAX_PLAN_DIGEST_STEPS = 8
_MAX_STEP_DETAIL_CHARS = 160
_MAX_PROBLEM_CHARS = 600
_MAX_FAILURE_CHARS = 4000


def _clip_text(text: str, max_chars: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _clip_tail_text(text: str, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return "...\n" + cleaned[-(max_chars - 4) :]


def _render_step_digest(step: PlanStep | str, index: int) -> str:
    if isinstance(step, PlanStep):
        description = (
            _clip_text(step.description, _MAX_STEP_DETAIL_CHARS)
            if step.description
            else ""
        )
        if description and description.lower() != step.name.strip().lower():
            return f"{index}. {step.name} - {description}"
        return f"{index}. {step.name}"
    return f"{index}. {_clip_text(str(step), _MAX_STEP_DETAIL_CHARS)}"


def _plan_digest(plan_markdown: str, structured_plan: list[PlanStep] | None = None) -> str:
    steps: list[PlanStep | str] = (
        list(structured_plan)
        if structured_plan
        else extract_plan_steps(plan_markdown)
    )
    if not steps:
        return _clip_text(plan_markdown, 1000) or "No structured plan digest available."

    lines = [f"- total steps: {len(steps)}"]
    visible_steps = steps[:_MAX_PLAN_DIGEST_STEPS]
    lines.extend(_render_step_digest(step, index + 1) for index, step in enumerate(visible_steps))
    remaining = len(steps) - len(visible_steps)
    if remaining > 0:
        lines.append(f"- +{remaining} additional step(s) omitted from this prompt for brevity.")
    return "\n".join(lines)


def _append_instruction_section(prompt: str, title: str, text: str) -> str:
    if not text:
        return prompt
    return f"{prompt}\n\n{title}:\n{text}"


def _comment_preservation_instruction() -> str:
    return (
        "Preserve useful existing comments. Change comments only when the related "
        "implementation changes or the comment is inaccurate, and keep all comments "
        "accurate and relevant to the code they describe."
    )


def build_repo_fix_prompt_prefix(
    cfg: AgentConfig,
    repo: RepoSpec,
    plan_markdown: str,
    *,
    structured_plan: list[PlanStep] | None = None,
) -> str:
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
    repo_description = (
        f"- description: {_clip_text(repo.description, 220)}\n"
        if repo.description
        else ""
    )
    repo_brief = f"- brief: {_clip_text(repo.brief, 500)}\n" if repo.brief else ""
    prompt = f"""
You are {cfg.agent_name}, repairing a repository that failed validation during OpenCHAMI execution.

Persona guidance:
- {cfg.persona_instruction}

Workspace root (hard containment):
{workspace}

Default shell working directory:
{workdir}

Repository:
- name: {repo.name}
- path: {repo.path}
{repo_description}{repo_brief}

Plan digest:
{_plan_digest(plan_markdown, structured_plan)}

Requirements:
1. Apply only safe, minimal changes needed to make checks pass.
2. Preserve backward compatibility where practical.
3. Do not read, modify, or create files outside the workspace root path.
4. Summarize edits and why they fix the failures.
5. Use the validation failure details and plan digest only as focused context;
   do not re-plan unrelated work.
6. {_comment_preservation_instruction()}
""".strip()
    prompt = _append_instruction_section(prompt, "Shared agent instructions", cfg.prompt_appendix)
    return _append_instruction_section(
        prompt,
        "Repair-specific agent instructions",
        cfg.repair_prompt_appendix,
    )


def build_repo_fix_control_suffix(failure_text: str, attempt: int) -> str:
    return f"""
Repair control:
- Attempt: {attempt}

Validation failure details:
{_clip_tail_text(failure_text, _MAX_FAILURE_CHARS)}
""".strip()


def build_repo_fix_prompt_from_prefix(
    prompt_prefix: str,
    *,
    failure_text: str,
    attempt: int,
) -> str:
    return f"{prompt_prefix}\n\n{build_repo_fix_control_suffix(failure_text, attempt)}"


def build_repo_fix_prompt(
    cfg: AgentConfig,
    repo: RepoSpec,
    plan_markdown: str,
    failure_text: str,
    attempt: int,
    *,
    structured_plan: list[PlanStep] | None = None,
) -> str:
    prompt_prefix = build_repo_fix_prompt_prefix(
        cfg,
        repo,
        plan_markdown,
        structured_plan=structured_plan,
    )
    return build_repo_fix_prompt_from_prefix(
        prompt_prefix,
        failure_text=failure_text,
        attempt=attempt,
    )


def build_planner_prompt_prefix(cfg: AgentConfig) -> str:
    prompt = f"""
You are {cfg.agent_name}, drafting the implementation plan for OpenCHAMI development work.

Persona guidance:
- {cfg.persona_instruction}

Output requirements:
1. Produce a sequenced implementation plan that can be written to Markdown.
2. Group work into concrete phases with dependencies.
3. For each step, include repo, files likely affected, rationale, code changes,
   tests, and validation commands.
4. Assume the execution phase will follow this plan exactly.
5. Include a final section named 'Execution Order'.
6. Prefer one feature at a time in dependency order.
7. Emit steps in a machine-readable numbered form so they can be tracked in
    `plan/marvin.md` and per-step `plan/step-*.md` files.
8. Each step must be specific enough to execute independently without bundling
   unrelated work.
9. Avoid duplicate, overlapping, or purely administrative steps.
10. When planning code edits, preserve useful existing comments unless the
    implementation changes or the comment is inaccurate; comments should remain
    accurate and relevant.

Structured step schema requirements:
- Each executable step should have a short `name`.
- Include a `description` that explains the concrete work.
- Include `expected_outputs` listing files, artifacts, or user-visible outcomes.
- Include `success_criteria` listing the checks that prove the step is done.
- Include `requires_code` and set it to `false` only for genuinely non-coding steps.
- If the planner runtime supports structured output, populate that schema directly.
- If not, ensure the Markdown still maps cleanly to one actionable step per numbered item.
""".strip()
    prompt = _append_instruction_section(prompt, "Shared agent instructions", cfg.prompt_appendix)
    return _append_instruction_section(
        prompt,
        "Planner-specific agent instructions",
        cfg.planner_prompt_appendix,
    )


def build_planner_control_suffix(cfg: AgentConfig) -> str:
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
    requirements = (
        "\n".join(f"- {x}" for x in cfg.plan_requirements)
        or "- Keep steps small and reviewable."
    )
    deliverables = (
        "\n".join(f"- {x}" for x in cfg.deliverables)
        or "- Updated code, tests, and docs."
    )
    notes = (
        "\n".join(f"- {x}" for x in cfg.notes)
        or "- Prefer incremental, mergeable changes."
    )
    return f"""
Planning context:
Project: {cfg.project}
Problem:
{cfg.problem}

Available repositories:
{repo_listing(cfg.repos)}

Workspace root (hard containment):
{workspace}

Default shell working directory:
{workdir}

Important implementation constraint:
- Keep all planned file operations under the workspace root path above.

Required deliverables:
{deliverables}

Planning requirements:
{requirements}

Additional notes:
{notes}
""".strip()


def build_planner_prompt_from_prefix(prompt_prefix: str, *, cfg: AgentConfig) -> str:
    return f"{prompt_prefix}\n\n{build_planner_control_suffix(cfg)}"


def build_planner_prompt(cfg: AgentConfig) -> str:
    prompt_prefix = build_planner_prompt_prefix(cfg)
    return build_planner_prompt_from_prefix(prompt_prefix, cfg=cfg)


def build_executor_prompt(
    cfg: AgentConfig,
    plan_markdown: str,
    *,
    structured_plan: list[PlanStep] | None = None,
) -> str:
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
    requirements = (
        "\n".join(f"- {x}" for x in cfg.execution_requirements)
        or "- Run focused tests after each meaningful change."
    )
    prompt = f"""
You are {cfg.agent_name}. Execute the approved OpenCHAMI coding task according to
the plan below, with your usual resigned competence.

Persona guidance:
- {cfg.persona_instruction}

Workspace root (hard containment):
{workspace}

Default shell working directory:
{workdir}

Available repositories:
{repo_listing(cfg.repos)}

Execution constraints:
- Preserve backward compatibility where practical.

Execution requirements:
{requirements}

Plan digest:
{_plan_digest(plan_markdown, structured_plan)}

Execution rules:
1. Work in the listed repository paths only.
2. Do not read, modify, or create files outside the workspace root path above.
3. Use relative paths from the workspace whenever possible.
4. Implement features in the plan order.
5. Update tests and docs with each feature where appropriate.
6. Summarize changes made, files touched, tests run, and anything left incomplete.
7. If a step cannot be completed safely, stop and explain why.
8. Keep plan tracking artifacts aligned with reality:
    - `plan/marvin.md` is the source of truth for completed vs remaining work.
    - `plan/step-*.md` files each represent one executable step.
    - Reconcile actual execution progress against the plan after each major change.
9. Treat the current step control block as authoritative;
    do not infer extra work from omitted plan details.
10. {_comment_preservation_instruction()}
""".strip()
    prompt = _append_instruction_section(prompt, "Shared agent instructions", cfg.prompt_appendix)
    return _append_instruction_section(
        prompt,
        "Executor-specific agent instructions",
        cfg.executor_prompt_appendix,
    )


def build_executor_control_suffix(
    *,
    step_detail: str,
    step_index: int,
    total_steps: int,
    previous_summary_brief: str | None = None,
    main_step_detail: str | None = None,
    main_step_index: int | None = None,
    total_main_steps: int | None = None,
    sub_step_index: int | None = None,
    total_sub_steps: int | None = None,
) -> str:
    lines = ["Execution control:"]
    if (
        main_step_detail is not None
        and main_step_index is not None
        and total_main_steps is not None
    ):
        lines.append(f"- Current main step: {main_step_detail}")
        lines.append(f"- Main step position: {main_step_index}/{total_main_steps}")
    lines.append(f"- Execute ONLY this step now: {step_index}/{total_steps}")
    if sub_step_index is not None and total_sub_steps is not None:
        lines.append(f"- Current sub-step position: {sub_step_index}/{total_sub_steps}")
    lines.append(f"- Step detail: {step_detail}")
    if previous_summary_brief:
        lines.append(f"- Previous sub-step summary: {previous_summary_brief}")
    lines.extend(
        [
            "- Do not start later steps in this invocation.",
            "- Return a concise summary of changes made for this step.",
        ]
    )
    if main_step_detail is not None:
        lines[-2] = "- Do not start later main steps or later sub-steps in this invocation."
        lines[-1] = "- Return a concise summary of changes made for this sub-step."
    return "\n".join(lines)


def build_executor_step_prompt(
    base_prompt: str,
    *,
    step_detail: str,
    step_index: int,
    total_steps: int,
    previous_summary_brief: str | None = None,
    main_step_detail: str | None = None,
    main_step_index: int | None = None,
    total_main_steps: int | None = None,
    sub_step_index: int | None = None,
    total_sub_steps: int | None = None,
) -> str:
    control_suffix = build_executor_control_suffix(
        step_detail=step_detail,
        step_index=step_index,
        total_steps=total_steps,
        previous_summary_brief=previous_summary_brief,
        main_step_detail=main_step_detail,
        main_step_index=main_step_index,
        total_main_steps=total_main_steps,
        sub_step_index=sub_step_index,
        total_sub_steps=total_sub_steps,
    )
    return f"{base_prompt}\n\n{control_suffix}"


def build_subplanner_prompt_prefix(cfg: AgentConfig) -> str:
    return f"""
You are {cfg.agent_name}, expanding one approved main implementation step into executable sub-steps.

Persona guidance:
- {cfg.persona_instruction}

Requirements:
1. Produce only the sub-steps needed to complete this main step.
2. Keep sub-steps concrete, sequential, and independently executable.
3. Do not include work from earlier or later main steps.
4. Each sub-step must include `name`, `description`, `expected_outputs`,
   `success_criteria`, and `requires_code`.
5. Prefer the smallest useful set of sub-steps over broad bundled work.
6. If this main step is already atomic, return exactly one sub-step that matches it closely.
7. Assume useful existing comments should be preserved unless the implementation
    changes or the comment is inaccurate; comments must stay accurate and relevant.
""".strip()


def build_subplanner_control_suffix(
    cfg: AgentConfig,
    *,
    main_step: PlanStep,
    main_step_index: int,
    total_main_steps: int,
) -> str:
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
    return f"""
Subplanning context:

Workspace root (hard containment):
{workspace}

Default shell working directory:
{workdir}

Available repositories:
{repo_listing(cfg.repos)}

Overall project:
- project: {cfg.project}
- problem: {_clip_text(cfg.problem, _MAX_PROBLEM_CHARS)}

Current main step to expand:
- index: {main_step_index}/{total_main_steps}
- name: {main_step.name}
- description: {_clip_text(main_step.description or '<none>', _MAX_STEP_DETAIL_CHARS * 2)}
""".strip()


def build_subplanner_prompt_from_prefix(
    prompt_prefix: str,
    *,
    cfg: AgentConfig,
    main_step: PlanStep,
    main_step_index: int,
    total_main_steps: int,
) -> str:
    control_suffix = build_subplanner_control_suffix(
        cfg,
        main_step=main_step,
        main_step_index=main_step_index,
        total_main_steps=total_main_steps,
    )
    return f"{prompt_prefix}\n\n{control_suffix}"


def build_subplanner_prompt(
    cfg: AgentConfig,
    *,
    main_step: PlanStep,
    main_step_index: int,
    total_main_steps: int,
) -> str:
    prompt_prefix = build_subplanner_prompt_prefix(cfg)
    return build_subplanner_prompt_from_prefix(
        prompt_prefix,
        cfg=cfg,
        main_step=main_step,
        main_step_index=main_step_index,
        total_main_steps=total_main_steps,
    )
