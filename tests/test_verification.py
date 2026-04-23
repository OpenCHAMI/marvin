from __future__ import annotations

from pathlib import Path

from openchami_coding_agent.models import AgentConfig, RepoSpec
from openchami_coding_agent.verification import (
    VerifierBase,
    build_verification_bundle,
    render_verification_markdown,
    run_verifier_mesh,
)


class _PassVerifier(VerifierBase):
    def verify(self, bundle, *, cfg):
        del bundle, cfg
        return self._result(verdict="PASS", findings=["pass"])


class _FailVerifier(VerifierBase):
    def verify(self, bundle, *, cfg):
        del bundle, cfg
        return self._result(verdict="FAIL", findings=["fail"], rerun_recommended=True)


def test_build_verification_bundle_collects_workspace_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "svc"
    repo.mkdir()

    cfg = AgentConfig(
        project="OpenCHAMI",
        problem="test",
        workspace=workspace,
        repos=[RepoSpec(name="svc", path=repo)],
    )

    plan_path = workspace / "proposal.md"
    summary_path = workspace / "artifacts" / "summary.json"
    run_trace_path = workspace / "artifacts" / "trace.json"
    plan_path.write_text("1. step", encoding="utf-8")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("{}", encoding="utf-8")
    run_trace_path.write_text("{}", encoding="utf-8")

    bundle = build_verification_bundle(
        cfg,
        plan_path=plan_path,
        execution_summary_path=summary_path,
        run_trace_path=run_trace_path,
        repo_profile_paths=[],
    )

    assert bundle["workspace_path"] == str(workspace)
    assert bundle["plan_path"] == str(plan_path)
    assert bundle["execution_summary_path"] == str(summary_path)
    assert bundle["run_trace_path"] == str(run_trace_path)


def test_run_verifier_mesh_fails_on_required_tier_1_verifier(tmp_path: Path) -> None:
    cfg = AgentConfig(project="OpenCHAMI", problem="test", workspace=tmp_path)
    report = run_verifier_mesh(
        cfg,
        bundle={
            "workspace_path": str(tmp_path),
            "repo_states": {},
            "plan_path": None,
            "execution_summary_path": None,
            "changed_files": [],
            "diff_path": None,
            "run_trace_path": None,
            "repo_profile_paths": [],
            "artifact_dir": str(tmp_path),
        },
        verifiers=[
            _FailVerifier(name="build_test", required=True, tier=1, run_in_parallel=False),
            _PassVerifier(name="security", required=False, tier=2),
        ],
    )

    assert report["summary_verdict"] == "FAIL"
    assert report["required_failures"] == ["build_test"]
    assert report["stopped_at_tier"] == 1


def test_render_verification_markdown_contains_core_sections() -> None:
    markdown = render_verification_markdown(
        {
            "summary_verdict": "PARTIAL",
            "required_failures": [],
            "results": [
                {
                    "name": "docs_operator_impact",
                    "verdict": "PARTIAL",
                    "required": False,
                    "tier": 2,
                    "scope": "workspace",
                    "evidence": ["README.md"],
                    "findings": ["Docs update recommended."],
                    "artifacts": [],
                    "rerun_recommended": False,
                }
            ],
        }
    )

    assert "Summary verdict: PARTIAL" in markdown
    assert "## Per-Verifier Results" in markdown
    assert "### docs_operator_impact" in markdown
