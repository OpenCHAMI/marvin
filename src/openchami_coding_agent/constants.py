"""Shared constants for the OpenCHAMI coding agent."""

AGENT_NAME = "Marvin"
AGENT_PERSONA_INSTRUCTION = (
    "Adopt a dry, deadpan, mildly existential tone while staying precise, practical, and helpful. "
    "Be candid about uncertainty, prefer concise actionable output, and never let "
    "personality obscure instructions, safety constraints, or technical accuracy."
)

DEFAULT_CONTEXT_CLAIM = "project_accounting_context"
DEFAULT_PROPOSAL_MD = "docs/tokensmith_feature_proposal.md"
DEFAULT_PLAN_JSON = "artifacts/openchami_plan.json"
DEFAULT_SUMMARY_JSON = "artifacts/openchami_execution_summary.json"
DEFAULT_WORKSPACE_ROOT = "."
DEFAULT_EXEC_PROGRESS_JSON = "artifacts/openchami_executor_progress.json"
