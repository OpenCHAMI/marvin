"""Git activity collection helpers for live TUI telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .utils import run_command


@dataclass
class RepoGitActivity:
    repo_name: str
    branch: str
    changed_files: int
    added_lines: int
    deleted_lines: int
    last_commit: str
    recent_files: str
    is_git_repo: bool


def parse_numstat_output(output: str) -> tuple[int, int]:
    added = 0
    deleted = 0
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("\t")
        if len(parts) < 2:
            continue
        add_raw, del_raw = parts[0], parts[1]
        if add_raw.isdigit():
            added += int(add_raw)
        if del_raw.isdigit():
            deleted += int(del_raw)
    return added, deleted


def parse_status_porcelain(output: str) -> tuple[int, list[str]]:
    files: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        path_part = line[3:] if len(line) > 3 else line
        if " -> " in path_part:
            path_part = path_part.split(" -> ")[-1]
        files.append(path_part.strip())
    return len(files), files


def _git(cwd: Path, args: list[str]) -> tuple[int, str, str]:
    return run_command(["git", *args], cwd=cwd, timeout=30)


def collect_repo_git_activity(repo_name: str, repo_path: Path) -> RepoGitActivity:
    code, _, _ = _git(repo_path, ["rev-parse", "--is-inside-work-tree"])
    if code != 0:
        return RepoGitActivity(
            repo_name=repo_name,
            branch="-",
            changed_files=0,
            added_lines=0,
            deleted_lines=0,
            last_commit="not a git repo",
            recent_files="-",
            is_git_repo=False,
        )

    _, branch_out, _ = _git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    _, commit_out, _ = _git(repo_path, ["log", "-1", "--pretty=format:%h %s"])
    _, status_out, _ = _git(repo_path, ["status", "--porcelain"])
    _, diff_unstaged_out, _ = _git(repo_path, ["diff", "--numstat"])
    _, diff_staged_out, _ = _git(repo_path, ["diff", "--cached", "--numstat"])

    changed_files, file_list = parse_status_porcelain(status_out)
    unstaged_add, unstaged_del = parse_numstat_output(diff_unstaged_out)
    staged_add, staged_del = parse_numstat_output(diff_staged_out)
    added_lines = unstaged_add + staged_add
    deleted_lines = unstaged_del + staged_del
    recent_files = ", ".join(file_list[:3]) if file_list else "-"

    return RepoGitActivity(
        repo_name=repo_name,
        branch=(branch_out.strip() or "-"),
        changed_files=changed_files,
        added_lines=added_lines,
        deleted_lines=deleted_lines,
        last_commit=(commit_out.strip() or "<no commits>"),
        recent_files=recent_files,
        is_git_repo=True,
    )