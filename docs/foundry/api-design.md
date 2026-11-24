# API Design Guide

## Overview

This template provides a pure REST API gateway for Temporal workflows with [foundry-temporal](https://github.com/boozallen/temporal-packages/blob/HEAD/docs/foundry/packages/foundry-temporal/index.md) integration. It handles workflow management operations but does NOT execute workflows - that requires separate worker services.

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Client App    │───▶│  Workflow API    │───▶│ Temporal Server │
│   (React/etc)   │    │   (FastAPI)      │    │  (External)     │
│                 │    │                  │    │                 │
│                 │    │ - REST endpoints │    │ ┌─────────────┐ │
│                 │    │ - foundry-temporal│    │ │   Worker    │ │
│                 │    │ - Health checks   │    │ │  Services   │ │
│                 │    │ - Error handling  │    │ │ (Separate)  │ │
└─────────────────┘    └──────────────────┘    └─┴─────────────┴─┘
```

**Key Principle**: API gateway communicates with Temporal server. Worker services (deployed separately) execute workflows.

## Core Components

### FastAPI Application ([`src/main.py`](../../src/main.py))
- FastAPI setup with [foundry-temporal](https://github.com/boozallen/temporal-packages/blob/HEAD/docs/foundry/packages/foundry-temporal/index.md) structured logging
- Application lifespan management with proper startup/shutdown
- Foundry-specific error handlers for WorkflowError, ValidationError, ConfigurationError
- Customize title, description, middleware as needed

### Workflow Router ([`src/routers/workflows.py`](../../src/routers/workflows.py))
- **POST /workflows/start** - Start new workflow executions with validation
- **GET /workflows/\{id\}/status** - Get comprehensive workflow status and results
- **POST /workflows/\{id\}/approve** - Explicit approve endpoint for MyWorkflow HITL step
- **POST /workflows/\{id\}/signal** - Generic signal endpoint for arbitrary signals
- **GET /workflows/\{id\}/query** - Query workflow state information
- **GET /workflows/** - List workflows with filtering and pagination

### Health Router ([`src/routers/health.py`](../../src/routers/health.py))
- **GET /health** - Basic API health check
- **GET /health/ready** - Kubernetes readiness probe with Temporal connectivity
- **GET /health/detailed** - Comprehensive health check with all dependencies
- **GET /health/temporal** - Temporal-specific connectivity check

### Models ([`src/models.py`](../../src/models.py))
- Template request/response models
- Customize for your workflow's input/output data structures

### Dependencies ([`src/dependencies.py`](../../src/dependencies.py))
- Temporal client connection management
- Add authentication, rate limiting as needed

### Configuration ([`src/config.py`](../../src/config.py))
- Environment-based configuration
- Add workflow-specific settings

## Customization Guidelines

1. **Workflow Registration**: Update `get_available_workflows()` in [`workflows.py`](../../src/routers/workflows.py) with your workflow names (currently registers `MyWorkflow`)
2. **Workflow Types**: Update `WorkflowStartRequest` with your specific workflow parameters
3. **Signal Endpoints**: Add explicit endpoints for each signal (see `/approve`). The generic `/signal` endpoint is kept for flexibility.
4. **Health Checks**: Add checks for your workflow's dependencies
5. **Authentication**: Implement in [`dependencies.py`](../../src/dependencies.py) if required
6. **Middleware**: Add CORS, logging, rate limiting in [`main.py`](../../src/main.py)

## Template Integration

This template includes a reference to the 3-step workflow from [`temporal-workflow-worker-starter`](https://github.com/boozallen/temporal-workflow-worker-starter):
- `MyWorkflow` - 3-step HITL workflow demonstrating:
  - **Step 1**: `my_activity` — initial data processing
  - **Step 2**: `call_strands_agent` — [foundry-temporal-agents](https://github.com/boozallen/temporal-packages/blob/HEAD/docs/foundry/packages/foundry-temporal-agents/index.md) integration with retry policy
  - **Step 3**: `approve` signal — Human-in-the-Loop approval gate (via `HILSignalsMixin`)
  - **State management**: Progress tracking (`_progress`, `_status`) throughout the 3 steps

This single workflow covers both key design document requirements, providing teams with a complete foundation for building production workflows.

Teams should replace this with their actual workflow names after deploying worker services.

## Foundry Integration

This template uses `foundry-temporal` for:
- Structured logging with `get_logger()`
- Standard observability patterns
- Temporal client best practices

## Development Workflow

1. Customize models and endpoints for your workflow
2. Update configuration for your environment
3. Add tests for your specific endpoints
4. Update documentation for your API
5. Deploy using provided Docker/compose setup