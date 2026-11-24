# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
TEMPLATE: API models with foundry-temporal integration

Customize these models for your specific workflow requirements.
Uses foundry-temporal error types for consistent error handling.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from foundry_agent_core.types import _validate_bounded_dict
from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Check foundry-temporal availability for consistent error handling patterns
# foundry-temporal is REQUIRED - no availability check needed


class WorkflowStatus(StrEnum):
    """Workflow execution status."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TERMINATED = "terminated"
    CONTINUED_AS_NEW = "continued_as_new"
    TIMED_OUT = "timed_out"


# Input bounds (STIG V-222612 / CCI-002824): every user-controlled request field
# has a size limit, so oversized values fail validation with HTTP 422.
# Identifier cap of 256 is a placeholder; adjust per expected ID length.
MAX_IDENTIFIER_LEN = 256
# Digest cap of 128 is a placeholder; fits a hex SHA-512, adjust per digest algorithm.
MAX_DIGEST_LEN = 128
# Handler-name cap of 64 is a placeholder; adjust per signal/query handler names.
MAX_HANDLER_NAME_LEN = 64
# Signal-data cap of 65536 is a placeholder; adjust per expected feedback length.
MAX_SIGNAL_DATA_LEN = 65_536
# input_data is checked by foundry-agent-core's own validator and shared caps
# (_MAX_DICT_KEYS, _MAX_DICT_KEY_LEN, _MAX_DICT_BYTES). They are placeholders;
# adjust per expected payload size in foundry-agent-core, and the change
# takes effect here too.

BoundedIdentifier = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_IDENTIFIER_LEN)
]
BoundedDigest = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_DIGEST_LEN)
]
BoundedHandlerName = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_HANDLER_NAME_LEN)
]


# Request Models


class WorkflowStartRequest(BaseModel):
    """TEMPLATE: Request to start a workflow - customize fields as needed."""

    model_config = {
        "json_schema_extra": {
            "example": {
                "workflow_type": "MyWorkflow",
                "workflow_id": "custom-workflow-123",
                "task_queue": "workflow-task-queue",
                "input_data": {
                    "user_name": "PostmanUser",
                    "task_type": "greeting",
                    "priority": 3,
                    "language": "en",
                    "metadata": {
                        "source": "swagger-docs",
                        "timestamp": "2026-02-05T12:00:00Z",
                    },
                },
            }
        }
    }

    workflow_type: BoundedIdentifier = Field(
        ...,
        description="Type of workflow to start",
        json_schema_extra={"example": "MyWorkflow"},
    )
    workflow_id: BoundedIdentifier | None = Field(
        None, description="Optional workflow ID (auto-generated if not provided)"
    )
    task_queue: BoundedIdentifier | None = Field(
        None,
        description="Task queue to execute workflow on (uses default if not provided)",
    )
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Input data for the workflow",
        json_schema_extra={
            "example": {
                "user_name": "PostmanUser",
                "task_type": "greeting",
                "priority": 3,
                "language": "en",
                "metadata": {
                    "source": "swagger-docs",
                    "timestamp": "2026-02-05T12:00:00Z",
                },
            }
        },
    )

    @field_validator("input_data")
    @classmethod
    def _validate_input_data_size(cls, value: dict[str, Any]) -> dict[str, Any] | None:
        """Reject input_data with too many keys, overlong keys, or too many bytes."""
        return _validate_bounded_dict(value)


# CUSTOMIZE: Signal names the generic /signal endpoint will relay to a
# managed workflow, and whether each requires a `comments` value. These
# match the feedback/cancel signal handlers on HILSignalsMixin in
# foundry-temporal, each of which requires an approver_id; add your own
# workflow's signal names here if it defines additional signals.
#
# approve, approve_plan and approve_research are deliberately absent: those
# go only through the run-bound, digest-checked /approve endpoint (see
# WorkflowApproveRequest's `lane` field), never through this generic route.
SIGNAL_REQUIRES_COMMENTS: dict[str, bool] = {
    "provide_feedback": True,
    "provide_plan_feedback": True,
    "cancel_workflow": False,
}
ALLOWED_SIGNALS = frozenset(SIGNAL_REQUIRES_COMMENTS)


class WorkflowSignalRequest(BaseModel):
    """Request to send a signal. Every allowed signal requires approver_id;
    provide_feedback and provide_plan_feedback also require comments."""

    signal_name: BoundedHandlerName = Field(
        ...,
        description=(
            "Name of the signal to send. Must be one of the gateway's "
            "allowed signals - see ALLOWED_SIGNALS."
        ),
        json_schema_extra={"example": "provide_feedback"},
    )
    approver_id: BoundedIdentifier = Field(
        ...,
        description="Identity of the caller sending this signal",
        json_schema_extra={"example": "jane.doe@example.com"},
    )
    comments: str | None = Field(
        None,
        max_length=MAX_SIGNAL_DATA_LEN,
        description=(
            "Feedback comments. Required for provide_feedback and "
            "provide_plan_feedback; not accepted for cancel_workflow."
        ),
        json_schema_extra={"example": "fix the introduction section"},
    )

    @model_validator(mode="after")
    def _validate_comments_for_signal(self) -> Self:
        """Enforce per-signal comments requirement from SIGNAL_REQUIRES_COMMENTS.

        An unrecognized signal_name is left to the router's ALLOWED_SIGNALS
        check, which reports the full allowed list - nothing further to
        validate here.
        """
        requires_comments = SIGNAL_REQUIRES_COMMENTS.get(self.signal_name)
        if requires_comments is None:
            return self
        if requires_comments and not self.comments:
            raise ValueError(f"signal '{self.signal_name}' requires non-empty comments")
        if not requires_comments and self.comments is not None:
            raise ValueError(f"signal '{self.signal_name}' does not accept comments")
        return self

    def build_signal_args(self, approver_id: str) -> list[Any]:
        """Build the positional args the target HILSignalsMixin handler expects.

        Takes approver_id as a parameter (rather than using self.approver_id)
        so the caller can supply the identity resolved by get_approver_identity,
        which may differ from the request body in a customized deployment.
        """
        if SIGNAL_REQUIRES_COMMENTS.get(self.signal_name):
            return [approver_id, self.comments]
        return [approver_id]


class WorkflowApproveRequest(BaseModel):
    """Request to approve a specific run that is awaiting review."""

    run_id: BoundedIdentifier = Field(
        ...,
        description="Run ID of the awaiting-review run to approve",
        json_schema_extra={"example": "019c2fd0-2bae-7420-942f-e37c8e83b1ce"},
    )
    approver_id: BoundedIdentifier = Field(
        ...,
        description="Identity of the approver, recorded on the workflow",
        json_schema_extra={"example": "jane.doe@example.com"},
    )
    content_digest: BoundedDigest = Field(
        ...,
        description="Digest of the content the approver reviewed",
        json_schema_extra={"example": "3f786850e387550fdab836ed7e6dc881de23001b"},
    )
    lane: Literal["general", "plan", "research"] = Field(
        "general",
        description=(
            "Review lane to approve. 'general' (default) uses the approve "
            "signal; 'plan' and 'research' use the plan/research review "
            "lanes, each with their own awaiting-review state and "
            "approved-by field."
        ),
        json_schema_extra={"example": "general"},
    )


# Response Models


class WorkflowStartResponse(BaseModel):
    """Response when starting a workflow."""

    workflow_id: str = Field(
        ...,
        description="ID of the started workflow",
        json_schema_extra={"example": "MyWorkflow-abc12345"},
    )
    run_id: str = Field(
        ...,
        description="Run ID of the workflow execution",
        json_schema_extra={"example": "019c2fd0-2bae-7420-942f-e37c8e83b1ce"},
    )
    task_queue: str = Field(
        ...,
        description="Task queue the workflow is running on",
        json_schema_extra={"example": "workflow-task-queue"},
    )
    started_at: datetime = Field(
        ...,
        description="When the workflow was started",
        json_schema_extra={"example": "2026-02-05T21:58:17.779166Z"},
    )


class WorkflowStatusResponse(BaseModel):
    """Response with workflow status information."""

    workflow_id: str = Field(..., description="Workflow ID")
    run_id: str = Field(..., description="Current run ID")
    workflow_type: str = Field(..., description="Type of workflow")
    task_queue: str = Field(..., description="Task queue")
    status: WorkflowStatus = Field(..., description="Current workflow status")
    started_at: datetime = Field(..., description="When the workflow started")
    completed_at: datetime | None = Field(
        None, description="When the workflow completed"
    )
    result: dict[str, Any] | None = Field(
        None, description="Workflow result if completed"
    )
    error: str | None = Field(None, description="Error message if failed")
    history_length: int = Field(..., description="Number of history events")
    pending_activities: list[dict[str, Any]] = Field(
        default_factory=list, description="Currently pending activities"
    )


class HealthCheckResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Health status")
    service: str = Field(..., description="Service name")
    timestamp: datetime = Field(..., description="Check timestamp")
    version: str = Field(..., description="API version")
    dependencies: dict[str, Any] | None = Field(
        None, description="Status of dependencies"
    )


# Common API Models (simplified aliases)


class WorkflowRequest(WorkflowStartRequest):
    """TEMPLATE: General workflow request - alias for WorkflowStartRequest."""

    pass


class WorkflowResponse(WorkflowStartResponse):
    """TEMPLATE: General workflow response - alias for WorkflowStartResponse."""

    pass


class ErrorResponse(BaseModel):
    """Standard error response model - compatible with foundry-temporal error types."""

    error: str = Field(..., description="Error message")
    error_code: str | None = Field(None, description="Error code")
    error_type: str | None = Field(
        None, description="Foundry error type (WorkflowError, ValidationError, etc.)"
    )
    detail: dict[str, Any] | None = Field(None, description="Additional error details")
    timestamp: datetime = Field(
        default_factory=datetime.now, description="Error timestamp"
    )
    retryable: bool | None = Field(None, description="Whether this error is retryable")
