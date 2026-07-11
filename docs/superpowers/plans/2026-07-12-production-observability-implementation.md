# Production Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add production-style observability, audit events, optional LangSmith configuration, and report-quality regression evaluation to the legal multimodal RAG Agent.

**Architecture:** Introduce a small internal observability layer (`app.services.observability`) that writes sanitized events to a database table and can later fan out to LangSmith or other telemetry backends. Keep LangSmith optional and environment-driven, while adding deterministic local quality checks for generated reports.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic Settings, LangGraph, Qdrant, pytest, ruff, JSONL evaluation scripts.

## Global Constraints

- Keep all user-facing docs and explanatory comments in Simplified Chinese.
- Use the existing dedicated worktree `legal-video-rag-agent-production-observability` and branch `codex/production-observability`.
- Do not push to remote unless explicitly requested.
- LangSmith must be optional, disabled by default, and safe when no API key exists.
- Trace/audit metadata must be redacted before storage or external export.
- New behavior must be covered by tests written before implementation.

---

### Task 1: AppEvent model and observability hook

**Files:**
- Modify: `app/db.py`
- Create: `app/services/observability.py`
- Test: `tests/test_observability.py`

**Interfaces:**
- Produces: `emit_event(event_type: str, *, status="success", case_id=None, material_id=None, review_task_id=None, request_id=None, duration_ms=None, provider=None, model=None, error_type=None, metadata=None) -> None`
- Produces: `safe_metadata(metadata: dict | None) -> dict`

- [ ] Write tests that prove metadata is redacted, events are persisted, and emit failures do not break callers.
- [ ] Add `AppEvent` SQLAlchemy model.
- [ ] Implement `safe_metadata` and `emit_event`.
- [ ] Run `pytest tests/test_observability.py -q`.

### Task 2: Wire observability into core workflow

**Files:**
- Modify: `app/services/cases.py`
- Modify: `app/services/ingestion.py`
- Modify: `app/services/agent.py`
- Modify: `app/routers/reports.py`
- Test: `tests/test_agent_observability.py`

**Interfaces:**
- Consumes: `emit_event`

- [ ] Write tests that monkeypatch `emit_event` and assert key events are emitted.
- [ ] Emit `case.created` and `intake.answered`.
- [ ] Emit `material.normalized`, `material.indexed`, and failure events around ingestion.
- [ ] Emit `report.started`, `retrieval.completed`, `report.generated`, `review.approved`, and `review.rejected`.
- [ ] Run focused tests.

### Task 3: Optional LangSmith-safe configuration

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `docs/production-observability.md`
- Test: `tests/test_observability.py`

**Interfaces:**
- Produces config fields: `langsmith_tracing`, `langsmith_project`, `langsmith_sample_rate`, `langsmith_record_inputs`

- [ ] Add Settings fields with safe defaults.
- [ ] Document how to enable LangSmith and why it is disabled by default.
- [ ] Test defaults and metadata sampling helper behavior.

### Task 4: Report quality regression evaluation

**Files:**
- Create: `scripts/eval_report_quality.py`
- Create: `data/demo/loan_case/report_quality_cases.jsonl`
- Create: `tests/test_eval_report_quality.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `evaluate_report(markdown: str) -> dict`
- Produces CLI JSON summary with pass/fail rows.

- [ ] Write tests for passing report, missing citation, and forbidden legal conclusion.
- [ ] Implement deterministic report quality checks.
- [ ] Add demo JSONL case and README usage.

### Task 5: Final verification and commit

**Files:**
- All changed files.

- [ ] Run `python -m ruff check app streamlit_app.py scripts tests`.
- [ ] Run `python -m pytest -q -p no:cacheprovider --basetemp '.test-phase2'`.
- [ ] Run `python -m compileall -q app streamlit_app.py scripts`.
- [ ] Run `git diff --check`.
- [ ] Commit with message `feat: add production observability hooks`.

