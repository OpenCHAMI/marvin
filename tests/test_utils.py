from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from openchami_coding_agent.utils import (
    build_token_cache_summary,
    estimate_prompt_tokens,
    extract_agent_tokens,
    extract_brief_model_message,
    extract_narrative_model_message,
    extract_stream_chunk_message,
    extract_structured_feedback_text,
    format_cache_hit_ratio,
    format_compact_count,
    format_elapsed_runtime,
    format_runtime_environment_summary,
    format_token_counts,
    invoke_agent,
    merge_tokens,
    slugify,
    token_delta,
    truncate_tail,
)


def test_slugify_normalizes_text() -> None:
    assert slugify("OpenCHAMI Task 01") == "openchami-task-01"


def test_slugify_fallback_when_empty() -> None:
    assert slugify("---") == "openchami-task"


def test_truncate_tail_keeps_tail_only() -> None:
    assert truncate_tail("abcdef", 3) == "def"


def test_merge_tokens_sums_each_field() -> None:
    assert merge_tokens(
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
    ) == {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}


def test_merge_tokens_keeps_cached_input_tokens_when_present() -> None:
    assert merge_tokens(
        {"input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 3, "total_tokens": 4},
        {"input_tokens": 5, "cached_input_tokens": 7, "output_tokens": 11, "total_tokens": 16},
    ) == {
        "input_tokens": 6,
        "cached_input_tokens": 9,
        "output_tokens": 14,
        "total_tokens": 20,
    }


def test_token_delta_returns_only_new_usage() -> None:
    assert token_delta(
        {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
        {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18},
    ) == {"input_tokens": 6, "output_tokens": 3, "total_tokens": 9}


def test_token_delta_keeps_cached_input_tokens_when_present() -> None:
    assert token_delta(
        {"input_tokens": 4, "cached_input_tokens": 3, "output_tokens": 5, "total_tokens": 9},
        {"input_tokens": 10, "cached_input_tokens": 8, "output_tokens": 8, "total_tokens": 18},
    ) == {
        "input_tokens": 6,
        "cached_input_tokens": 5,
        "output_tokens": 3,
        "total_tokens": 9,
    }


def test_estimate_prompt_tokens_uses_simple_character_ratio() -> None:
    assert estimate_prompt_tokens("abcdefgh") == 2


def test_format_token_counts_uses_sent_received_labels() -> None:
    assert format_token_counts(
        {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}
    ) == "sent=3 | received=5 | total=8"


def test_format_token_counts_uses_compact_units_for_large_values() -> None:
    assert format_token_counts(
        {"input_tokens": 12_300, "output_tokens": 502_000, "total_tokens": 1_233_000}
    ) == "sent=12.3K | received=502K | total=1.2M"


def test_format_token_counts_includes_cached_when_present() -> None:
    assert format_token_counts(
        {
            "input_tokens": 12_300,
            "cached_input_tokens": 10_000,
            "output_tokens": 502_000,
            "total_tokens": 1_233_000,
        }
    ) == "sent=12.3K | cached=10K | received=502K | total=1.2M"


def test_format_runtime_environment_summary_renders_detected_versions(monkeypatch) -> None:
    versions = {
        "openchami-coding-agent": "0.1.0",
        "ursa-ai": "0.15.6",
    }

    monkeypatch.setattr(
        "openchami_coding_agent.utils.metadata.version",
        lambda name: versions[name],
    )
    monkeypatch.setattr("openchami_coding_agent.utils.platform.python_implementation", lambda: "CPython")
    monkeypatch.setattr("openchami_coding_agent.utils.platform.python_version", lambda: "3.12.13")
    monkeypatch.setattr("openchami_coding_agent.utils.platform.system", lambda: "Darwin")
    monkeypatch.setattr("openchami_coding_agent.utils.platform.release", lambda: "24.5.0")
    monkeypatch.setattr("openchami_coding_agent.utils.platform.machine", lambda: "arm64")
    monkeypatch.setattr("openchami_coding_agent.utils.sys", type("FakeSys", (), {"executable": "/venv/bin/python"})())

    assert format_runtime_environment_summary() == "\n".join(
        [
            "Runtime environment",
            "Marvin: 0.1.0",
            "Python: CPython 3.12.13",
            "URSA: 0.15.6",
            "Platform: Darwin 24.5.0",
            "Arch: arm64",
            "Executable: /venv/bin/python",
        ]
    )


def test_format_runtime_environment_summary_falls_back_when_package_metadata_missing(monkeypatch) -> None:
    def fake_version(name: str) -> str:
        raise __import__("importlib").metadata.PackageNotFoundError(name)

    monkeypatch.setattr("openchami_coding_agent.utils.metadata.version", fake_version)
    monkeypatch.setattr("openchami_coding_agent.utils.platform.python_implementation",
                         lambda: "CPython")
    monkeypatch.setattr("openchami_coding_agent.utils.platform.python_version", lambda: "3.12.13")
    monkeypatch.setattr("openchami_coding_agent.utils.platform.system", lambda: "Linux")
    monkeypatch.setattr("openchami_coding_agent.utils.platform.release", lambda: "6.10")
    monkeypatch.setattr("openchami_coding_agent.utils.platform.machine", lambda: "x86_64")
    monkeypatch.setattr("openchami_coding_agent.utils.sys",  
                        type("FakeSys", (), {"executable": "/usr/bin/python3"})())

    summary = format_runtime_environment_summary()

    assert "Marvin: unknown" in summary
    assert "URSA: unknown" in summary
    assert "Python: CPython 3.12.13" in summary


def test_extract_agent_tokens_reads_cached_prompt_tokens_details() -> None:
    class FakeAgent:
        class telemetry:  # noqa: N801
            class llm:  # noqa: N801
                samples = [
                    {
                        "metrics": {
                            "usage_rollup": {
                                "input_tokens": 2006,
                                "output_tokens": 300,
                                "total_tokens": 2306,
                                "prompt_tokens_details": {"cached_tokens": 1920},
                            }
                        }
                    }
                ]

    assert extract_agent_tokens(FakeAgent()) == {
        "input_tokens": 2006,
        "cached_input_tokens": 1920,
        "output_tokens": 300,
        "total_tokens": 2306,
    }


def test_build_token_cache_summary_reports_uncached_and_ratio() -> None:
    assert build_token_cache_summary(
        {"input_tokens": 2006, "cached_input_tokens": 1920}
    ) == {
        "input_tokens": 2006,
        "cached_input_tokens": 1920,
        "uncached_input_tokens": 86,
        "cache_hit_ratio": 0.9571,
    }


def test_format_cache_hit_ratio_renders_readable_percent() -> None:
    assert format_cache_hit_ratio(0.9571) == "95.7%"
    assert format_cache_hit_ratio(1.0) == "100%"


def test_format_compact_count_threshold_behavior() -> None:
    assert format_compact_count(9_999) == "9999"
    assert format_compact_count(10_000) == "10K"
    assert format_compact_count(1_250_000) == "1.2M"


def test_format_elapsed_runtime_progression() -> None:
    assert format_elapsed_runtime(7.2) == "7s"
    assert format_elapsed_runtime(90.0) == "01:30"
    assert format_elapsed_runtime(3725.0) == "01:02"


def test_extract_brief_model_message_uses_first_meaningful_line() -> None:
    content = """
    ## Step update
    - Implemented network-config serialization.
    - Added tests.
    """
    assert (
        extract_brief_model_message(content, fallback="fallback")
        == "Step update"
    )


def test_extract_brief_model_message_falls_back_when_empty() -> None:
    assert extract_brief_model_message("   ", fallback="fallback") == "fallback"


def test_extract_narrative_model_message_discards_fenced_patch_content() -> None:
    text = extract_narrative_model_message(
        "Applied the patch and reran tests.\n```diff\n*** Begin Patch\n+print('x')\n```",
        fallback="fallback",
        max_chars=None,
    )

    assert text == "Applied the patch and reran tests."


def test_extract_stream_chunk_message_prefers_assistant_message() -> None:
    chunk = {
        "messages": [
            HumanMessage(content="do the thing"),
            AIMessage(content="Inspecting repository state before editing."),
        ]
    }

    assert extract_stream_chunk_message(chunk) == "Inspecting repository state before editing."


def test_extract_structured_feedback_text_humanizes_step_payloads() -> None:
    text = extract_structured_feedback_text(
        {
            "steps": [
                {
                    "name": "inspect",
                    "description": "Inspect config_init.py and identify prompt construction.",
                },
                {
                    "name": "patch",
                    "description": (
                        "Update the streamed feedback formatter to unwrap "
                        "step descriptions."
                    ),
                },
            ]
        }
    )

    assert "Planned steps:" in text
    assert "inspect: Inspect config_init.py and identify prompt construction" in text
    assert "patch: Update the streamed feedback formatter" in text


def test_extract_stream_chunk_message_humanizes_json_strings() -> None:
    chunk = {
        "message": (
            '{"steps": [{"description": "Inspect repository state before editing."}, '
            '{"description": "Apply the change and run focused tests."}]}'
        )
    }

    text = extract_stream_chunk_message(chunk)

    assert text.startswith("Planned steps:")
    assert "Inspect repository state before editing" in text


def test_extract_stream_chunk_message_humanizes_json_in_assistant_message() -> None:
    chunk = {
        "messages": [
            HumanMessage(content="do the thing"),
            AIMessage(
                content=(
                    '{"steps": ['
                    "{\"name\": \"inspect\", \"description\": "
                    "\"Inspect repository and map current token APIs.\"}, "
                    "{\"name\": \"patch\", \"description\": "
                    "\"Update the token formatter and rerun tests.\"}"
                    ']}'
                )
            ),
        ]
    }

    text = extract_stream_chunk_message(chunk, max_chars=None)

    assert text.startswith("Planned steps:")
    assert "inspect: Inspect repository and map current token APIs" in text
    assert "patch: Update the token formatter and rerun tests" in text


def test_extract_stream_chunk_message_omits_code_blocks_from_plain_commentary() -> None:
    chunk = {
        "messages": [
            HumanMessage(content="do the thing"),
            AIMessage(
                content=(
                    "Inspecting repository state before editing.\n\n"
                    "```python\n"
                    "def noisy():\n"
                    "    return 'patch body'\n"
                    "```"
                )
            ),
        ]
    }

    text = extract_stream_chunk_message(chunk, max_chars=None)

    assert text == "Inspecting repository state before editing."


def test_extract_stream_chunk_message_unwraps_internal_wrapper_messages() -> None:
    class FakeOverwrite:
        def __init__(self, value):
            self.value = value

        def __str__(self) -> str:
            return f"Overwrite(value={self.value!r})"

    chunk = {
        "messages": [
            FakeOverwrite(
                [
                    SystemMessage(content="system prompt"),
                    AIMessage(content="Capture baseline behavior and add regression coverage."),
                ]
            )
        ]
    }

    text = extract_stream_chunk_message(chunk, max_chars=None)

    assert text == "Capture baseline behavior and add regression coverage."


def test_invoke_agent_uses_stream_and_emits_feedback() -> None:
    feedback: list[str] = []

    class FakeStreamingAgent:
        def stream(self, inputs):
            assert inputs["messages"][0].content == "do the thing"
            yield {
                "messages": [
                    HumanMessage(content="do the thing"),
                    AIMessage(content="Inspecting repository state before editing."),
                ]
            }
            yield {
                "messages": [
                    HumanMessage(content="do the thing"),
                    AIMessage(content="Applied the change and ran focused tests."),
                ]
            }

    invocation = invoke_agent(
        FakeStreamingAgent(),
        "do the thing",
        verbose_io=False,
        feedback_callback=feedback.append,
    )

    assert feedback == [
        "Inspecting repository state before editing.",
        "Applied the change and ran focused tests.",
    ]
    assert invocation.content == "Applied the change and ran focused tests."
