"""Utilities used by multiple OpenCHAMI coding agent modules."""

from __future__ import annotations

import io
import re
import subprocess
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from .models import InvocationCapture
from .ursa_compat import load_json_file, save_json_file


def to_plain_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: to_plain_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_plain_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(to_plain_data(v) for v in value)
    if hasattr(value, "__dict__") and not isinstance(value, (str, bytes, Path)):
        try:
            return {k: to_plain_data(v) for k, v in vars(value).items()}
        except TypeError:
            pass
    return value


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "openchami-task"


def run_command(
    args: list[str], cwd: Path | None = None, timeout: int = 900
) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return 1, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def progress_file(base: Path, rel_path: str) -> Path:
    return (base / rel_path).resolve()


def load_exec_progress(base: Path, rel_path: str) -> dict[str, Any]:
    return load_json_file(progress_file(base, rel_path), {})


def save_exec_progress(base: Path, rel_path: str, payload: dict[str, Any]) -> Path:
    path = progress_file(base, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json_file(path, payload)
    return path


def write_text_file(base: Path, rel_path: str, content: str) -> Path:
    path = (base / rel_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return path


def write_json_file(base: Path, rel_path: str, payload: Any) -> Path:
    path = (base / rel_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json_file(path, payload)
    return path


def render_yaml_text(payload: Any) -> str:
    import yaml

    class _LiteralSafeDumper(yaml.SafeDumper):
        pass

    def _represent_str(dumper: Any, data: str) -> Any:
        style = "|" if "\n" in data else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)

    _LiteralSafeDumper.add_representer(str, _represent_str)
    return yaml.dump(
        payload,
        Dumper=_LiteralSafeDumper,
        sort_keys=False,
        allow_unicode=False,
        width=100,
    )


def truncate_tail(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[-max_chars:]


def extract_agent_tokens(agent: Any) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    telemetry = getattr(agent, "telemetry", None)
    llm = getattr(telemetry, "llm", None)
    samples = getattr(llm, "samples", None)
    if not samples:
        return totals
    for sample in samples:
        metrics = sample.get("metrics") or {}
        rollup = metrics.get("usage_rollup") or metrics.get("usage") or {}
        totals["input_tokens"] += int(rollup.get("input_tokens", 0) or 0)
        totals["output_tokens"] += int(rollup.get("output_tokens", 0) or 0)
        totals["total_tokens"] += int(rollup.get("total_tokens", 0) or 0)
    return totals


def merge_tokens(base: dict[str, int], extra: dict[str, int]) -> dict[str, int]:
    return {
        "input_tokens": int(base.get("input_tokens", 0)) + int(extra.get("input_tokens", 0)),
        "output_tokens": int(base.get("output_tokens", 0)) + int(extra.get("output_tokens", 0)),
        "total_tokens": int(base.get("total_tokens", 0)) + int(extra.get("total_tokens", 0)),
    }


def format_compact_count(value: int) -> str:
    number = int(value)
    absolute = abs(number)
    if absolute < 10_000:
        return str(number)
    if absolute < 1_000_000:
        compact = f"{number / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{compact}K"
    compact = f"{number / 1_000_000:.1f}".rstrip("0").rstrip(".")
    return f"{compact}M"


def format_elapsed_runtime(elapsed_sec: float | None) -> str:
    if elapsed_sec is None:
        return "-"
    seconds = max(0, int(elapsed_sec))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes = seconds // 60
        remaining = seconds % 60
        return f"{minutes:02d}:{remaining:02d}"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def format_token_counts(token_usage: dict[str, int]) -> str:
    return (
        f"sent={format_compact_count(int(token_usage.get('input_tokens', 0)))} | "
        f"received={format_compact_count(int(token_usage.get('output_tokens', 0)))} | "
        f"total={format_compact_count(int(token_usage.get('total_tokens', 0)))}"
    )


def extract_brief_model_message(
    text: str | None,
    fallback: str,
    *,
    max_chars: int = 180,
) -> str:
    if not text or not text.strip():
        return fallback

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cleaned_lines.append(line)

    message = cleaned_lines[0] if cleaned_lines else re.sub(r"\s+", " ", text).strip()
    if len(message) > max_chars:
        message = message[: max_chars - 1].rstrip() + "…"
    return message or fallback


def extract_agent_status_message(
    agent: Any,
    fallback: str,
    *,
    max_chars: int = 180,
) -> str:
    telemetry = getattr(agent, "telemetry", None)
    llm = getattr(telemetry, "llm", None)
    samples = getattr(llm, "samples", None) or []
    if not isinstance(samples, list) or not samples:
        return fallback

    preferred_keys = (
        "assistant",
        "completion",
        "response",
        "output",
        "message",
        "content",
        "text",
    )
    banned_keys = ("prompt", "input", "system", "user", "human", "request")

    for sample in reversed(samples):
        if not isinstance(sample, dict):
            continue
        candidates: list[str] = []
        for key, value in sample.items():
            key_text = str(key).lower()
            if any(blocked in key_text for blocked in banned_keys):
                continue
            if not isinstance(value, str) or not value.strip():
                continue
            if any(pref in key_text for pref in preferred_keys):
                return extract_brief_model_message(value, fallback, max_chars=max_chars)
            candidates.append(value)
        if candidates:
            return extract_brief_model_message(candidates[0], fallback, max_chars=max_chars)

    return fallback


def invoke_agent(agent: Any, prompt: str, verbose_io: bool) -> InvocationCapture:
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    out_ctx = nullcontext() if verbose_io else redirect_stdout(stdout_buffer)
    err_ctx = nullcontext() if verbose_io else redirect_stderr(stderr_buffer)
    with out_ctx, err_ctx:
        response = agent.invoke({"messages": [HumanMessage(content=prompt)]})

    if isinstance(response, dict):
        messages = response.get("messages") or []
        last = messages[-1] if messages else response
        content = getattr(last, "content", None) or str(last)
    else:
        content = getattr(response, "content", None) or str(response)

    return InvocationCapture(
        content=content,
        captured_stdout=stdout_buffer.getvalue(),
        captured_stderr=stderr_buffer.getvalue(),
        raw_response=response,
    )
