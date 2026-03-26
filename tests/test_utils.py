from openchami_coding_agent.utils import (
    estimate_prompt_tokens,
    extract_brief_model_message,
    format_compact_count,
    format_elapsed_runtime,
    format_token_counts,
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


def test_token_delta_returns_only_new_usage() -> None:
    assert token_delta(
        {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
        {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18},
    ) == {"input_tokens": 6, "output_tokens": 3, "total_tokens": 9}


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
