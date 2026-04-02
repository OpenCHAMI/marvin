# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

- Documented the `uv`-managed development and runtime workflow across README and contributing guidance.
- Documented top-level run mode, workspace selection fields, and the `--non-interactive` run flag.
- Added documentation for reference-only repositories via `repos[].read_only` / `repos[].read-only`.
- Added a shipped task example showing a reference-only repo used for execution context.
- Added `mode: analyze_workspace` to inspect a previous workspace, ask targeted clarification questions, and recommend YAML updates without rerunning execution.
- Added a merged recommended-config artifact for workspace analysis so suggested YAML changes do not need to be reconstructed by hand.
- Added a direct `analyze-workspace` CLI command so previous workspaces can be investigated without authoring a second task YAML.
- Added a structured `marvin_partial_success.json` artifact derived from the execution run trace, and began routing CLI/TUI completion summaries through shared summary helpers.
- Added an editable `marvin_operator_feedback.md` artifact and wired its notes back into resume, repair, and workspace-analysis flows, including optional hierarchical subplan refresh on resume.
- Added scoped hierarchical resume policy derived from failure classes so Marvin can refresh only the current subplan or all pending subplans instead of relying on a single yes/no hint.
- Added `marvin_recommended_operator_feedback.md` so workspace analysis can emit suggested edits for the next resume cycle alongside the merged YAML recommendation.

## [0.1.0] - 2026-03-18

- Refactored monolithic agent script into a modular package.
- Added project tooling configuration in `pyproject.toml`.
- Added unit tests for utility/config/execution-order behavior.
- Migrated package to `src/` layout.
- Added CI workflow, `.gitignore`, and repository metadata files.
