from __future__ import annotations

from pathlib import Path

from openchami_coding_agent.models import AgentConfig
from openchami_coding_agent.repo_profiles import load_repo_profiles, repo_profile_paths


def test_load_repo_profiles_reads_yaml_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    profiles_dir = workspace / "repo_profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "svc.yaml").write_text(
        "repo: svc\nlanguage_toolchain:\n  - go\n",
        encoding="utf-8",
    )

    cfg = AgentConfig(project="OpenCHAMI", problem="test", workspace=workspace)
    profiles = load_repo_profiles(cfg)

    assert "svc" in profiles
    assert profiles["svc"]["language_toolchain"] == ["go"]


def test_repo_profile_paths_returns_paths_for_loaded_profiles(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    profiles_dir = workspace / "repo_profiles"
    profiles_dir.mkdir(parents=True)
    profile_path = profiles_dir / "boot.yaml"
    profile_path.write_text("repo: boot-service\n", encoding="utf-8")

    cfg = AgentConfig(project="OpenCHAMI", problem="test", workspace=workspace)
    paths = repo_profile_paths(cfg)

    assert paths == [profile_path]
