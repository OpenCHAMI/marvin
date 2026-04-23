# Task Brief: Improve Marvin's Coding Framework

## Objective

Upgrade `marvin` into a more modular, reliable, coding-agent-friendly framework for OpenCHAMI development by introducing explicit workflow phases, a parallel verifier layer, modular prompt assets, and a more stable interface to URSA.

This brief is designed to be given directly to any capable coding agent.

---

## Background

Marvin already has several strengths that should be preserved:

- workspace-based execution
- multi-repo support
- writable vs reference-only repo semantics
- checkpointing and resume
- validation and repair loops
- run artifacts and post-run analysis

The main opportunity is to separate concerns more cleanly and make verification more rigorous and extensible.

The desired workflow model is:

**Explore -> Plan -> Execute -> Verify -> Summarize/Learn**

Where:

- **Explore** is read-only codebase discovery and context gathering
- **Plan** is read-only task decomposition and implementation design
- **Execute** is the only source-mutating phase
- **Verify** is a parallelizable fan-out layer of read-only or source-non-mutating verification agents
- **Summarize/Learn** records outcomes, failures, lessons, and recommended future config improvements

Security review should not be treated as its own top-level phase. It should be implemented as one verifier among many.

---

## High-Level Goals

1. Introduce explicit workflow phase boundaries.
2. Replace single linear validation with a verifier mesh.
3. Make verification results structured, machine-readable, and parallelizable.
4. Externalize prompt content into versioned prompt modules.
5. Stabilize the Marvin-to-URSA integration boundary.
6. Add repo-aware policy so Marvin can better support OpenCHAMI repositories.
7. Preserve existing Marvin strengths: workspaces, artifacts, checkpoints, resume, and multi-repo operation.

---

## Required Architectural Changes

### 1. Explicit workflow phases

Refactor the orchestration flow so the primary lifecycle is:

1. **Explore**
2. **Plan**
3. **Execute**
4. **Verify**
5. **Summarize/Learn**

Requirements:

- Explore and Plan must be hard read-only.
- Execute must be the only phase allowed to modify source files.
- Verify must consume an immutable execution handoff bundle.
- Summarize/Learn must persist artifacts that help improve future runs.

### 2. Verification as a parallel fan-out stage

Implement `Verify` as a registry of independent verifier agents, not as a single monolithic step.

Each verifier should:

- receive the same verification bundle
- produce a structured result
- be marked as required, advisory, or conditional
- declare whether it can run in parallel
- be non-mutating with respect to the source tree under verification

At minimum, support these verifier families:

- build/test verifier
- API/contract verifier
- integration verifier
- idempotence/resume verifier
- security verifier
- static-analysis verifier
- packaging/deployment verifier
- docs/operator-impact verifier

### 3. Structured verification data model

Add a verification bundle and verification result schema.

Suggested model:

```python
class VerificationBundle(TypedDict):
    workspace_path: str
    repo_states: dict[str, str]
    plan_path: str | None
    execution_summary_path: str | None
    changed_files: list[str]
    diff_path: str | None
    run_trace_path: str | None
    repo_profile_paths: list[str]
    artifact_dir: str

class VerifierResult(TypedDict):
    name: str
    verdict: Literal["PASS", "FAIL", "PARTIAL"]
    required: bool
    tier: int
    scope: str
    evidence: list[str]
    findings: list[str]
    artifacts: list[str]
    rerun_recommended: bool
```

### 4. Tiered verification execution

Implement verifier scheduling in tiers.

#### Tier 1: cheap blockers
Run first and fail fast.
Examples:

- build
- unit tests
- lint/type/vet
- config/schema checks
- focused contract checks

#### Tier 2: medium-cost verifiers
Run in parallel once Tier 1 passes.
Examples:

- integration tests
- security review
- packaging checks
- docs/operator checks

#### Tier 3: expensive or optional verifiers
Run only when configured or triggered.
Examples:

- performance smoke tests
- upgrade/downgrade compatibility
- large-system simulations
- hardware-emulation scenarios

### 5. Prompt modularization

Move prompt and policy text out of a single Python prompt-construction blob and into external, versionable prompt assets.

Create a prompt library structure such as:

```text
prompt_library/
  explore.md
  plan.md
  execute.md
  repair.md
  verify.md
  summarize.md
  reminders/
    tool_use.md
    validation_rules.md
    repo_hints.md
```

Requirements:

- Python should assemble prompts from modular assets.
- Prompt assets should be easy to diff, test, and override.
- Each phase should have its own prompt contract.

### 6. Stable URSA adapter boundary

Reduce direct compatibility probing and historical import fallback logic.

Define narrow protocol-style interfaces such as:

- `PlanningAgentProtocol`
- `ExecutionAgentProtocol`
- `VerifierAgentProtocol`
- `ModelFactoryProtocol`
- `CheckpointStoreProtocol`

Then create a single URSA adapter layer implementing those interfaces.

Requirements:

- Marvin should not need to know multiple historical URSA import paths.
- URSA integration should be testable through adapter contract tests.
- The boundary should be simple to mock in unit tests.

### 7. Repo profiles for OpenCHAMI

Add data-driven repo profiles so planning, execution, repair, and verification can use shared repo intelligence.

Each profile should be able to declare:

- language/toolchain
- canonical validation commands
- smoke tests
- packaging/build commands
- dangerous operations
- critical files
- service boundaries
- related repos
- protected paths
- operator-sensitive behavior
- resume/idempotence expectations

Store these profiles in a versioned, declarative format.

---

## Suggested Deliverables

The implementation should include the following deliverables.

### A. New orchestration flow

Add or refactor orchestration code so the new phase model is explicit in code and in artifacts.

Expected outcome:

- phases are represented as first-class concepts
- phase transitions are visible in logs and artifacts
- permissions and expectations differ by phase

### B. Verifier framework

Implement:

- verifier base interface
- verifier registry
- verification bundle creation
- parallel verifier execution
- verifier result aggregation
- required/advisory/conditional gating

Expected outcome:

- multiple verifier agents can run independently
- results are structured and persisted
- required failures block success

### C. Aggregated verification report

Produce a machine-readable and human-readable aggregate report with:

- per-verifier status
- summary verdict
- required failures
- evidence pointers
- recommended targeted repair scope

Expected outcome:

- repair can become surgical rather than generic
- operator can quickly understand what failed and why

### D. Targeted repair loop

When verification fails, repair should be scoped to the failing verifier outputs.

Expected outcome:

- repair instructions reference specific failed verifiers
- successful verifiers are not re-litigated unnecessarily
- repair loops become narrower and more deterministic

### E. Prompt asset refactor

Move prompt content into versioned prompt files and keep only interpolation/assembly logic in Python.

Expected outcome:

- easier prompt review and iteration
- easier repo-specific appendices
- clearer separation of code and policy text

### F. Repo profile system

Create initial profile support and populate it for core OpenCHAMI-targeted repositories.

Expected outcome:

- verification and planning become repo-aware
- commands and protected paths are not hardcoded ad hoc

---

## Proposed Implementation Plan

### Phase 1: Foundation

1. Add the phase model to orchestration.
2. Define `VerificationBundle` and `VerifierResult`.
3. Implement verifier base interface and aggregator.
4. Add one required verifier and one advisory verifier as proofs of concept.
5. Persist verification artifacts.

Recommended first verifiers:

- build/test verifier
- security verifier

### Phase 2: Tiered and parallel verification

1. Add verifier tiers.
2. Add parallel execution for compatible verifiers.
3. Add required/advisory/conditional policies.
4. Add targeted repair input generation from failed verifiers.

### Phase 3: Prompt system refactor

1. Extract existing prompt content into `prompt_library/`.
2. Split prompt contracts by phase.
3. Add prompt-loading and composition logic.
4. Preserve backward compatibility where practical.

### Phase 4: URSA boundary cleanup

1. Introduce protocol-style internal interfaces.
2. Build a single URSA adapter.
3. Remove or reduce compatibility probing outside the adapter.
4. Add tests for the adapter boundary.

### Phase 5: OpenCHAMI specialization

1. Add repo profiles.
2. Add repo-aware verifier selection and policy.
3. Add repo-aware critical file discovery.
4. Add idempotence/resume-focused verification for workflows relevant to OpenCHAMI.

---

## Acceptance Criteria

The task is complete when all of the following are true.

### Workflow and phase boundaries

- Marvin visibly uses the phases Explore, Plan, Execute, Verify, Summarize/Learn.
- Explore and Plan do not modify source repos.
- Execute is the only source-mutating phase.
- Verify operates on a frozen handoff bundle.

### Verifier system

- Marvin can run multiple verifiers from a registry.
- At least some verifiers can run in parallel.
- Verifier results are structured and persisted.
- A final aggregate verdict is produced.
- Required verifier failures block overall success.

### Repair behavior

- Repair receives targeted input derived from failing verifier results.
- Repair scope is narrower than a generic rerun.
- Post-repair verification can be rerun selectively.

### Prompt modularity

- Prompt assets live in versioned external files.
- Each phase has its own prompt contract.
- Prompt assembly logic is separate from prompt content.

### URSA boundary

- Marvin integrates with URSA through a stable adapter layer.
- Direct compatibility probing is substantially reduced.
- The adapter boundary is unit-testable.

### Repo awareness

- Repo profiles exist and can influence planning, execution, and verification.
- At least one OpenCHAMI-relevant repo profile is implemented and exercised.

---

## Constraints and Guardrails

- Preserve existing workspace, checkpoint, resume, and artifact behavior unless a change is clearly justified.
- Do not degrade support for multi-repo operation.
- Keep backward compatibility where practical, but prefer architectural clarity over fragile legacy behavior.
- Avoid making verification agents mutate the source tree they verify.
- Make any verifier side effects occur only in scratch space, temp directories, logs, or isolated runtime environments.
- Prefer declarative policy over hardcoded per-repo behavior.
- Keep the design usable by agents, humans, and CI.

---

## Suggested Files/Areas Likely to Change

The exact file paths may differ depending on the current repo state, but likely areas include:

- orchestration / workflow entrypoints
- planning logic
- execution logic
- validation / repair logic
- prompt construction and prompt asset loading
- configuration schema and YAML parsing
- artifact persistence
- URSA compatibility / adapter code
- tests for orchestration, verification, and adapters

---

## Recommended First PR Shape

To minimize risk, do not attempt the entire redesign in one change.

A strong first PR would:

1. introduce the phase model
2. add a minimal verifier interface
3. add a build/test verifier
4. add an aggregate verification report
5. keep existing validation behavior as a compatibility fallback

A strong second PR would:

1. add parallel verifier execution
2. add a security verifier
3. add targeted repair input
4. extract prompt assets

A strong third PR would:

1. add repo profiles
2. add repo-aware verifier policy
3. clean up the URSA boundary

---

## Definition of Success

Marvin should emerge from this work as:

- easier to reason about
- safer to extend
- more reliable for coding tasks
- better suited to OpenCHAMI’s multi-repo engineering model
- more compatible with independent coding-agent roles
- able to verify changes through multiple specialized read-only agents in parallel

---

## Instructions to the Coding Agent

Implement this work incrementally. Prefer small, reviewable commits. Preserve existing behavior where possible, but do not keep unclear abstractions just for compatibility. Favor explicit contracts, structured artifacts, modular prompts, and verifier-driven repair.

Before making major structural changes:

- inspect the current Marvin architecture carefully
- identify the current orchestration and validation entrypoints
- map existing prompt assembly
- identify current URSA integration seams
- preserve current tests where possible and add new ones for each architectural change

When done, provide:

1. a summary of design changes
2. a list of new abstractions introduced
3. a migration/compatibility note
4. a list of follow-on improvements not completed in the first implementation pass

