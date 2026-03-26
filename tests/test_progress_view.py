from openchami_coding_agent.models import ProgressSnapshot
from openchami_coding_agent.progress_view import (
    build_progress_display,
    progress_snapshot_key,
    repo_status_label,
    stage_label,
)


def test_build_progress_display_derives_shared_fields() -> None:
    snapshot = ProgressSnapshot(
        stage="execution",
        detail="Applying step 2/5",
        workspace="ws-1",
        token_usage={"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500},
        completed_repos=1,
        total_repos=3,
        failed_repos=0,
        retries=2,
        elapsed_sec=12.3,
    )

    display = build_progress_display(snapshot)

    assert display.workspace == "ws-1"
    assert display.stage_label == "Executing"
    assert display.repo_progress == "1/3"
    assert display.failures == "0"
    assert display.retries == "2"
    assert display.tokens
    assert display.elapsed


def test_progress_snapshot_key_is_stable_for_same_snapshot() -> None:
    snapshot = ProgressSnapshot(stage="planning", detail="x")
    assert progress_snapshot_key(snapshot) == progress_snapshot_key(snapshot)


def test_shared_label_helpers_fallback_to_input() -> None:
    assert stage_label("unknown") == "unknown"
    assert repo_status_label("mystery") == "mystery"
