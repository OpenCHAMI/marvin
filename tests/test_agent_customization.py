from pathlib import Path

from openchami_coding_agent.constants import AGENT_NAME, AGENT_PERSONA_INSTRUCTION
from openchami_coding_agent.models import AgentConfig, RepoSpec
from openchami_coding_agent.prompts import (
    build_executor_prompt,
    build_planner_prompt,
    build_repo_fix_prompt,
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
    repo = RepoSpec(name="svc", path=tmp_path / "repos" / "svc")
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
    executor_prompt = build_executor_prompt(cfg, "1. Do the thing")
    repair_prompt = build_repo_fix_prompt(cfg, repo, "1. Do the thing", "failure", 1)

    assert "You are Ford" in planner_prompt
    assert "Stay calm and pragmatic." in planner_prompt
    assert "Shared agent instructions:\nShared guidance." in planner_prompt
    assert "Planner-specific agent instructions:\nPlanner guidance." in planner_prompt
    assert "Structured step schema requirements:" in planner_prompt
    assert "expected_outputs" in planner_prompt
    assert "success_criteria" in planner_prompt
    assert "requires_code" in planner_prompt

    assert "You are Ford." in executor_prompt
    assert "Shared agent instructions:\nShared guidance." in executor_prompt
    assert "Executor-specific agent instructions:\nExecutor guidance." in executor_prompt

    assert "You are Ford, repairing" in repair_prompt
    assert "Shared agent instructions:\nShared guidance." in repair_prompt
    assert "Repair-specific agent instructions:\nRepair guidance." in repair_prompt