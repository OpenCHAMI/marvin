# Marvin

Marvin is a YAML-driven OpenCHAMI coding agent built on top of [LANL URSA](https://github.com/lanl/ursa).

It reads a task config, creates a contained workspace, plans the work, executes it, runs repository checks, attempts repair when reality objects, and leaves behind enough artifacts that a human can inspect what happened after the optimism evaporates.

Marvin is not a general-purpose autonomous coding free-for-all. It is opinionated, workspace-contained, and aimed at OpenCHAMI development workflows.

## What Marvin Actually Does

Given a YAML config, Marvin will:

1. Resolve or create a workspace under the directory where you launched it.
2. Materialize repositories into that workspace by cloning or copying them.
3. Ask a planner model for an implementation plan.
4. Execute that plan step by step, or main-step by sub-step in hierarchical mode.
5. Run each repository's validation commands.
6. Attempt repair passes when checks fail.
7. Persist checkpoints, progress JSON, plan artifacts, and a final summary.

If you use the Textual TUI, Marvin also provides a live dashboard with plan progress, token reporting, repository status, checkpoints, and commentary bleak enough to remain on brand.

## Why It Exists

OpenCHAMI work often starts as a vague issue, proposal, or architecture note, then turns into multi-repo implementation with enough moving parts to embarrass a simpler script. Marvin exists to make that loop tolerable.

It gives you:

- Repeatable, reviewable YAML task definitions.
- A contained workspace instead of random edits in whatever directory happened to be nearby.
- Explicit plans and execution artifacts.
- Resume and checkpoint support.
- Structured progress and token reporting.
- A CLI `init` flow that can draft future Marvin configs from rough source material.

## Installation

Marvin requires Python 3.12 or newer.

Install development dependencies with `uv`:

```bash
uv sync --frozen --extra dev
```

Run Marvin and project tooling through `uv run` so commands execute inside the managed environment instead of whichever interpreter happens to be nearby.

Run Marvin in Docker with your local workspace bind-mounted into the container:

```bash
docker build -t marvin .
docker run --rm -it \
	-e OPENAI_API_KEY="$OPENAI_API_KEY" \
	-v "$(pwd):/workspace" \
	marvin tokensmith-amsc-task.yaml
```

The image now follows URSA's `uv`-based container style more closely and installs Marvin from the locked project metadata in `pyproject.toml` and `uv.lock`. Native build dependencies are kept in a separate builder stage and only the working runtime environment is shipped. The container runs from `/workspace`, so the mounted directory is where Marvin reads configs and creates or reuses workspaces. If your repository checks need extra toolchains such as `go`, extend the image rather than pretending Python can solve that.

The bundled runtime image includes a current Go toolchain plus `make`, which means `go`, `gofmt`, and `go vet` are available to Marvin when it needs to work on OpenCHAMI Go services. Tools that normally require separate network installs such as `golangci-lint`, `goimports`, or `govulncheck` are not bundled yet; if a target repository requires them in-container, extend the image or preinstall them in a derivative image.

If you are using OpenAI-backed models, set `OPENAI_API_KEY` before running Marvin. It will not infer your credentials by sheer bitterness.

## Quick Start

Generate a config interactively:

```bash
uv run marvin init
```

Generate a config non-interactively from a source document:

```bash
uv run marvin init --source-file tokensmith-amsc.md --output tokensmith-amsc-task.yaml
```

Run Marvin with that config:

```bash
uv run marvin tokensmith-amsc-task.yaml
```

Run with the TUI dashboard:

```bash
uv run marvin tokensmith-amsc-task.yaml --tui
```

Resume an existing workspace from a checkpoint:

```bash
uv run marvin tokensmith-amsc-task.yaml --workspace ./tokensmith-run --resume --resume-from executor_checkpoint_5.db
```

Analyze a previously used workspace and get YAML recommendations from an existing analysis-mode task YAML:

```bash
uv run marvin path/to/task.yaml --workspace ./tokensmith-run --resume
```

Set `mode: analyze_workspace` in the YAML when you want Marvin to inspect an existing workspace instead of planning or executing code. In that mode Marvin uses the planner agent in hierarchical mode, reviews prior artifacts, asks clarification questions when needed unless `--non-interactive` is set, and writes separate workspace-analysis artifacts.

When available, workspace analysis now also reads Marvin's structured run trace and partial-success artifact, so failed or incomplete runs carry forward more than a single narrative summary.
If you add notes to Marvin's operator-feedback artifact before resuming, workspace analysis reads that too instead of pretending the machine was the only witness.
Workspace analysis can also emit a recommended operator-feedback artifact for the next resume cycle when the YAML change alone would not be enough.

If you do not want to write a second YAML just to investigate a previous run, use the direct CLI command instead:

```bash
uv run marvin analyze-workspace ./tokensmith-run
```

Optional flags for direct workspace analysis:

- `--config path/to/task.yaml` to provide the original task config explicitly.
- `--model openai:gpt-5.4` to choose the planner model used for analysis.
- `--non-interactive` to suppress clarification prompts.
- `--verbose-io` to expose underlying planner stdout/stderr.

When possible, Marvin reconstructs the original task settings from a source-config snapshot stored in the workspace. If that snapshot is missing, it falls back to a synthesized minimal config built from the workspace artifacts and discovered repos, which is less elegant but still preferable to requiring fresh YAML busywork.

## Core Concepts

### Workspace Containment

Marvin always runs inside a workspace directory. If you do not provide one, it creates a new workspace under the launch directory. If you do provide one, it must also live under the launch directory.

That constraint is deliberate. Marvin is pessimistic, not reckless.

### Planning Modes

Marvin supports two planning modes:

- `single`: one executable plan, stepped through in order.
- `hierarchical`: top-level plan steps plus generated sub-steps per main step.

Hierarchical mode produces better execution feedback and more precise resume points. It also spends more planner tokens, because naturally nuance is not free.

### Invocation Modes

Marvin supports these top-level `mode` values:

- `plan`: generate the proposal and plan artifacts, then stop.
- `execute`: execute against an existing proposal in the workspace.
- `plan_and_execute`: plan first, then execute.
- `analyze_workspace`: inspect an existing workspace, identify likely failure causes, recommend YAML updates for the next run, and suggest operator-feedback edits when the next resume needs better human guidance.

`analyze_workspace` requires an existing workspace path via `--workspace`, `workspace`, or `restart_workspace`. It does not materialize repos or execute code. It reads prior artifacts, repository state, and tracker data, then writes a markdown report plus JSON analysis payload.

### Artifacts

Marvin writes and updates these artifacts in the workspace:

- `plan/marvin.md`: the live execution tracker.
- `plan/step-*.md`: one file per executable plan step.
- `proposal.md` by default: the planner's markdown output.
- `artifacts/marvin_plan.json`: structured plan payload and planner metadata.
- `artifacts/marvin_executor_progress.json`: persisted execution state for resume.
- `artifacts/marvin_execution_summary.json`: final execution summary, including token rollups.
- `artifacts/marvin_partial_success.json`: structured learning artifact derived from the run trace, including completed steps, unresolved blockers, and suggested operator feedback for the next run.
- `artifacts/marvin_operator_feedback.md`: editable operator notes for the next repair or resume cycle, including `refresh_subplans: no|current|yes` to control how much hierarchical planning should be regenerated on resume.
- `artifacts/marvin_workspace_analysis.md`: workspace-analysis report for `mode: analyze_workspace`.
- `artifacts/marvin_workspace_analysis.json`: machine-readable workspace-analysis payload.
- `artifacts/marvin_recommended_config.yaml`: merged recommended config produced from the analysis YAML patch.
- `artifacts/marvin_recommended_operator_feedback.md`: analysis-suggested operator-feedback edits for the next repair or resume cycle.
- `artifacts/marvin_source_config.yaml`: snapshot of the original task config used to improve future workspace analysis.
- `checkpoints/*.db`: executor and planner checkpoint snapshots.

## Running Marvin

Basic run:

```bash
uv run marvin path/to/task.yaml
```

Useful run flags:

- `--tui` to use the Textual dashboard.
- `--workspace <path>` to reuse or create a specific workspace.
- `--resume` to require that the supplied workspace already exists.
- `--non-interactive` to disable the execution confirmation prompt for that run.
- `--resume-from <checkpoint>` to restore executor state from a specific snapshot.
- `--planning-mode single|hierarchical` to override the YAML value for one run.
- `--confirm-before-execute` to require confirmation before execution starts.
- `--no-resume-state` to ignore saved execution progress and start fresh.
- `--verbose-io` to capture and print underlying agent stdout and stderr.

The default run path is non-interactive once execution begins. That was intentional. Constant approval prompts are not a workflow. They are a hostage situation.

## Generating Configs with `marvin init`

`marvin init` creates a future Marvin YAML config from rough source material such as:

- a GitHub issue
- a feature description
- an architectural proposal
- some other regrettably under-specified input

Interactive mode:

```bash
uv run marvin init --interactive
```

Non-interactive draft generation:

```bash
uv run marvin init --source-file path/to/notes.md --output task.yaml
```

Useful init flags:

- `--source-file <path>` to seed generation from existing notes.
- `--output <path>` to choose the YAML output path.
- `--model <provider:model>` to select the config-generation model.
- `--interactive` to force the wizard even when enough inputs exist for auto-generation.
- `--force` to overwrite an existing output file.

`marvin init` is CLI-only. The TUI is for runs, not config authoring.

Auto-generation behavior:

- Marvin reads your source text.
- Marvin searches only GitHub pages under `github.com/OpenCHAMI` for supporting context.
- Marvin drafts a config and normalizes it back into Marvin's schema.
- If the draft is wrong, rerun with `--interactive` and correct it like adults.

Generated configs currently default to:

- `mode: plan_and_execute`
- `repos[].checkout: true`
- `execution.commit_each_step: true`
- `task.confirm_before_execute: true`
- `planning.mode: single`

## YAML Configuration

Marvin is driven by YAML. The minimum useful config needs a project name, a problem statement, at least one repo, and model selection.

Example:

```yaml
project: OpenCHAMI boot-service
problem: |
	Implement issue #6 in boot-service and validate the change with focused tests.

models:
	default: openai:gpt-5.4
	planner: openai:gpt-5.4
	executor: openai:gpt-5.4

planning:
	mode: hierarchical

task:
	execute_after_plan: true
	confirm_before_execute: true
	confirm_timeout_sec: 45
	deliverables:
		- Code changes in boot-service
		- Updated tests and documentation where relevant
	notes:
		- Stay within the workspace
		- Prefer focused validation commands
	plan_requirements:
		- Keep plan steps independently executable
	execution_requirements:
		- Run focused tests after each meaningful change

repos:
	- name: boot-service
		url: https://github.com/OpenCHAMI/boot-service.git
		branch: main
		checkout: true
		language: go
		description: Boot service repository
		checks:
			- go test ./...
	- name: fabrica
		url: https://github.com/OpenCHAMI/fabrica.git
		branch: main
		checkout: true
		read_only: true
		language: generic
		description: Reference-only Fabrica repository for generator workflow and source model context

execution:
	executor_agent: auto
	max_parallel_checks: 4
	max_check_retries: 1
	resume_execution_state: true
	commit_each_step: true
	repo_dependencies: {}
	repo_order: []

outputs:
	proposal_markdown: proposal.md
	plan_json: artifacts/marvin_plan.json
	executor_progress_json: artifacts/marvin_executor_progress.json
	summary_json: artifacts/marvin_execution_summary.json
	partial_success_json: artifacts/marvin_partial_success.json
	operator_feedback_markdown: artifacts/marvin_operator_feedback.md

agent:
	name: Marvin
	persona_instruction: |
		Keep the dry, skeptical tone, but stay operationally useful.
	prompt_appendix: |
		Prefer minimal diffs and focused validation output.
	planner_prompt_appendix: |
		Call out migration risks and rollback points.
	executor_prompt_appendix: |
		Favor small commits and narrow tests first.
	repair_prompt_appendix: |
		Explain the root cause before patching.
```

Workspace fields:

- `workspace`: explicit workspace path under the launch directory.
- `workspace_root`: base directory used when Marvin auto-generates a new workspace name. Defaults to `.`.
- `restart_workspace`: accepted alias for `workspace` during workspace resolution.

Marvin still enforces workspace containment. If a configured workspace escapes the launch directory, it refuses to proceed rather than wandering off to vandalize whatever happened to be nearby.

Important fields:

- `project`: human-readable task name.
- `problem`: the actual work Marvin is supposed to solve.
- `mode`: top-level run mode such as `plan`, `execute`, or `plan_and_execute`.
- `mode: analyze_workspace`: inspect a prior workspace and recommend YAML changes instead of executing code.
- `models.default|planner|executor`: planner and executor model selection.
- `planning.mode`: `single` or `hierarchical`.
- `workspace|workspace_root|restart_workspace`: workspace selection and auto-generation controls.
- `repos[]`: repository definitions, checkout behavior, and validation commands.
- `repos[].read_only` or `repos[].read-only`: marks a repository as reference-only context instead of an execution target.
- `task.*`: execution gating, plan requirements, deliverables, and notes.
- `execution.*`: runtime behavior such as retries, repo ordering, and step commits.
- `outputs.*`: artifact paths within the workspace.
- `outputs.workspace_analysis_markdown|workspace_analysis_json`: analysis artifact paths used by `mode: analyze_workspace`.
- `outputs.recommended_config_yaml`: full merged config artifact written by `mode: analyze_workspace`.
- `outputs.partial_success_json|operator_feedback_markdown`: learning and operator-feedback artifacts used by resume, repair, and workspace analysis.
- `agent.*`: prompt customization and persona controls.

### Resume Feedback Loop

After execution, Marvin writes two related artifacts:

- `marvin_partial_success.json`: structured machine-readable learning about what finished, what failed, and what likely needs clarification.
- `marvin_operator_feedback.md`: an editable note file seeded from that learning artifact.

If you update `marvin_operator_feedback.md` before resuming, Marvin reads those notes back into resumed execution and repair prompts. In hierarchical mode, setting `refresh_subplans: yes` tells Marvin to discard pending cached subplans and regenerate them for the remaining main steps on resume.

### Reference Repositories

Some tasks need additional repositories for context but should never edit them. Mark those repositories with `read_only: true` or `read-only: true`:

```yaml
repos:
	- name: service
		url: https://github.com/OpenCHAMI/example-service.git
		checkout: true
	- name: shared-docs
		url: https://github.com/OpenCHAMI/docs.git
		checkout: true
		read_only: true
```

Reference-only repositories are:

- materialized into the workspace so the planner and executor can inspect them.
- included in prompt context and labeled as `role=reference-only`.
- excluded from writable execution-repo selection, validation targeting, and step commits.
- treated as read-only context by the execution prompt.

In other words, Marvin can read them, cite them, and glower at them, but it should not modify them.

## Agent Customization

Marvin's persona is configurable without changing code.

Field behavior:

- `agent.name` changes how the agent identifies itself in prompts.
- `agent.persona_instruction` replaces the default Marvin persona block.
- `agent.prompt_appendix` is appended to all prompts.
- `agent.planner_prompt_appendix` affects only the planner.
- `agent.executor_prompt_appendix` affects only execution.
- `agent.repair_prompt_appendix` affects only repair passes.
- `agent.*_path` variants load appendix text from files relative to the YAML config.
- `repos[].brief` adds cached repository context to planner, executor, and repair prompts.
- `repos[].brief_path` loads that brief from a file relative to the YAML config.
- `repos[].read_only` or `repos[].read-only` marks a repository as reference-only context. In prompts this appears as `role=reference-only`.
- `execution.executor_agent` selects the URSA execution agent implementation.
- `execution.commit_each_step` controls whether Marvin tries to create a git commit after each completed step.

Example:

```yaml
agent:
	prompt_appendix_path: prompt-library/appendices/openchami-shared.md
	executor_prompt_appendix_path: prompt-library/appendices/fabrica-executor.md

repos:
	- name: fabrica
		url: https://github.com/OpenCHAMI/fabrica.git
		branch: main
		checkout: true
		language: generic
		description: Fabrica Go API code generation repository
		brief_path: prompt-library/briefs/fabrica.md
```

Use briefs for high-signal invariants, integration touchpoints, and change triggers. They work best as short, opinionated cache files, not mini-READMEs.

## Textual TUI

Run with `--tui` to get a live dashboard with:

- the current plan tracker
- commentary and timeline updates
- repository validation state
- git activity
- live token totals and per-update deltas
- stage-level token rollups and token hotspots after execution finishes

The TUI is deliberately more informative now. It still sounds like Marvin, because cheerfulness would only confuse people.

## Repository Layout

- `marvin.py`: compatibility wrapper entrypoint.
- `src/openchami_coding_agent/`: package source code.
- `tests/`: automated tests.
- `pyproject.toml`: packaging and tool configuration.
- `CHANGELOG.md`: notable release history.
- `CONTRIBUTING.md`: development and contribution guidance.

## Development

Install development dependencies:

```bash
uv sync --frozen --extra dev
```

Run the local checks:

```bash
uv run ruff check .
uv run mypy
uv run pytest
```

## CI

Pull requests run:

- `uv run ruff check .`
- `uv run mypy`
- `uv run pytest`
