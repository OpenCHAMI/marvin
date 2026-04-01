from pathlib import Path

from openchami_coding_agent.constants import AGENT_NAME, AGENT_PERSONA_INSTRUCTION
from openchami_coding_agent.models import AgentConfig, PlanStep, RepoSpec
from openchami_coding_agent.prompts import (
    build_executor_prompt,
    build_planner_prompt,
    build_repo_fix_prompt,
    build_subplanner_prompt,
)


def test_agent_config_from_raw_uses_custom_agent_fields(tmp_path: Path) -> None:
    cfg = AgentConfig.from_raw(
        {
            "project": "OpenCHAMI",
            "problem": "Test custom prompt config",
            "agent": {
                "name": "Trillian",
                "persona_instruction": "Be precise and brief.",
                "prompt_appendix": "Shared guidance.",
                "planner_prompt_appendix": "Planner guidance.",
                "executor_prompt_appendix": "Executor guidance.",
                "repair_prompt_appendix": "Repair guidance.",
            },
        },
        workspace=tmp_path,
        workspace_reused=False,
        repos=[],
    )

    assert cfg.agent_name == "Trillian"
    assert cfg.persona_instruction == "Be precise and brief."
    assert cfg.prompt_appendix == "Shared guidance."
    assert cfg.planner_prompt_appendix == "Planner guidance."
    assert cfg.executor_prompt_appendix == "Executor guidance."
    assert cfg.repair_prompt_appendix == "Repair guidance."


def test_agent_config_from_raw_falls_back_for_blank_agent_fields(tmp_path: Path) -> None:
    cfg = AgentConfig.from_raw(
        {
            "project": "OpenCHAMI",
            "problem": "Test default prompt config",
            "agent": {
                "name": "   ",
                "persona_instruction": "   ",
                "prompt_appendix": None,
            },
        },
        workspace=tmp_path,
        workspace_reused=False,
        repos=[],
    )

    assert cfg.agent_name == AGENT_NAME
    assert cfg.persona_instruction == AGENT_PERSONA_INSTRUCTION
    assert cfg.prompt_appendix == ""


def test_prompt_builders_include_shared_and_specific_agent_customization(tmp_path: Path) -> None:
    repo = RepoSpec(
        name="svc",
        path=tmp_path / "repos" / "svc",
        description="Service repository",
        brief="Inspect handlers, config loaders, and generated artifacts before editing.",
    )
    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="Test custom prompts",
        workspace=tmp_path,
        repos=[repo],
        agent_name="Ford",
        persona_instruction="Stay calm and pragmatic.",
        prompt_appendix="Shared guidance.",
        planner_prompt_appendix="Planner guidance.",
        executor_prompt_appendix="Executor guidance.",
        repair_prompt_appendix="Repair guidance.",
    )

    planner_prompt = build_planner_prompt(cfg)
    structured_steps = [PlanStep(name=f"Step {index}") for index in range(1, 11)]
    executor_prompt = build_executor_prompt(
        cfg,
        "1. Do the thing",
        structured_plan=structured_steps,
    )
    repair_prompt = build_repo_fix_prompt(
        cfg,
        repo,
        "1. Do the thing",
        "failure\n" + ("x" * 5000),
        1,
        structured_plan=structured_steps,
    )
    subplanner_prompt = build_subplanner_prompt(
        AgentConfig(
            project="OpenCHAMI",
            problem="A very long problem statement. " * 80,
            workspace=tmp_path,
            repos=[repo],
            agent_name="Ford",
            persona_instruction="Stay calm and pragmatic.",
        ),
        main_step=PlanStep(name="Implement token trimming", description="Tighten prompts"),
        main_step_index=1,
        total_main_steps=3,
    )

    assert "You are Ford" in planner_prompt
    assert "Stay calm and pragmatic." in planner_prompt
    assert "Shared agent instructions:\nShared guidance." in planner_prompt
    assert "Planner-specific agent instructions:\nPlanner guidance." in planner_prompt
    assert "brief=Inspect handlers, config loaders" in planner_prompt
    assert "Structured step schema requirements:" in planner_prompt
    assert "expected_outputs" in planner_prompt
    assert "success_criteria" in planner_prompt
    assert "requires_code" in planner_prompt
    assert "comments should remain" in planner_prompt

    assert "You are Ford." in executor_prompt
    assert "Shared agent instructions:\nShared guidance." in executor_prompt
    assert "Executor-specific agent instructions:\nExecutor guidance." in executor_prompt
    assert "Available repositories:" in executor_prompt
    assert "brief=Inspect handlers, config loaders" in executor_prompt
    assert "Plan digest:" in executor_prompt
    assert "+2 additional step(s) omitted from this prompt for brevity." in executor_prompt
    assert "Plan to execute:" not in executor_prompt
    assert "Preserve useful existing comments." in executor_prompt

    assert "You are Ford, repairing" in repair_prompt
    assert "Shared agent instructions:\nShared guidance." in repair_prompt
    assert "Repair-specific agent instructions:\nRepair guidance." in repair_prompt
    assert "brief: Inspect handlers, config loaders" in repair_prompt
    assert "Plan digest:" in repair_prompt
    assert "Validation failure details:" in repair_prompt
    assert "Preserve useful existing comments." in repair_prompt
    assert len(repair_prompt) < 6000

    assert "Overall project:" in subplanner_prompt
    assert "Available repositories:" in subplanner_prompt
    assert "comments must stay accurate and relevant" in subplanner_prompt
    assert len(subplanner_prompt) < 2500