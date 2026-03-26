from pathlib import Path

import pytest

from openchami_coding_agent.config import ensure_within_workspace, resolve_repo
from openchami_coding_agent.models import AgentConfig


def test_ensure_within_workspace_rejects_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside"):
        ensure_within_workspace(outside, workspace)


def test_resolve_repo_uses_workspace_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    repo = resolve_repo(workspace, {"name": "foo"})
    assert repo.path == workspace / "repos" / "foo"
    assert repo.source_path is None


def test_resolve_repo_uses_source_path_when_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external_repo = tmp_path / "external-repo"
    external_repo.mkdir()

    repo = resolve_repo(workspace, {"name": "foo", "path": str(external_repo)})
    assert repo.path == workspace / "repos" / "foo"
    assert repo.source_path == external_repo


def test_agent_config_from_raw_reads_planning_mode(tmp_path: Path) -> None:
    cfg = AgentConfig.from_raw(
        {
            "project": "OpenCHAMI",
            "problem": "Improve planner",
            "planning": {"mode": "hierarchical"},
        },
        workspace=tmp_path,
        workspace_reused=False,
        repos=[],
    )

    assert cfg.planning_mode == "hierarchical"
