# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
TEMPLATE: Basic workflow endpoints

Customize these endpoints for your specific workflow requirements.
"""

import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from temporalio.client import (
    Client,
    WorkflowExecutionDescription,
    WorkflowExecutionStatus,
    WorkflowHandle,
)
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from ..config import AppConfig, get_config
from ..dependencies import (
    evict_on_connection_failure,
    get_temporal_client,
)
from ..models import (
    ALLOWED_SIGNALS,
    MAX_HANDLER_NAME_LEN,
    MAX_IDENTIFIER_LEN,
    WorkflowApproveRequest,
    WorkflowSignalRequest,
    WorkflowStartRequest,
    WorkflowStartResponse,
    WorkflowStatus,
    WorkflowStatusResponse,
)

router = APIRouter()

# Path workflow IDs share the request models' identifier bound (STIG V-222612).
WorkflowIdPath = Annotated[
    str,
    Path(min_length=1, max_length=MAX_IDENTIFIER_LEN, description="Workflow ID"),
]


def get_available_workflows() -> dict[str, str]:
    """
    Get available workflow type names for validation.

    CUSTOMIZE: Replace this function to register your workflow type names.

    This API gateway doesn't need workflow class imports - just the type names
    to validate requests and pass to Temporal server. The actual workflows
    are implemented and executed in separate worker services.

    Teams should list their workflow type names that match their worker services.
    """
    # TEMPLATE: Workflow type names from temporal-workflow-worker-starter template
    # This name matches MyWorkflow in the worker starter template
    # Teams should replace with their actual workflow type names
    workflow_types = {
        "MyWorkflow",
    }

    # TEMPLATE: Add your workflow type names here
    # Example:
    # workflow_types = {
    #     "ProcessOrderWorkflow",
    #     "SendEmailWorkflow",
    #     "DataProcessingWorkflow",
    # }

    # Convert to dict for backwards compatibility with existing code
    return {name: name for name in workflow_types}


# Available workflows - populated dynamically
AVAILABLE_WORKFLOWS = get_available_workflows()


def get_allowed_task_queues(config: AppConfig) -> set[str]:
    """
    Get the set of task queues this gateway is permitted to dispatch to.

    CUSTOMIZE: Add additional queue names here if your deployment intentionally
    routes to multiple worker pools. The default restricts callers to the
    server-configured queue so that an unauthenticated request cannot steer
    a workflow onto another team's worker pool.
    """
    return {config.temporal_task_queue}


# CUSTOMIZE: Query handler names the gateway will forward to a managed
# workflow. This matches the query handler on QueryMixin in foundry-temporal;
# add your own workflow's query names here if it defines additional queries.
ALLOWED_QUERIES = {
    "get_status",
}

# CUSTOMIZE: Maps each /approve "lane" to its HILSignalsMixin signal name,
# the current_phase value the workflow sets while that lane is open for
# review, and the get_status() field recording who approved it. The
# awaiting-review phase strings are a template convention - current_phase is
# set by the concrete workflow, not by foundry-temporal - so adjust them to
# match your own workflow's phase names. "general" matches the existing
# single-lane behavior; add more lanes here if your workflow defines them.
APPROVAL_LANES: dict[str, dict[str, str]] = {
    "general": {
        "signal_name": "approve",
        "awaiting_phase": "awaiting_review",
        "approved_by_field": "approved_by",
    },
    "plan": {
        "signal_name": "approve_plan",
        "awaiting_phase": "awaiting_plan_review",
        "approved_by_field": "plan_approved_by",
    },
    "research": {
        "signal_name": "approve_research",
        "awaiting_phase": "awaiting_research_review",
        "approved_by_field": "document_approved_by",
    },
}


async def get_approver_identity(request: Request) -> str:
    """CUSTOMIZE: Resolve the caller's identity for audit/attribution.

    Default (template): caller-asserted. Reads `approver_id` from the JSON
    request body, since the template only authenticates callers with one
    shared bearer token and has no per-caller identity of its own. Replace
    this with your own identity provider (OIDC claims, a foundry-auth
    session, a header set by a trusted proxy, etc.) so the recorded approver
    reflects who actually approved, not merely what the request body claims.
    """
    try:
        body = await request.json()
    except ValueError:
        # Empty or malformed body - the bound Pydantic model (or lack of one)
        # reports the specific validation error; this just can't find an
        # approver_id in it.
        body = None
    approver_id = body.get("approver_id") if isinstance(body, dict) else None
    if not isinstance(approver_id, str) or not approver_id:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_CONTENT,
            detail="approver_id is required",
        )
    return approver_id


def _raise_if_temporal_unavailable(client: Client, error: RPCError) -> None:
    """Evict a broken client and answer 503, not a misleading 404, 500 or 200."""
    if evict_on_connection_failure(client, error):
        from foundry_temporal.logging import get_logger

        get_logger().bind(component="api.workflows").error(
            f"Temporal unavailable, cached client evicted: {error}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to connect to Temporal server",
        ) from error


async def _get_managed_description(
    handle: WorkflowHandle, config: AppConfig
) -> WorkflowExecutionDescription:
    """
    Describe a workflow handle and enforce the gateway's managed scope.

    Raises HTTP 404 if the run's workflow type or task queue is outside the
    gateway's managed set, so a caller cannot read or act on a workflow
    belonging to another team's worker pool through this gateway.
    """
    description = await handle.describe()
    if (
        description.workflow_type not in AVAILABLE_WORKFLOWS
        or description.task_queue not in get_allowed_task_queues(config)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {handle.id}",
        )
    return description


def _map_temporal_status(temporal_status: WorkflowExecutionStatus) -> WorkflowStatus:
    """Map Temporal workflow status to our API status enum."""
    status_mapping = {
        "RUNNING": WorkflowStatus.RUNNING,
        "COMPLETED": WorkflowStatus.COMPLETED,
        "FAILED": WorkflowStatus.FAILED,
        "CANCELED": WorkflowStatus.CANCELLED,
        "TERMINATED": WorkflowStatus.TERMINATED,
        "CONTINUED_AS_NEW": WorkflowStatus.CONTINUED_AS_NEW,
        "TIMED_OUT": WorkflowStatus.TIMED_OUT,
    }
    return status_mapping.get(temporal_status.name, WorkflowStatus.FAILED)


@router.post(
    "/start",
    response_model=WorkflowStartResponse,
    summary="Start workflow",
    description="Start a new workflow execution",
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "A workflow with this workflow_id already exists"
        }
    },
)
async def start_workflow(
    request: WorkflowStartRequest,
    client: Client = Depends(get_temporal_client),
    config: AppConfig = Depends(get_config),
) -> WorkflowStartResponse:
    """TEMPLATE: Start a workflow - customize for your workflow type."""
    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.workflows.start")

    # Generate workflow ID if not provided
    workflow_id = (
        request.workflow_id or f"{request.workflow_type}-{uuid.uuid4().hex[:8]}"
    )
    task_queue = request.task_queue or config.temporal_task_queue

    try:
        # Validate workflow type name against registered types
        workflow_type_name = AVAILABLE_WORKFLOWS.get(request.workflow_type)
        if not workflow_type_name:
            available_types = ", ".join(AVAILABLE_WORKFLOWS.keys())
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unknown workflow type '{request.workflow_type}'. "
                    f"Available types: {available_types}"
                ),
            )

        # Validate task queue against allowlist (mirrors workflow_type check)
        allowed_queues = get_allowed_task_queues(config)
        if task_queue not in allowed_queues:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Task queue '{task_queue}' is not permitted. "
                    f"Allowed: {', '.join(sorted(allowed_queues))}"
                ),
            )

        # Start the workflow using workflow type name
        # API gateway doesn't need workflow class imports - just the type name
        handle = await client.start_workflow(
            request.workflow_type,  # Use string workflow type name
            request.input_data,
            id=workflow_id,
            task_queue=task_queue,
        )

        logger.info(f"Workflow started: {workflow_id}")

        return WorkflowStartResponse(
            workflow_id=handle.id,
            run_id=handle.result_run_id or handle.first_execution_run_id or "unknown",
            task_queue=task_queue,
            started_at=datetime.now(UTC),
        )

    except HTTPException:
        # Re-raise HTTPException to preserve status code (e.g., 400 for validation)
        raise
    except WorkflowAlreadyStartedError as e:
        # Not an RPCError, so without this a duplicate ID would surface as a 500.
        # The 409 only confirms that the caller's own workflow_id is taken. It
        # discloses no other workflow ID, owner, type, status or data.
        logger.warning(f"Workflow already exists: {workflow_id}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workflow {workflow_id} already exists",
        ) from e
    except RPCError as e:
        _raise_if_temporal_unavailable(client, e)
        logger.error(f"Failed to start workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start workflow",
        ) from e
    except Exception as e:
        logger.error(f"Failed to start workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start workflow",
        ) from e


@router.get(
    "/{workflow_id}/status",
    response_model=WorkflowStatusResponse,
    summary="Get workflow status",
    description="Get current workflow status and result",
)
async def get_workflow_status(
    workflow_id: WorkflowIdPath,
    client: Client = Depends(get_temporal_client),
    config: AppConfig = Depends(get_config),
) -> WorkflowStatusResponse:
    """TEMPLATE: Get workflow status - customize result handling as needed."""
    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.workflows.status")

    try:
        handle = client.get_workflow_handle(workflow_id)
        description = await _get_managed_description(handle, config)

        # Map status
        workflow_status = (
            _map_temporal_status(description.status)
            if description.status
            else WorkflowStatus.FAILED
        )

        # Get result if completed
        result = None
        error_message = None

        if workflow_status == WorkflowStatus.COMPLETED:
            # Pin to the run describe() just reported, and do not follow the chain
            # of runs it leads to (a cron run's next run, a retry, continue-as-new),
            # so the result always belongs to the run whose status is returned.
            run_handle = client.get_workflow_handle(
                workflow_id, run_id=description.run_id
            )
            try:
                result = await run_handle.result(follow_runs=False)
            except RPCError as result_error:
                _raise_if_temporal_unavailable(client, result_error)
                logger.warning(
                    f"Result fetch failed, returning status without result: "
                    f"workflow_id={workflow_id}, error={result_error}"
                )
            except Exception as result_error:
                logger.warning(
                    f"Result could not be read, returning status without result: "
                    f"workflow_id={workflow_id}, error={result_error}"
                )
        elif workflow_status == WorkflowStatus.FAILED:
            failure = getattr(description, "failure", None)
            error_message = failure.message if failure else "Workflow failed"

        return WorkflowStatusResponse(
            workflow_id=workflow_id,
            run_id=description.run_id,
            workflow_type=description.workflow_type,
            task_queue=description.task_queue,
            status=workflow_status,
            started_at=description.start_time,
            completed_at=description.close_time,
            result=result,
            error=error_message,
            history_length=description.history_length,
            pending_activities=[],  # Customize as needed
        )

    except HTTPException:
        raise
    except RPCError as e:
        _raise_if_temporal_unavailable(client, e)
        logger.error(f"Failed to get workflow status {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        ) from e
    except Exception as e:
        logger.error(f"Failed to get workflow status {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {workflow_id}",
        ) from e


@router.get("/", summary="List workflows", description="List running workflows")
async def list_workflows(
    limit: int = Query(
        default=10, ge=1, le=100, description="Maximum number of workflows to return"
    ),
    status_filter: Literal[
        "Running",
        "Completed",
        "Failed",
        "Canceled",
        "Terminated",
        "ContinuedAsNew",
        "TimedOut",
    ] = Query(
        default="Running",
        description="Filter by workflow status. Valid values: Running, Completed, Failed, Canceled, Terminated, ContinuedAsNew, TimedOut",
        examples=["Running", "Completed", "Failed"],
    ),
    client: Client = Depends(get_temporal_client),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    """
    List workflows - needed for API-first workflow management.

    Valid status_filter values: Running, Completed, Failed, Canceled, Terminated, ContinuedAsNew, TimedOut
    """
    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.workflows.list")

    try:
        # Restrict results to the gateway's managed workflow type(s) and task
        # queue(s) so a caller can't discover or read runs of another team's
        # workflow through this gateway's list endpoint.
        workflow_type_clause = " OR ".join(
            f'WorkflowType="{workflow_type}"' for workflow_type in AVAILABLE_WORKFLOWS
        )
        task_queue_clause = " OR ".join(
            f'TaskQueue="{task_queue}"'
            for task_queue in get_allowed_task_queues(config)
        )
        visibility_query = (
            f'ExecutionStatus="{status_filter}" '
            f"AND ({workflow_type_clause}) AND ({task_queue_clause})"
        )

        # List workflows using client
        workflows = []
        async for workflow in client.list_workflows(visibility_query):
            workflows.append(
                {
                    "workflow_id": workflow.id,
                    "run_id": workflow.run_id,
                    "workflow_type": workflow.workflow_type,
                    "status": workflow.status.name if workflow.status else "UNKNOWN",
                    "start_time": workflow.start_time.isoformat()
                    if workflow.start_time
                    else None,
                    "close_time": workflow.close_time.isoformat()
                    if workflow.close_time
                    else None,
                    "task_queue": workflow.task_queue,
                }
            )

            if len(workflows) >= limit:
                break

        logger.info(
            f"Listed workflows: count={len(workflows)}, status_filter={status_filter}"
        )
        return {"workflows": workflows, "total": len(workflows)}

    except RPCError as e:
        _raise_if_temporal_unavailable(client, e)
        logger.error(f"Failed to list workflows: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list workflows",
        ) from e
    except Exception as e:
        logger.error(f"Failed to list workflows: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list workflows",
        ) from e


@router.get(
    "/{workflow_id}/query",
    summary="Query workflow state",
    description="Query workflow for current state information",
)
async def query_workflow(
    workflow_id: WorkflowIdPath,
    query_name: Annotated[
        str, Query(min_length=1, max_length=MAX_HANDLER_NAME_LEN)
    ] = "get_status",
    client: Client = Depends(get_temporal_client),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    """Query workflow state - needed for API-first approval detection."""
    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.workflows.query")

    if query_name not in ALLOWED_QUERIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Query '{query_name}' is not permitted. "
                f"Allowed: {', '.join(sorted(ALLOWED_QUERIES))}"
            ),
        )

    try:
        handle = client.get_workflow_handle(workflow_id)
        await _get_managed_description(handle, config)

        # Try to query the workflow
        try:
            result = await handle.query(query_name)
            logger.info(
                f"Workflow queried: workflow_id={workflow_id}, query_name={query_name}"
            )
            return {
                "workflow_id": workflow_id,
                "query_name": query_name,
                "result": result,
            }
        except RPCError as query_error:
            _raise_if_temporal_unavailable(client, query_error)
            # If query fails, return basic info
            logger.warning(
                f"Query failed, returning basic status: "
                f"workflow_id={workflow_id}, error={str(query_error)}"
            )
            return {
                "workflow_id": workflow_id,
                "query_name": query_name,
                "result": None,
                "error": "Query failed",
            }
        except Exception as query_error:
            # If query fails, return basic info
            logger.warning(
                f"Query failed, returning basic status: "
                f"workflow_id={workflow_id}, error={str(query_error)}"
            )
            return {
                "workflow_id": workflow_id,
                "query_name": query_name,
                "result": None,
                "error": "Query failed",
            }

    except HTTPException:
        raise
    except RPCError as e:
        _raise_if_temporal_unavailable(client, e)
        logger.error(f"Failed to query workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found or query failed: {workflow_id}",
        ) from e
    except Exception as e:
        logger.error(f"Failed to query workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found or query failed: {workflow_id}",
        ) from e


@router.post(
    "/{workflow_id}/approve",
    summary="Approve workflow",
    description=(
        "Send the approve signal to a specific run that is awaiting review. "
        "Requires the run ID, approver identity, and a digest of the "
        "reviewed content; fails if the run is not awaiting review."
    ),
)
async def approve_workflow(
    approve_request: WorkflowApproveRequest,
    workflow_id: WorkflowIdPath,
    approver_id: str = Depends(get_approver_identity),
    client: Client = Depends(get_temporal_client),
    config: AppConfig = Depends(get_config),
) -> dict[str, str]:
    """Approve a specific awaiting-review run (HITL step 3)."""
    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.workflows.approve")
    lane = APPROVAL_LANES[approve_request.lane]

    try:
        handle = client.get_workflow_handle(workflow_id, run_id=approve_request.run_id)
        try:
            # The handle is already pinned to run_id, so describe() raises
            # directly for a stale/nonexistent run_id rather than returning a
            # mismatched one - translate that to the same 404 the sibling
            # endpoints return for "run not found" instead of a 500.
            await _get_managed_description(handle, config)
        except HTTPException:
            raise
        except RPCError as describe_error:
            _raise_if_temporal_unavailable(client, describe_error)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {approve_request.run_id}",
            ) from describe_error
        except Exception as describe_error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run not found: {approve_request.run_id}",
            ) from describe_error

        status_before = await handle.query("get_status")
        if status_before.get("current_phase") != lane["awaiting_phase"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Run is not awaiting review",
            )

        await handle.signal(
            lane["signal_name"],
            args=[approver_id, approve_request.content_digest],
        )

        # HILSignalsMixin's approve signals are a silent no-op on a digest
        # mismatch or a review lane that closed between the check above and
        # this signal, so confirm the approval actually landed before
        # reporting success.
        status_after = await handle.query("get_status")
        if status_after.get(lane["approved_by_field"]) != approver_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approval was not recorded",
            )

        logger.info(
            f"Approval sent: workflow_id={workflow_id}, "
            f"run_id={approve_request.run_id}, lane={approve_request.lane}"
        )

        return {
            "status": "approved",
            "workflow_id": workflow_id,
            "run_id": approve_request.run_id,
            "lane": approve_request.lane,
            "message": "Workflow approved, proceeding to completion",
        }

    except HTTPException:
        raise
    except RPCError as e:
        _raise_if_temporal_unavailable(client, e)
        logger.error(f"Failed to approve workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to approve workflow",
        ) from e
    except Exception as e:
        logger.error(f"Failed to approve workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to approve workflow",
        ) from e


@router.post(
    "/{workflow_id}/signal",
    summary="Send signal to workflow",
    description="Send an arbitrary signal to a running workflow",
)
async def signal_workflow(
    signal_request: WorkflowSignalRequest,
    workflow_id: WorkflowIdPath,
    approver_id: str = Depends(get_approver_identity),
    client: Client = Depends(get_temporal_client),
    config: AppConfig = Depends(get_config),
) -> dict[str, str]:
    """TEMPLATE: Generic signal endpoint - prefer the explicit /approve endpoint for MyWorkflow."""
    from foundry_temporal.logging import get_logger

    logger = get_logger().bind(component="api.workflows.signal")

    if signal_request.signal_name not in ALLOWED_SIGNALS:
        raise HTTPException(
            # stdlib name: starlette's HTTP_422_UNPROCESSABLE_ENTITY is deprecated,
            # and its replacement doesn't exist in older starlette releases.
            status_code=HTTPStatus.UNPROCESSABLE_CONTENT,
            detail=(
                f"Signal '{signal_request.signal_name}' is not permitted. "
                f"Allowed: {', '.join(sorted(ALLOWED_SIGNALS))}"
            ),
        )

    try:
        handle = client.get_workflow_handle(workflow_id)
        await _get_managed_description(handle, config)
        await handle.signal(
            signal_request.signal_name,
            args=signal_request.build_signal_args(approver_id),
        )

        logger.info(
            f"Signal sent: workflow_id={workflow_id}, "
            f"signal_name={signal_request.signal_name}"
        )

        return {"message": "Signal sent", "workflow_id": workflow_id}

    except HTTPException:
        raise
    except RPCError as e:
        _raise_if_temporal_unavailable(client, e)
        logger.error(f"Failed to send signal to workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send signal",
        ) from e
    except Exception as e:
        logger.error(f"Failed to send signal to workflow {workflow_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send signal",
        ) from e
