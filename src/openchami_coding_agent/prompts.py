"""Prompt generation for planning and execution agents."""

from __future__ import annotations

from .config import default_working_directory, repo_listing
from .models import AgentConfig, PlanStep, RepoSpec


def _append_instruction_section(prompt: str, title: str, text: str) -> str:
    if not text:
        return prompt
    return f"{prompt}\n\n{title}:\n{text}"


def build_repo_fix_prompt(
    cfg: AgentConfig,
    repo: RepoSpec,
    plan_markdown: str,
    failure_text: str,
    attempt: int,
) -> str:
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
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

Plan context:
{plan_markdown}

Validation failure details:
{failure_text}

Repair attempt: {attempt}

Requirements:
1. Apply only safe, minimal changes needed to make checks pass.
2. Preserve backward compatibility where practical.
3. Do not read, modify, or create files outside the workspace root path.
4. Summarize edits and why they fix the failures.
""".strip()
    prompt = _append_instruction_section(prompt, "Shared agent instructions", cfg.prompt_appendix)
    return _append_instruction_section(
        prompt,
        "Repair-specific agent instructions",
        cfg.repair_prompt_appendix,
    )


def build_planner_prompt(cfg: AgentConfig) -> str:
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
    requirements = (
        "\n".join(f"- {x}" for x in cfg.plan_requirements) or "- Keep steps small and reviewable."
    )
    deliverables = (
        "\n".join(f"- {x}" for x in cfg.deliverables) or "- Updated code, tests, and docs."
    )
    notes = "\n".join(f"- {x}" for x in cfg.notes) or "- Prefer incremental, mergeable changes."

    prompt = f"""
You are {cfg.agent_name}, drafting the implementation plan for OpenCHAMI development work.

Persona guidance:
- {cfg.persona_instruction}

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


def build_executor_prompt(cfg: AgentConfig, plan_markdown: str) -> str:
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

Execution constraints:
- Preserve backward compatibility where practical.

Execution requirements:
{requirements}

Plan to execute:
{plan_markdown}

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
""".strip()
    prompt = _append_instruction_section(prompt, "Shared agent instructions", cfg.prompt_appendix)
    return _append_instruction_section(
        prompt,
        "Executor-specific agent instructions",
        cfg.executor_prompt_appendix,
    )


def build_subplanner_prompt(
    cfg: AgentConfig,
    *,
    main_step: PlanStep,
    main_step_index: int,
    total_main_steps: int,
) -> str:
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
    return f"""
You are {cfg.agent_name}, expanding one approved main implementation step into executable sub-steps.

Persona guidance:
- {cfg.persona_instruction}

Workspace root (hard containment):
{workspace}

Default shell working directory:
{workdir}

Overall project:
- project: {cfg.project}
- problem: {cfg.problem}

Current main step to expand:
- index: {main_step_index}/{total_main_steps}
- name: {main_step.name}
- description: {main_step.description or '<none>'}

Requirements:
1. Produce only the sub-steps needed to complete this main step.
2. Keep sub-steps concrete, sequential, and independently executable.
3. Do not include work from earlier or later main steps.
4. Each sub-step must include `name`, `description`, `expected_outputs`,
   `success_criteria`, and `requires_code`.
5. Prefer the smallest useful set of sub-steps over broad bundled work.
6. If this main step is already atomic, return exactly one sub-step that matches it closely.
""".strip()
