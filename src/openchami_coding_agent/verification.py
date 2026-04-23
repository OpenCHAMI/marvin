"""Tiered verifier framework for Marvin's verify phase."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import AgentConfig, VerifierResult, VerificationBundle
from .utils import run_command


class VerifierProtocol(Protocol):
    name: str
    required: bool
    tier: int
    scope: str
    run_in_parallel: bool

    def verify(self, bundle: VerificationBundle, *, cfg: AgentConfig) -> VerifierResult: ...


@dataclass(frozen=True)
class VerifierSpec:
    name: str
    required: bool
    tier: int
    scope: str
    run_in_parallel: bool


@dataclass
class VerifierBase:
    name: str
    required: bool
    tier: int
    scope: str = "workspace"
    run_in_parallel: bool = True

    def _result(
        self,
        *,
        verdict: str,
        evidence: list[str] | None = None,
        findings: list[str] | None = None,
        artifacts: list[str] | None = None,
        rerun_recommended: bool = False,
    ) -> VerifierResult:
        return {
            "name": self.name,
            "verdict": verdict,  # type: ignore[typeddict-item]
            "required": self.required,
            "tier": self.tier,
            "scope": self.scope,
            "evidence": list(evidence or []),
            "findings": list(findings or []),
            "artifacts": list(artifacts or []),
            "rerun_recommended": bool(rerun_recommended),
        }


class BuildTestVerifier(VerifierBase):
    def verify(self, bundle: VerificationBundle, *, cfg: AgentConfig) -> VerifierResult:
        summary_path = bundle.get("execution_summary_path")
        if not summary_path:
            return self._result(
                verdict="FAIL",
                findings=["Execution summary path is missing from verification bundle."],
                rerun_recommended=True,
            )

        try:
            payload = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        except Exception as exc:
            return self._result(
                verdict="FAIL",
                findings=[f"Unable to parse execution summary JSON: {exc}"],
                evidence=[summary_path],
                rerun_recommended=True,
            )

        failed_repos = [str(name) for name in (payload.get("failed_repos") or [])]
        checks = payload.get("checks") or {}
        evidence = [summary_path]
        findings: list[str] = []
        for repo_name in failed_repos:
            check_rows = checks.get(repo_name) or []
            findings.append(f"Repository '{repo_name}' has {len(check_rows)} failing check result(s).")

        if failed_repos:
            return self._result(
                verdict="FAIL",
                findings=findings,
                evidence=evidence,
                rerun_recommended=True,
            )

        return self._result(
            verdict="PASS",
            findings=["All configured repository checks passed."],
            evidence=evidence,
        )


class SecurityVerifier(VerifierBase):
    def verify(self, bundle: VerificationBundle, *, cfg: AgentConfig) -> VerifierResult:
        changed_files = [Path(value) for value in bundle.get("changed_files") or []]
        suspicious: list[str] = []
        for path in changed_files[:80]:
            if not path.exists() or not path.is_file():
                continue
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            lowered = text.lower()
            if "private_key" in lowered or "aws_secret_access_key" in lowered:
                suspicious.append(str(path))

        if suspicious:
            return self._result(
                verdict="FAIL" if self.required else "PARTIAL",
                findings=[
                    "Potential secret-like markers found in changed files.",
                    *[f"Review potential secret exposure in {path}" for path in suspicious],
                ],
                evidence=[str(path) for path in suspicious],
                rerun_recommended=True,
            )

        return self._result(
            verdict="PASS",
            findings=["No obvious secret markers found in changed files."],
        )


class HeuristicVerifier(VerifierBase):
    """Generic conditional verifier placeholder until repo profiles provide commands."""

    hint: str = "No repository profile command configured."

    def verify(self, bundle: VerificationBundle, *, cfg: AgentConfig) -> VerifierResult:
        return self._result(
            verdict="PARTIAL",
            findings=[self.hint],
        )


class APIContractVerifier(HeuristicVerifier):
    hint = "No API/contract verifier command configured in repo profiles."


class IntegrationVerifier(HeuristicVerifier):
    hint = "No integration verifier command configured in repo profiles."


class IdempotenceVerifier(HeuristicVerifier):
    hint = "No idempotence/resume verifier command configured in repo profiles."


class StaticAnalysisVerifier(HeuristicVerifier):
    hint = "No static-analysis verifier command configured in repo profiles."


class PackagingVerifier(HeuristicVerifier):
    hint = "No packaging/deployment verifier command configured in repo profiles."


class DocsImpactVerifier(VerifierBase):
    def verify(self, bundle: VerificationBundle, *, cfg: AgentConfig) -> VerifierResult:
        changed_files = [str(value) for value in bundle.get("changed_files") or []]
        code_changes = [
            path
            for path in changed_files
            if "/src/" in path or path.endswith(".py") or path.endswith(".go")
        ]
        docs_changes = [
            path for path in changed_files if "/docs/" in path or Path(path).suffix in {".md", ".rst"}
        ]
        if code_changes and not docs_changes:
            return self._result(
                verdict="PARTIAL",
                findings=[
                    "Code changed without docs/operator artifacts updated.",
                    "If behavior changed, include docs or operator guidance updates.",
                ],
                evidence=code_changes[:10],
            )
        return self._result(
            verdict="PASS",
            findings=["Docs/operator impact looks covered for changed scope."],
        )


def default_verifiers() -> list[VerifierProtocol]:
    return [
        BuildTestVerifier(name="build_test", required=True, tier=1, scope="repos", run_in_parallel=False),
        StaticAnalysisVerifier(name="static_analysis", required=False, tier=1, scope="repos", run_in_parallel=True),
        APIContractVerifier(name="api_contract", required=False, tier=1, scope="repos", run_in_parallel=True),
        IntegrationVerifier(name="integration", required=False, tier=2, scope="repos", run_in_parallel=True),
        SecurityVerifier(name="security", required=False, tier=2, scope="workspace", run_in_parallel=True),
        PackagingVerifier(name="packaging", required=False, tier=2, scope="repos", run_in_parallel=True),
        DocsImpactVerifier(name="docs_operator_impact", required=False, tier=2, scope="workspace", run_in_parallel=True),
        IdempotenceVerifier(name="idempotence_resume", required=False, tier=3, scope="workspace", run_in_parallel=True),
    ]


def _repo_state_summary(cfg: AgentConfig) -> dict[str, str]:
    states: dict[str, str] = {}
    for repo in cfg.repos:
        code, branch_out, _ = run_command(["git", "-C", str(repo.path), "rev-parse", "--abbrev-ref", "HEAD"])
        if code != 0:
            states[repo.name] = "not-a-git-repo"
            continue
        branch = branch_out.strip() or "unknown"
        code, sha_out, _ = run_command(["git", "-C", str(repo.path), "rev-parse", "HEAD"])
        sha = sha_out.strip()[:12] if code == 0 else "unknown"
        states[repo.name] = f"{branch}@{sha}"
    return states


def _changed_files(cfg: AgentConfig) -> list[str]:
    changed: list[str] = []
    for repo in cfg.repos:
        code, out, _ = run_command(["git", "-C", str(repo.path), "status", "--porcelain"])
        if code != 0:
            continue
        for line in out.splitlines():
            entry = line[3:].strip()
            if not entry:
                continue
            changed.append(str((repo.path / entry).resolve()))
    return sorted(set(changed))


def build_verification_bundle(
    cfg: AgentConfig,
    *,
    plan_path: Path | None,
    execution_summary_path: Path | None,
    run_trace_path: Path | None,
    repo_profile_paths: list[Path] | None = None,
) -> VerificationBundle:
    workspace = cfg.workspace
    if workspace is None:
        raise RuntimeError("Workspace must be set before building verification bundle.")

    bundle: VerificationBundle = {
        "workspace_path": str(workspace),
        "repo_states": _repo_state_summary(cfg),
        "plan_path": str(plan_path) if plan_path else None,
        "execution_summary_path": str(execution_summary_path) if execution_summary_path else None,
        "changed_files": _changed_files(cfg),
        "diff_path": None,
        "run_trace_path": str(run_trace_path) if run_trace_path else None,
        "repo_profile_paths": [str(path) for path in (repo_profile_paths or [])],
        "artifact_dir": str((workspace / "artifacts").resolve()),
    }
    return bundle


def _is_enabled(cfg: AgentConfig, verifier: VerifierProtocol) -> bool:
    if verifier.tier not in set(cfg.enabled_verifier_tiers or [1, 2]):
        return False
    if cfg.enabled_verifiers:
        return verifier.name in set(cfg.enabled_verifiers)
    if cfg.disabled_verifiers:
        return verifier.name not in set(cfg.disabled_verifiers)
    return True


async def _run_parallel(
    verifiers: list[VerifierProtocol],
    *,
    bundle: VerificationBundle,
    cfg: AgentConfig,
) -> list[VerifierResult]:
    tasks = [
        asyncio.create_task(asyncio.to_thread(verifier.verify, bundle, cfg=cfg))
        for verifier in verifiers
    ]
    return await asyncio.gather(*tasks)


def _aggregate_verdict(results: list[VerifierResult]) -> str:
    required_failures = [result for result in results if result["required"] and result["verdict"] != "PASS"]
    if required_failures:
        return "FAIL"
    if any(result["verdict"] == "PARTIAL" for result in results):
        return "PARTIAL"
    return "PASS"


def run_verifier_mesh(
    cfg: AgentConfig,
    *,
    bundle: VerificationBundle,
    verifiers: list[VerifierProtocol] | None = None,
) -> dict[str, Any]:
    verifier_pool = [verifier for verifier in (verifiers or default_verifiers()) if _is_enabled(cfg, verifier)]
    tiers = sorted({verifier.tier for verifier in verifier_pool})
    results: list[VerifierResult] = []

    for tier in tiers:
        tier_verifiers = [verifier for verifier in verifier_pool if verifier.tier == tier]
        sequential = [verifier for verifier in tier_verifiers if not verifier.run_in_parallel]
        parallel = [verifier for verifier in tier_verifiers if verifier.run_in_parallel]

        for verifier in sequential:
            result = verifier.verify(bundle, cfg=cfg)
            results.append(result)
            if result["required"] and result["verdict"] != "PASS":
                return {
                    "summary_verdict": "FAIL",
                    "required_failures": [result["name"]],
                    "results": results,
                    "bundle": bundle,
                    "stopped_at_tier": tier,
                }

        if parallel:
            tier_results = asyncio.run(_run_parallel(parallel, bundle=bundle, cfg=cfg))
            results.extend(tier_results)
            required_failures = [
                result["name"]
                for result in tier_results
                if result["required"] and result["verdict"] != "PASS"
            ]
            if required_failures:
                return {
                    "summary_verdict": "FAIL",
                    "required_failures": required_failures,
                    "results": results,
                    "bundle": bundle,
                    "stopped_at_tier": tier,
                }

    summary_verdict = _aggregate_verdict(results)
    return {
        "summary_verdict": summary_verdict,
        "required_failures": [
            result["name"]
            for result in results
            if result["required"] and result["verdict"] != "PASS"
        ],
        "results": results,
        "bundle": bundle,
        "stopped_at_tier": max(tiers) if tiers else None,
    }


def render_verification_markdown(report: dict[str, Any]) -> str:
    results = list(report.get("results") or [])
    lines = [
        "# Marvin Verification Report",
        "",
        f"Summary verdict: {report.get('summary_verdict', 'UNKNOWN')}",
        "",
        "## Required Failures",
    ]
    required_failures = list(report.get("required_failures") or [])
    if required_failures:
        lines.extend(f"- {name}" for name in required_failures)
    else:
        lines.append("- none")

    lines.extend(["", "## Per-Verifier Results", ""])
    for result in results:
        lines.extend(
            [
                f"### {result.get('name')}",
                f"- verdict: {result.get('verdict')}",
                f"- required: {result.get('required')}",
                f"- tier: {result.get('tier')}",
                f"- scope: {result.get('scope')}",
                "- findings:",
            ]
        )
        findings = list(result.get("findings") or [])
        if findings:
            lines.extend(f"  - {item}" for item in findings)
        else:
            lines.append("  - none")
        evidence = list(result.get("evidence") or [])
        lines.append("- evidence:")
        if evidence:
            lines.extend(f"  - {item}" for item in evidence)
        else:
            lines.append("  - none")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
