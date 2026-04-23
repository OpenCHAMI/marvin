Repository-first OpenCHAMI guidance:

Operating Principles
- Ground all decisions in repository evidence before changing code.
- Preserve existing behavior unless the task requires explicit contract changes.
- Prefer incremental, reviewable edits that keep rollback risk low.

Cross-Repo Awareness
- Call out changes likely to affect sibling OpenCHAMI repositories.
- Highlight schema, API, and deployment coupling when present.
- Avoid hardcoding repo-specific policy where a profile-driven rule is available.

Reliability and Safety
- Keep verification explicit and evidence-backed.
- Avoid hidden side effects in automation paths.
- Surface operator-impacting changes early in summaries and artifacts.
