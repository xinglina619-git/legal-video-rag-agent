# Task Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-style task record and optional queue execution path for long-running material ingestion and report generation.

**Architecture:** Add `TaskRecord` as durable task state, `task_queue.py` as the task service/worker boundary, `/tasks/{id}` as the query API, and optional `background` parameters on existing material/report APIs. Default queue mode is `inline` so tests and demos do not require Redis; `rq` mode is documented and isolated behind optional imports.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, optional Redis/RQ, pytest, Streamlit.

## Global Constraints

- Keep current synchronous API behavior working by default.
- Queue mode defaults to `inline`.
- Do not require Redis/RQ for the normal test suite.
- Task metadata and errors must be safe to show in the UI.
- Long-task state changes must emit AppEvent observability events.

---

### Task 1: Durable TaskRecord and schemas

**Files:**
- Modify: `app/db.py`
- Modify: `app/schemas.py`
- Test: `tests/test_task_queue.py`

**Interfaces:**
- `TaskStatusResponse`
- `TaskRecord`

Steps:

- [ ] Write failing tests for creating and reading task records.
- [ ] Add `TaskRecord` model.
- [ ] Add task response schemas.
- [ ] Run focused tests.

### Task 2: Task service

**Files:**
- Create: `app/services/task_queue.py`
- Test: `tests/test_task_queue.py`

**Interfaces:**
- `create_task`
- `get_task_payload`
- `task_response`
- `run_task`
- `enqueue_task`
- `run_material_ingest_task`
- `run_report_generation_task`

Steps:

- [ ] Write failing tests for success, failure, and inline execution.
- [ ] Implement task state transitions.
- [ ] Implement inline queue execution.
- [ ] Add optional RQ dispatch boundary.
- [ ] Run focused tests.

### Task 3: API integration

**Files:**
- Create: `app/routers/tasks.py`
- Modify: `app/main.py`
- Modify: `app/routers/materials.py`
- Modify: `app/routers/reports.py`
- Test: `tests/test_task_routes.py`

Steps:

- [ ] Write route tests for task status, background ingest, and background report.
- [ ] Add `/tasks/{task_id}` route.
- [ ] Add `background=true` support for material ingestion.
- [ ] Add `background` support for report generation.
- [ ] Preserve old synchronous responses.

### Task 4: Worker script and docs

**Files:**
- Create: `scripts/run_worker.py`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `docs/task-queue.md`
- Modify: `streamlit_app.py`

Steps:

- [ ] Add optional worker script.
- [ ] Document inline and RQ modes.
- [ ] Add Streamlit background checkboxes and status display.

### Task 5: Final verification and commit

Steps:

- [ ] Run ruff.
- [ ] Run pytest.
- [ ] Run compileall.
- [ ] Run git diff --check.
- [ ] Commit the phase.

