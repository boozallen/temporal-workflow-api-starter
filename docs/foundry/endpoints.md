# API Endpoints Reference

> **Note**: All examples use `MyWorkflow` which is available in the [temporal-workflow-worker-starter](https://github.com/boozallen/temporal-workflow-worker-starter) template. These examples match the tested [Postman collection](../../postman/API.postman_collection.json) configurations.

> **Authentication**: All `/workflows/*` endpoints require a Bearer token (`Authorization: Bearer <token>`). Set `API_AUTH_TOKEN` in your environment — the API is fail-closed and rejects all workflow requests when unset. Health check endpoints (`/health/*`) do not require authentication.

> **Size limits**: Request bodies over `MAX_REQUEST_BODY_BYTES` (default 4 MiB) are rejected with `413` as soon as they pass the limit, before the app processes them. Every `{workflow_id}` path parameter must be 1-256 characters. Field limits are listed under each endpoint; a value over its limit returns `422`.

## Workflow Management

### Start Workflow
**POST** `/workflows/start`

Start a new Temporal workflow execution.

**Request Body:**
```json
{
  "workflow_type": "MyWorkflow",
  "workflow_id": "optional-custom-id",
  "task_queue": "optional-custom-queue",
  "input_data": {
    "user_name": "TestUser",
    "task_type": "greeting",
    "priority": 1,
    "language": "en",
    "metadata": {
      "source": "api-docs",
      "timestamp": "2026-02-05T12:00:00Z"
    }
  }
}
```

**Available Workflow Types:**
- `MyWorkflow` - 3-step HITL workflow: my_activity → call_strands_agent → approve signal

**MyWorkflow Input Data Format** (checked by the worker in temporal-workflow-worker-starter when it runs the workflow, not by this gateway):
- `user_name` (required): Name of the user, 1-256 characters
- `task_type` (optional): Type of task, 1-64 characters (default: "greeting")
- `priority` (optional): Priority level 1-10 (default: 1)
- `language` (optional): Language code, 2-16 characters (default: "en")
- `metadata` (optional): Additional metadata dictionary, up to 50 keys

**Input limits** (requests over a limit return `422`):
- `workflow_type`, `workflow_id`, `task_queue`: 1-256 characters
- `input_data`: up to 32 top-level keys, keys up to 128 characters, and 4 MiB serialized (the Foundry-wide limits from [`foundry-agent-core`](https://github.com/boozallen/foundry-agent-packages/blob/HEAD/docs/foundry/packages/foundry-agent-core/index.md)). The size is measured as escaped JSON with a space after every `:` and `,`, so it can exceed the bytes on the wire: non-ASCII text counts 2-3x its UTF-8 size, quotes, backslashes, and control characters count 2x or more, and the added spaces count toward the cap. The whole request body is also capped at 4 MiB by default, so a request whose body is over that is rejected with `413` before `input_data` is checked.

**Response:**
```json
{
  "workflow_id": "MyWorkflow-abc12345",
  "run_id": "019c2fd0-2bae-7420-942f-e37c8e83b1ce",
  "task_queue": "workflow-task-queue",
  "started_at": "2026-02-05T21:58:17.779166Z"
}
```

Returns `409` if a workflow with the same `workflow_id` already exists. With the default ID reuse policy, that means one that is still running. This also happens when two requests send the same ID at the same time. Only one starts the workflow.

### Get Workflow Status
**GET** `/workflows/{workflow_id}/status`

Get current status and result of a workflow.

**Response:**
```json
{
  "workflow_id": "MyWorkflow-abc12345",
  "run_id": "uuid-run-id",
  "workflow_type": "MyWorkflow",
  "task_queue": "workflow-task-queue",
  "status": "completed",
  "started_at": "2025-01-01T12:00:00Z",
  "completed_at": "2025-01-01T12:05:00Z",
  "result": {"output": "workflow result"},
  "error": null,
  "history_length": 25,
  "pending_activities": []
}
```

**Status Values:**
- `running` - Workflow is executing
- `completed` - Workflow finished successfully
- `failed` - Workflow failed with error
- `cancelled` - Workflow was cancelled
- `terminated` - Workflow was terminated
- `continued_as_new` - Workflow continued as new execution
- `timed_out` - Workflow exceeded timeout

### List Workflows
**GET** `/workflows/`

List workflows with optional filtering and pagination.

**Query Parameters:**
- `limit` (optional): Maximum number of workflows to return (default: 10, max: 100)
- `status_filter` (optional): Filter by workflow status (default: "Running")

**Valid Status Values:**
- `Running` - Workflow is currently executing
- `Completed` - Workflow finished successfully
- `Failed` - Workflow failed with an error
- `Canceled` - Workflow was canceled
- `Terminated` - Workflow was terminated
- `ContinuedAsNew` - Workflow continued as new execution
- `TimedOut` - Workflow exceeded timeout

Each entry's `status` is Temporal's own status name (`RUNNING`, `COMPLETED`, `FAILED`,
`CANCELED`, `TERMINATED`, `CONTINUED_AS_NEW`, `TIMED_OUT`); the filter above takes the
visibility names.

**Response:**
```json
{
  "workflows": [
    {
      "workflow_id": "MyWorkflow-abc12345",
      "run_id": "uuid-run-id",
      "workflow_type": "MyWorkflow",
      "status": "RUNNING",
      "start_time": "2025-01-01T12:00:00Z",
      "close_time": null,
      "task_queue": "workflow-task-queue"
    }
  ],
  "total": 1
}
```

### Query Workflow
**GET** `/workflows/{workflow_id}/query`

Query a running workflow for current state information.

**Query Parameters:**
- `query_name` (optional): Name of the query to execute, 1-64 characters (default: "get_status"). Names outside the gateway's allowlist (`ALLOWED_QUERIES` in [`src/routers/workflows.py`](../../src/routers/workflows.py)) return `400`.

> **Note**: MyWorkflow (in `temporal-workflow-worker-starter`) answers the `get_status` query (from foundry-temporal's `QueryMixin`) with its current phase. A query the workflow does not handle, or one sent while no worker is running, returns `200` with a `null` result and `"error": "Query failed"`. Add `@workflow.query` handlers to your workflow for other queries.

**Response:**
```json
{
  "workflow_id": "MyWorkflow-abc12345",
  "query_name": "get_status",
  "result": null,
  "error": "Query failed"
}
```

### Approve Workflow
**POST** `/workflows/{workflow_id}/approve`

Approve a specific MyWorkflow run that is awaiting review. The workflow blocks at step 3 until this signal is received.

**Request Body:**
```json
{
  "run_id": "019c2fd0-2bae-7420-942f-e37c8e83b1ce",
  "approver_id": "jane.doe@example.com",
  "content_digest": "3f786850e387550fdab836ed7e6dc881de23001b",
  "lane": "general"
}
```

- `run_id` (required): Run ID of the awaiting-review run, 1-256 characters
- `approver_id` (required): Identity of the approver, 1-256 characters
- `content_digest` (required): Digest of the content the approver reviewed, 1-128 characters
- `lane` (optional, default `"general"`): Which review lane to approve - `general`, `plan`, or `research`. `approve`, `approve_plan`, and `approve_research` are only reachable through this endpoint, never through the generic `/signal` endpoint below.

Returns `404` if the run is not found and `409` if the run is not awaiting review (for the requested lane) or the approval was not recorded. The endpoint asks the run for its status first, so a worker must be running: with none to answer, the call waits for that query to time out and returns `500`.

**Response:**
```json
{
  "status": "approved",
  "workflow_id": "MyWorkflow-abc12345",
  "run_id": "019c2fd0-2bae-7420-942f-e37c8e83b1ce",
  "lane": "general",
  "message": "Workflow approved, proceeding to completion"
}
```

### Send Signal (Generic)
**POST** `/workflows/{workflow_id}/signal`

Send a signal to a running workflow. Approvals (`approve`, `approve_plan`, `approve_research`) are **not** allowed here - use the `/approve` endpoint above.

**Request Body:**
```json
{
  "signal_name": "provide_feedback",
  "approver_id": "jane.doe@example.com",
  "comments": "fix the introduction section"
}
```

- `signal_name` (required): 1-64 characters, and must be on the gateway's allowlist (`ALLOWED_SIGNALS` in [`src/models.py`](../../src/models.py)); other names - including the approval signals - return `422`
- `approver_id` (required): Identity of the caller sending this signal, 1-256 characters
- `comments` (optional, up to 65,536 characters): required (non-empty) for `provide_feedback` and `provide_plan_feedback`; must be omitted or `null` for `cancel_workflow`

**Response:**
```json
{
  "message": "Signal sent",
  "workflow_id": "MyWorkflow-abc12345"
}
```

## Health Checks

### Basic Health Check
**GET** `/health`

Basic API health status.

**Response:**
```json
{
  "status": "healthy",
  "service": "workflow-api",
  "timestamp": "2025-01-01T12:00:00Z",
  "version": "1.0.0",
  "dependencies": {}
}
```

In mock mode, `dependencies.temporal` reports `test_mode`.

### Detailed Health Check
**GET** `/health/detailed`

Comprehensive health check with all dependency details.

**Response (Healthy):**
```json
{
  "status": "healthy",
  "service": "workflow-api",
  "timestamp": "2025-01-01T12:00:00Z",
  "version": "1.0.0",
  "dependencies": {
    "temporal": {
      "service": "temporal",
      "status": "healthy",
      "connection": "verified"
    }
  }
}
```

When Temporal can't be reached, the endpoint still returns `200`, with
`dependencies.temporal` `{"service": "temporal", "status": "unhealthy"}`.

### Temporal Health Check
**GET** `/health/temporal`

Temporal-specific connectivity check.

**Response:**
```json
{
  "status": "healthy",
  "service": "temporal",
  "timestamp": "2025-01-01T12:00:00Z",
  "version": "1.0.0",
  "dependencies": {}
}
```

When Temporal can't be reached, `status` is `"unhealthy"` and the status code stays `200`.

### Readiness Check
**GET** `/health/ready`

Kubernetes readiness probe with Temporal connectivity check. This is the only
health endpoint that reports a Temporal outage through its status code, so
orchestrators can remove the instance from service:

| Condition | Status | `dependencies.temporal` |
|-----------|--------|-------------------------|
| Temporal reachable and the probe succeeds | `200` | `connected` |
| Mock mode enabled (`TESTING`/`INTEGRATION_TESTING`, non-production) | `200` | `test_mode` |
| Temporal reachable but rejects the probe with `PERMISSION_DENIED` or `INVALID_ARGUMENT` | `200` | `degraded` |
| Temporal unreachable: the initial connection fails or isn't ready within `TEMPORAL_PROBE_TIMEOUT_SECONDS` (default 0.9, which covers the connection wait and the probe together), the probe times out, or the probe fails with any other status or error | `503` | `disconnected` |

Every 503 uses the unhealthy body shown below, never the generic `detail` error
format, and it never includes connection error text, host, namespace, or TLS
settings. Failure details are written to the service log only.
`/health/detailed` and `/health/temporal` are diagnostic endpoints. They keep
returning `200` and report an outage in the body.
A `degraded` readiness response keeps top-level `status: "healthy"` because the
instance stays in service; monitors should alert on `dependencies.temporal`.

**Response (Healthy, HTTP 200):**
```json
{
  "status": "healthy",
  "service": "workflow-api",
  "timestamp": "2025-01-01T12:00:00Z",
  "version": "1.0.0",
  "dependencies": {
    "temporal": "connected"
  }
}
```

**Response (Unhealthy, HTTP 503):**
```json
{
  "status": "unhealthy",
  "service": "workflow-api",
  "timestamp": "2025-01-01T12:00:00Z",
  "version": "1.0.0",
  "dependencies": {
    "temporal": "disconnected"
  }
}
```

## Error Responses

All endpoints return standard HTTP status codes:

- `200` - Success
- `400` - Bad request (e.g., unknown workflow type, disallowed task queue or query name)
- `401` - Missing or invalid bearer token
- `404` - Workflow not found
- `409` - Approval conflict (run not awaiting review), or a workflow with that ID already exists
- `413` - Request body larger than `MAX_REQUEST_BODY_BYTES`
- `422` - Validation error (including a field over its size limit)
- `429` - Too many failed authentication attempts
- `500` - Internal server error
- `503` - Service unavailable. The API cannot reach Temporal, or Temporal rejected its credentials. Retry later.

Error response formats (the Temporal-unavailable 503 from `/health/ready` uses its readiness body instead):

- Most errors: a `detail` string.

  ```json
  {
    "detail": "Error description"
  }
  ```

- `422` request validation errors: a `detail` list, one entry per invalid field.

  ```json
  {
    "detail": [
      {
        "type": "missing",
        "loc": ["body", "workflow_type"],
        "msg": "Field required",
        "input": {"message": "Hello World"}
      }
    ]
  }
  ```

- foundry-temporal errors (`WorkflowError` `400`, `ValidationError` `422`,
  `ConfigurationError` `500`): an error object with its type, code and whether a
  retry can succeed.

  ```json
  {
    "error": "Workflow operation failed",
    "error_code": "WORKFLOW_ERROR",
    "error_type": "WorkflowError",
    "detail": null,
    "timestamp": "2026-01-01T12:00:00.000000",
    "retryable": true
  }
  ```

## OpenAPI Documentation

Interactive API documentation is available at:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

Verify all three render:

macOS, Linux and WSL:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/redoc
curl -s http://localhost:8000/openapi.json | python3 -c "import json,sys; json.load(sys.stdin)"
```

Windows PowerShell:
```powershell
curl.exe -s -o NUL -w "%{http_code}\n" http://localhost:8000/docs
curl.exe -s -o NUL -w "%{http_code}\n" http://localhost:8000/redoc
Invoke-RestMethod http://localhost:8000/openapi.json | Out-Null
```

Output:

```
200
200
```

The first two print `200`. The third prints nothing and exits 0 if `/openapi.json` is valid JSON — it raises `json.decoder.JSONDecodeError` otherwise.