from openchami_coding_agent.utils import (
    format_token_counts,
    merge_tokens,
    slugify,
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


def test_format_token_counts_uses_sent_received_labels() -> None:
    assert format_token_counts(
        {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}
    ) == "sent=3 | received=5 | total=8"
