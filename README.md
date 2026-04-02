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

## Core Concepts

### Workspace Containment

Marvin always runs inside a workspace directory. If you do not provide one, it creates a new workspace under the launch directory. If you do provide one, it must also live under the launch directory.

That constraint is deliberate. Marvin is pessimistic, not reckless.

### Planning Modes

Marvin supports two planning modes:

- `single`: one executable plan, stepped through in order.
- `hierarchical`: top-level plan steps plus generated sub-steps per main step.

Hierarchical mode produces better execution feedback and more precise resume points. It also spends more planner tokens, because naturally nuance is not free.

### Artifacts

Marvin writes and updates these artifacts in the workspace:

- `plan/marvin.md`: the live execution tracker.
- `plan/step-*.md`: one file per executable plan step.
- `proposal.md` by default: the planner's markdown output.
- `artifacts/marvin_plan.json`: structured plan payload and planner metadata.
- `artifacts/marvin_executor_progress.json`: persisted execution state for resume.
- `artifacts/marvin_execution_summary.json`: final execution summary, including token rollups.
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

Important fields:

- `project`: human-readable task name.
- `problem`: the actual work Marvin is supposed to solve.
- `models.default|planner|executor`: planner and executor model selection.
- `planning.mode`: `single` or `hierarchical`.
- `repos[]`: repository definitions, checkout behavior, and validation commands.
- `task.*`: execution gating, plan requirements, deliverables, and notes.
- `execution.*`: runtime behavior such as retries, repo ordering, and step commits.
- `outputs.*`: artifact paths within the workspace.
- `agent.*`: prompt customization and persona controls.

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
