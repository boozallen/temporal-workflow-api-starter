# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Simple unit test for API models
"""

from datetime import UTC, datetime

import pytest
from foundry_agent_core import _MAX_DICT_BYTES
from foundry_temporal.signals import HILSignalsMixin
from pydantic import ValidationError

from src.models import (
    ALLOWED_SIGNALS,
    WorkflowApproveRequest,
    WorkflowSignalRequest,
    WorkflowStartRequest,
    WorkflowStartResponse,
)


def test_workflow_start_request_validation():
    """Test WorkflowStartRequest model validation works"""

    # Valid request
    request = WorkflowStartRequest(
        workflow_type="TestWorkflow", input_data={"message": "test message"}
    )

    assert request.workflow_type == "TestWorkflow"
    assert request.input_data == {"message": "test message"}
    assert request.workflow_id is None  # Optional field
    assert request.task_queue is None  # Optional field

    # Invalid request - missing required field should fail
    with pytest.raises(ValidationError):
        WorkflowStartRequest(
            input_data={"test": "data"}
        )  # Missing required workflow_type


def test_workflow_start_response_creation():
    """Test WorkflowStartResponse model creation"""

    # Success response
    started_at = datetime.now(UTC)
    response = WorkflowStartResponse(
        workflow_id="workflow-123",
        run_id="run-456",
        task_queue="test-queue",
        started_at=started_at,
    )

    assert response.workflow_id == "workflow-123"
    assert response.run_id == "run-456"
    assert response.task_queue == "test-queue"
    assert response.started_at == started_at


def test_workflow_approve_request_validation():
    """Test WorkflowApproveRequest requires run_id, approver_id, and content_digest"""

    request = WorkflowApproveRequest(
        run_id="run-123",
        approver_id="jane.doe@example.com",
        content_digest="digest-abc",
    )
    assert request.run_id == "run-123"
    assert request.approver_id == "jane.doe@example.com"
    assert request.content_digest == "digest-abc"

    for missing_field in ("run_id", "approver_id", "content_digest"):
        fields = {
            "run_id": "run-123",
            "approver_id": "jane.doe@example.com",
            "content_digest": "digest-abc",
        }
        del fields[missing_field]
        with pytest.raises(ValidationError):
            WorkflowApproveRequest(**fields)


def test_start_request_identifiers_exceed_max_length():
    """Identifier fields over 256 characters are rejected"""
    for field in ("workflow_type", "workflow_id", "task_queue"):
        fields = {"workflow_type": "TestWorkflow", field: "a" * 257}
        with pytest.raises(ValidationError):
            WorkflowStartRequest(**fields)


def test_start_request_identifiers_empty_string():
    """Identifier fields reject empty strings"""
    for field in ("workflow_type", "workflow_id", "task_queue"):
        fields = {"workflow_type": "TestWorkflow", field: ""}
        with pytest.raises(ValidationError):
            WorkflowStartRequest(**fields)


def test_start_request_identifiers_at_boundary():
    """Identifier fields accept exactly 256 characters"""
    request = WorkflowStartRequest(
        workflow_type="a" * 256, workflow_id="b" * 256, task_queue="c" * 256
    )
    assert len(request.workflow_type) == 256
    assert len(request.workflow_id) == 256
    assert len(request.task_queue) == 256


def test_input_data_exceeds_max_keys():
    """input_data with more than 32 top-level keys is rejected"""
    with pytest.raises(ValidationError):
        WorkflowStartRequest(
            workflow_type="TestWorkflow",
            input_data={f"key_{i}": i for i in range(33)},
        )


def test_input_data_key_exceeds_max_length():
    """input_data keys over 128 characters are rejected"""
    with pytest.raises(ValidationError):
        WorkflowStartRequest(workflow_type="TestWorkflow", input_data={"k" * 129: 1})


def test_input_data_exceeds_max_bytes():
    """input_data serializing above the shared byte cap is rejected"""
    with pytest.raises(ValidationError):
        WorkflowStartRequest(
            workflow_type="TestWorkflow",
            input_data={"text": "a" * _MAX_DICT_BYTES},
        )


def test_input_data_at_boundary():
    """input_data with 32 keys and 128-character keys is accepted"""
    input_data = {f"key_{i}": i for i in range(31)}
    input_data["k" * 128] = 1
    request = WorkflowStartRequest(workflow_type="TestWorkflow", input_data=input_data)
    assert len(request.input_data) == 32


def test_signal_request_bounds():
    """Signal name over 64 characters and comments over 65536 are rejected"""
    with pytest.raises(ValidationError):
        WorkflowSignalRequest(signal_name="a" * 65, approver_id="jane.doe@example.com")
    with pytest.raises(ValidationError):
        WorkflowSignalRequest(
            signal_name="provide_feedback",
            approver_id="jane.doe@example.com",
            comments="a" * 65_537,
        )

    request = WorkflowSignalRequest(
        signal_name="a" * 64,
        approver_id="jane.doe@example.com",
        comments="a" * 65_536,
    )
    assert len(request.signal_name) == 64
    assert len(request.comments) == 65_536


def test_signal_request_requires_approver_id():
    """Every signal request requires a non-empty approver_id"""
    with pytest.raises(ValidationError):
        WorkflowSignalRequest(signal_name="cancel_workflow")


def test_signal_request_feedback_requires_comments():
    """provide_feedback and provide_plan_feedback require non-empty comments"""
    for signal_name in ("provide_feedback", "provide_plan_feedback"):
        with pytest.raises(ValidationError):
            WorkflowSignalRequest(
                signal_name=signal_name, approver_id="jane.doe@example.com"
            )

        request = WorkflowSignalRequest(
            signal_name=signal_name,
            approver_id="jane.doe@example.com",
            comments="looks good",
        )
        assert request.build_signal_args("jane.doe@example.com") == [
            "jane.doe@example.com",
            "looks good",
        ]


def test_signal_request_cancel_workflow_rejects_comments():
    """cancel_workflow does not accept comments"""
    with pytest.raises(ValidationError):
        WorkflowSignalRequest(
            signal_name="cancel_workflow",
            approver_id="jane.doe@example.com",
            comments="not allowed",
        )

    request = WorkflowSignalRequest(
        signal_name="cancel_workflow", approver_id="jane.doe@example.com"
    )
    assert request.build_signal_args("jane.doe@example.com") == ["jane.doe@example.com"]


def test_approve_request_lane_defaults_to_general():
    """WorkflowApproveRequest's lane defaults to 'general'"""
    request = WorkflowApproveRequest(
        run_id="run-123",
        approver_id="jane.doe@example.com",
        content_digest="digest-abc",
    )
    assert request.lane == "general"


def test_approve_request_accepts_plan_and_research_lanes():
    """WorkflowApproveRequest accepts the plan and research lanes"""
    for lane in ("plan", "research"):
        request = WorkflowApproveRequest(
            run_id="run-123",
            approver_id="jane.doe@example.com",
            content_digest="digest-abc",
            lane=lane,
        )
        assert request.lane == lane


def test_allowed_signals_match_hilsignalsmixin_real_handlers():
    """Every ALLOWED_SIGNALS entry, dispatched to the real HILSignalsMixin
    handler (not a hand-written stand-in), produces that handler's
    documented state change.

    This calls the actual foundry_temporal.signals.HILSignalsMixin methods
    directly, so a future foundry-temporal upgrade that changes a handler's
    argument count or behavior fails this test - instead of silently
    stalling a real workflow, which is the bug this test
    guards against.
    """
    assert {
        "provide_feedback",
        "provide_plan_feedback",
        "cancel_workflow",
    } == ALLOWED_SIGNALS
    assert "approve" not in ALLOWED_SIGNALS
    assert "approve_plan" not in ALLOWED_SIGNALS
    assert "approve_research" not in ALLOWED_SIGNALS

    approver_id = "jane.doe@example.com"

    instance = HILSignalsMixin()
    request = WorkflowSignalRequest(
        signal_name="provide_feedback",
        approver_id=approver_id,
        comments="fix the introduction",
    )
    instance.provide_feedback(*request.build_signal_args(approver_id))
    assert instance.document_feedback_received is True
    assert instance.document_feedback_comments == "fix the introduction"

    instance = HILSignalsMixin()
    request = WorkflowSignalRequest(
        signal_name="provide_plan_feedback",
        approver_id=approver_id,
        comments="revise the plan",
    )
    instance.provide_plan_feedback(*request.build_signal_args(approver_id))
    assert instance.plan_feedback_received is True
    assert instance.plan_feedback_comments == "revise the plan"

    instance = HILSignalsMixin()
    request = WorkflowSignalRequest(
        signal_name="cancel_workflow", approver_id=approver_id
    )
    instance.cancel_workflow(*request.build_signal_args(approver_id))
    assert instance.cancelled is True
    assert instance.cancelled_by == approver_id


def test_wrong_arg_count_is_a_silent_no_op_on_the_real_handler():
    """Proves why the argument count matters: HILSignalsMixin's handlers
    never raise on a bad call - they silently no-op, which is exactly how
    That bug could stall a workflow without any visible error. Calling
    cancel_workflow with the OLD generic-signal shape (two args, matching
    the no-longer-possible signal_data era) must not record a cancellation.
    """
    instance = HILSignalsMixin()
    instance.cancel_workflow("jane.doe@example.com", "unexpected extra arg")
    assert instance.cancelled is False
    assert instance.cancelled_by is None


def test_approve_request_rejects_unknown_lane():
    """WorkflowApproveRequest rejects a lane outside general/plan/research"""
    with pytest.raises(ValidationError):
        WorkflowApproveRequest(
            run_id="run-123",
            approver_id="jane.doe@example.com",
            content_digest="digest-abc",
            lane="unknown",
        )


def test_approve_request_bounds():
    """Approve fields over their limits are rejected"""
    valid = {
        "run_id": "run-123",
        "approver_id": "jane.doe@example.com",
        "content_digest": "digest-abc",
    }
    for field, length in (
        ("run_id", 257),
        ("approver_id", 257),
        ("content_digest", 129),
    ):
        with pytest.raises(ValidationError):
            WorkflowApproveRequest(**{**valid, field: "a" * length})

    request = WorkflowApproveRequest(
        run_id="a" * 256, approver_id="b" * 256, content_digest="c" * 128
    )
    assert len(request.run_id) == 256
    assert len(request.content_digest) == 128
