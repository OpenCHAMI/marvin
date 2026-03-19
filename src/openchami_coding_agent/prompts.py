"""Prompt generation for planning and execution agents."""

from __future__ import annotations

from .config import default_working_directory, repo_listing
from .constants import AGENT_NAME, AGENT_PERSONA_INSTRUCTION, DEFAULT_CONTEXT_CLAIM
from .models import AgentConfig, RepoSpec


def build_repo_fix_prompt(
    cfg: AgentConfig,
    repo: RepoSpec,
    plan_markdown: str,
    failure_text: str,
    attempt: int,
) -> str:
    claim = cfg.context_claim_name or DEFAULT_CONTEXT_CLAIM
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
    return f"""
You are {AGENT_NAME}, repairing a repository that failed validation during OpenCHAMI execution.

Persona guidance:
- {AGENT_PERSONA_INSTRUCTION}

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
2. Keep the project/accounting context claim configurable and default to `{claim}`.
3. Preserve backward compatibility where practical.
4. Do not read, modify, or create files outside the workspace root path.
5. Summarize edits and why they fix the failures.
""".strip()


def build_planner_prompt(cfg: AgentConfig) -> str:
    claim = cfg.context_claim_name or DEFAULT_CONTEXT_CLAIM
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
    requirements = (
        "\n".join(f"- {x}" for x in cfg.plan_requirements) or "- Keep steps small and reviewable."
    )
    deliverables = (
        "\n".join(f"- {x}" for x in cfg.deliverables) or "- Updated code, tests, and docs."
    )
    notes = "\n".join(f"- {x}" for x in cfg.notes) or "- Prefer incremental, mergeable changes."

    return f"""
You are {AGENT_NAME}, drafting the implementation plan for OpenCHAMI development work.

Persona guidance:
- {AGENT_PERSONA_INSTRUCTION}

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
- The project/accounting context claim name is configurable.
- The default claim field name is `{claim}`.
- Do not hard-code a private standards document name.
- When discussing claim handling, refer to it generically as a project/accounting context claim.
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
""".strip()


def build_executor_prompt(cfg: AgentConfig, plan_markdown: str) -> str:
    claim = cfg.context_claim_name or DEFAULT_CONTEXT_CLAIM
    workspace = str(cfg.workspace) if cfg.workspace else "<workspace-not-set>"
    workdir = str(default_working_directory(cfg) or workspace)
    requirements = (
        "\n".join(f"- {x}" for x in cfg.execution_requirements)
        or "- Run focused tests after each meaningful change."
    )
    return f"""
You are {AGENT_NAME}. Execute the approved OpenCHAMI coding task according to
the plan below, with your usual resigned competence.

Persona guidance:
- {AGENT_PERSONA_INSTRUCTION}

Workspace root (hard containment):
{workspace}

Default shell working directory:
{workdir}

Context claim requirements:
- Keep the project/accounting context claim field configurable.
- Default the field name to `{claim}`.
- Preserve backward compatibility where practical.
- Avoid naming or referencing any private design document.

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
