"""Utilities used by multiple OpenCHAMI coding agent modules."""

from __future__ import annotations

import io
import json
import math
import re
import subprocess
from collections.abc import Callable
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
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    telemetry = getattr(agent, "telemetry", None)
    llm = getattr(telemetry, "llm", None)
    samples = getattr(llm, "samples", None)
    if not samples:
        return totals
    for sample in samples:
        metrics = sample.get("metrics") or {}
        rollup = metrics.get("usage_rollup") or metrics.get("usage") or {}
        totals["input_tokens"] += int(rollup.get("input_tokens", 0) or 0)
        totals["cached_input_tokens"] += _extract_cached_input_tokens(sample, metrics, rollup)
        totals["output_tokens"] += int(rollup.get("output_tokens", 0) or 0)
        totals["total_tokens"] += int(rollup.get("total_tokens", 0) or 0)
    return totals


def _read_nested_int(payload: Any, path: tuple[str, ...]) -> int:
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return 0
        current = current.get(key)
    try:
        return int(current or 0)
    except (TypeError, ValueError):
        return 0


def _extract_cached_input_tokens(
    sample: Any,
    metrics: dict[str, Any],
    rollup: dict[str, Any],
) -> int:
    candidates = (
        _read_nested_int(rollup, ("cached_input_tokens",)),
        _read_nested_int(rollup, ("input_cached_tokens",)),
        _read_nested_int(rollup, ("prompt_tokens_details", "cached_tokens")),
        _read_nested_int(metrics, ("prompt_tokens_details", "cached_tokens")),
        _read_nested_int(metrics, ("usage", "prompt_tokens_details", "cached_tokens")),
        _read_nested_int(metrics, ("usage_rollup", "prompt_tokens_details", "cached_tokens")),
        _read_nested_int(sample, ("prompt_tokens_details", "cached_tokens")),
        _read_nested_int(sample, ("usage", "prompt_tokens_details", "cached_tokens")),
    )
    return max(candidates)


def merge_tokens(base: dict[str, int], extra: dict[str, int]) -> dict[str, int]:
    merged = {
        "input_tokens": int(base.get("input_tokens", 0)) + int(extra.get("input_tokens", 0)),
        "output_tokens": int(base.get("output_tokens", 0)) + int(extra.get("output_tokens", 0)),
        "total_tokens": int(base.get("total_tokens", 0)) + int(extra.get("total_tokens", 0)),
    }
    cached_total = int(base.get("cached_input_tokens", 0)) + int(
        extra.get("cached_input_tokens", 0)
    )
    if "cached_input_tokens" in base or "cached_input_tokens" in extra or cached_total:
        merged["cached_input_tokens"] = cached_total
    return merged


def token_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    delta = {
        "input_tokens": max(
            0,
            int(after.get("input_tokens", 0)) - int(before.get("input_tokens", 0)),
        ),
        "output_tokens": max(
            0,
            int(after.get("output_tokens", 0)) - int(before.get("output_tokens", 0)),
        ),
        "total_tokens": max(
            0,
            int(after.get("total_tokens", 0)) - int(before.get("total_tokens", 0)),
        ),
    }
    cached_delta = max(
        0,
        int(after.get("cached_input_tokens", 0))
        - int(before.get("cached_input_tokens", 0)),
    )
    if "cached_input_tokens" in before or "cached_input_tokens" in after or cached_delta:
        delta["cached_input_tokens"] = cached_delta
    return delta


def estimate_prompt_tokens(text: str, *, chars_per_token: int = 4) -> int:
    cleaned = text.strip()
    if not cleaned:
        return 0
    divisor = max(1, chars_per_token)
    return max(1, math.ceil(len(cleaned) / divisor))


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
    cached = int(token_usage.get("cached_input_tokens", 0) or 0)
    parts = [f"sent={format_compact_count(int(token_usage.get('input_tokens', 0)))}"]
    if cached:
        parts.append(f"cached={format_compact_count(cached)}")
    parts.extend(
        [
            f"received={format_compact_count(int(token_usage.get('output_tokens', 0)))}",
            f"total={format_compact_count(int(token_usage.get('total_tokens', 0)))}",
        ]
    )
    return " | ".join(parts)


def build_token_cache_summary(token_usage: dict[str, int]) -> dict[str, int | float]:
    sent = max(0, int(token_usage.get("input_tokens", 0) or 0))
    cached = max(0, int(token_usage.get("cached_input_tokens", 0) or 0))
    cached = min(sent, cached)
    uncached = max(0, sent - cached)
    ratio = round(cached / sent, 4) if sent else 0.0
    return {
        "input_tokens": sent,
        "cached_input_tokens": cached,
        "uncached_input_tokens": uncached,
        "cache_hit_ratio": ratio,
    }


def format_cache_hit_ratio(ratio: float | int | None) -> str:
    try:
        normalized = float(ratio or 0.0)
    except (TypeError, ValueError):
        normalized = 0.0
    normalized = max(0.0, min(1.0, normalized))
    percent = normalized * 100.0
    if percent.is_integer():
        return f"{int(percent)}%"
    return f"{percent:.1f}%"


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


def extract_narrative_model_message(
    text: str | None,
    fallback: str,
    *,
    max_chars: int | None = None,
    max_lines: int = 3,
) -> str:
    if not text or not text.strip():
        return fallback

    stripped = re.sub(r"```[\s\S]*?```", "\n", text)
    narrative_lines: list[str] = []
    stop_markers = (
        "*** Begin Patch",
        "*** End Patch",
        "*** Update File:",
        "*** Add File:",
        "*** Delete File:",
        "diff --git",
        "@@",
        "+++ ",
        "--- ",
    )

    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line:
            if narrative_lines:
                break
            continue
        if line.startswith(stop_markers):
            break
        if re.match(r"^[+\-](?!\s)", line):
            break
        if re.match(r"^(from\s+\S+\s+import\s+|import\s+\S+|def\s+\w+\(|class\s+\w+\b)", line):
            break
        if re.match(r"^[\[{].*[\]}]$", line) and len(line) > 80:
            continue

        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        narrative_lines.append(line)
        if len(narrative_lines) >= max(1, max_lines):
            break

    message = " ".join(narrative_lines).strip()
    if not message:
        return fallback
    if max_chars is not None and len(message) > max_chars:
        message = message[: max_chars - 1].rstrip() + "…"
    return message


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


def _message_content_text(value: Any) -> str:
    value = _unwrap_internal_message_value(value)

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, list):
        message_text = _extract_message_list_text(value)
        if message_text:
            return message_text

    content = getattr(value, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("output")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            else:
                text = getattr(item, "text", None) or getattr(item, "content", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return ""


def _is_human_message_like(value: Any) -> bool:
    value_type = str(getattr(value, "type", "") or "").lower()
    class_name = value.__class__.__name__.lower()
    return value_type == "human" or "human" in class_name


def _is_system_message_like(value: Any) -> bool:
    value_type = str(getattr(value, "type", "") or "").lower()
    class_name = value.__class__.__name__.lower()
    return value_type == "system" or "system" in class_name


def _unwrap_internal_message_value(value: Any) -> Any:
    current = value
    for _ in range(4):
        if isinstance(current, (str, bytes, dict, list, tuple, Path)):
            break
        inner = getattr(current, "value", None)
        if inner is None or inner is current:
            break
        current = inner
    return current


def _extract_message_list_text(items: list[Any]) -> str:
    for item in reversed(items):
        if _is_human_message_like(item) or _is_system_message_like(item):
            continue
        text = _message_content_text(item)
        if text:
            return text

    parts: list[str] = []
    for item in items:
        if _is_human_message_like(item) or _is_system_message_like(item):
            continue
        text = _message_content_text(item)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _coerce_json_text(value: str) -> Any:
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return None


def _structured_feedback_lines(value: Any) -> list[str]:
    value = _unwrap_internal_message_value(value)

    if _is_human_message_like(value):
        return []

    message_text = _message_content_text(value)
    if message_text:
        parsed = _coerce_json_text(message_text)
        if parsed is not None:
            return _structured_feedback_lines(parsed)
        cleaned = extract_narrative_model_message(
            message_text,
            fallback="",
            max_chars=None,
        )
        return [cleaned] if cleaned else []

    if isinstance(value, str):
        parsed = _coerce_json_text(value)
        if parsed is not None:
            return _structured_feedback_lines(parsed)
        cleaned = extract_narrative_model_message(
            value,
            fallback="",
            max_chars=None,
        )
        return [cleaned] if cleaned else []

    if isinstance(value, list):
        collected: list[str] = []
        productive_items = 0
        for item in value:
            if len(collected) >= 3:
                break
            item_lines = _structured_feedback_lines(item)
            if item_lines:
                productive_items += 1
            for line in item_lines:
                if line and line not in collected:
                    collected.append(line)
                if len(collected) >= 3:
                    break
        if productive_items > len(collected) and collected:
            collected.append(f"and {productive_items - len(collected)} more")
        return collected

    if isinstance(value, dict):
        collected: list[str] = []
        name = re.sub(r"\s+", " ", str(value.get("name") or "")).strip()
        description = re.sub(r"\s+", " ", str(value.get("description") or "")).strip()
        index_value = value.get("index", value.get("step_index", value.get("number")))

        if name and description:
            prefix = ""
            if isinstance(index_value, int):
                prefix = f"Step {index_value + 1}: "
            collected.append(f"{prefix}{name}: {description}")
        elif name:
            prefix = ""
            if isinstance(index_value, int):
                prefix = f"Step {index_value + 1}: "
            collected.append(prefix + name)
        elif description:
            collected.append(description)

        primary_keys = (
            "description",
            "summary",
            "message",
            "status",
            "update",
            "thought",
            "reasoning",
            "assistant",
            "response",
            "content",
            "text",
        )
        for key in primary_keys:
            if key not in value:
                continue
            for line in _structured_feedback_lines(value.get(key)):
                if line and line not in collected:
                    collected.append(line)

        step_like = value.get("step")
        if step_like is not None:
            for line in _structured_feedback_lines(step_like):
                if line and line not in collected:
                    collected.append(line)

        steps = value.get("steps")
        if isinstance(steps, list) and steps:
            step_lines = _structured_feedback_lines(steps)
            if step_lines:
                prefix = "Planned steps: " if not collected else "Next steps: "
                collected.append(prefix + "; ".join(step_lines))

        if collected:
            return collected

        fallback_bits: list[str] = []
        for item in value.values():
            fallback_bits.extend(_structured_feedback_lines(item))
            if len(fallback_bits) >= 3:
                break
        return fallback_bits

    return []


def extract_structured_feedback_text(value: Any) -> str:
    lines = _structured_feedback_lines(value)
    if not lines:
        return ""
    return " ".join(line.strip() for line in lines if line).strip()


def extract_response_content(response: Any) -> str:
    response = _unwrap_internal_message_value(response)

    if isinstance(response, dict):
        messages = response.get("messages") or []
        if isinstance(messages, list):
            for message in reversed(messages):
                if _is_human_message_like(message):
                    continue
                text = _message_content_text(message)
                if text:
                    return text

        for key in ("assistant", "completion", "response", "output", "message", "content", "text"):
            value = response.get(key)
            text = _message_content_text(value) if not isinstance(value, str) else value.strip()
            if text:
                return text

        for value in response.values():
            text = extract_response_content(value)
            if text:
                return text
        return str(response)

    if isinstance(response, list):
        for item in reversed(response):
            text = extract_response_content(item)
            if text:
                return text
        return ""

    text = _message_content_text(response)
    if text:
        return text

    if hasattr(response, "content"):
        return _message_content_text(response)
    return str(response)


def extract_stream_chunk_message(
    chunk: Any,
    fallback: str = "",
    *,
    max_chars: int | None = 220,
) -> str:
    text = extract_response_content(chunk)
    preferred_text = ""
    structured_text = extract_structured_feedback_text(chunk)
    if not text or text == str(chunk):
        if isinstance(chunk, dict):
            preferred_keys = (
                "status",
                "update",
                "thought",
                "reasoning",
                "assistant",
                "response",
                "message",
                "content",
                "text",
            )
            for key in preferred_keys:
                if key not in chunk:
                    continue
                value = chunk.get(key)
                candidate = extract_response_content(value)
                if candidate and candidate != str(value):
                    preferred_text = candidate
                    break
    final_text = structured_text or preferred_text or ("" if text == str(chunk) else text)
    if not final_text:
        return fallback
    if max_chars is None:
        return re.sub(r"\s+", " ", final_text).strip() or fallback
    return extract_brief_model_message(final_text, fallback, max_chars=max_chars)


def invoke_agent(
    agent: Any,
    prompt: str,
    verbose_io: bool,
    *,
    feedback_callback: Callable[[str], None] | None = None,
) -> InvocationCapture:
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    out_ctx = nullcontext() if verbose_io else redirect_stdout(stdout_buffer)
    err_ctx = nullcontext() if verbose_io else redirect_stderr(stderr_buffer)
    with out_ctx, err_ctx:
        if hasattr(agent, "stream"):
            last_chunk: Any | None = None
            last_contentful_chunk: Any | None = None
            last_feedback = ""
            for chunk in agent.stream({"messages": [HumanMessage(content=prompt)]}):
                last_chunk = chunk
                chunk_content = extract_response_content(chunk)
                if chunk_content and chunk_content != str(chunk):
                    last_contentful_chunk = chunk
                if feedback_callback is None:
                    continue
                feedback = extract_stream_chunk_message(chunk, fallback="", max_chars=None)
                if feedback and feedback != last_feedback:
                    last_feedback = feedback
                    feedback_callback(feedback)
            if last_chunk is None:
                response = agent.invoke({"messages": [HumanMessage(content=prompt)]})
            else:
                response = last_contentful_chunk or last_chunk
        else:
            response = agent.invoke({"messages": [HumanMessage(content=prompt)]})

    content = extract_response_content(response)

    return InvocationCapture(
        content=content,
        captured_stdout=stdout_buffer.getvalue(),
        captured_stderr=stderr_buffer.getvalue(),
        raw_response=response,
    )
