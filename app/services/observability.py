from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID

from app.config import Settings
from app.db import AppEvent, SessionLocal
from app.services.privacy import redact_sensitive_text

MAX_METADATA_TEXT = 1200


def safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    return {
        str(key): _sanitize_value(value)
        for key, value in metadata.items()
    }


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return safe_metadata(value)
    if isinstance(value, list | tuple | set):
        return [_sanitize_value(item) for item in list(value)[:20]]
    text = str(value)
    if len(text) > MAX_METADATA_TEXT:
        text = text[:MAX_METADATA_TEXT] + "...[truncated]"
    return redact_sensitive_text(text)


def emit_event(
    event_type: str,
    *,
    status: str = "success",
    case_id: UUID | None = None,
    material_id: UUID | None = None,
    review_task_id: UUID | None = None,
    request_id: str | None = None,
    duration_ms: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    error_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        sanitized = safe_metadata(metadata)
        # 确保 JSON 可序列化，避免方言差异或复杂对象导致审计写入失败。
        sanitized = json.loads(json.dumps(sanitized, ensure_ascii=False, default=str))
        with SessionLocal() as session:
            session.add(
                AppEvent(
                    event_type=event_type,
                    status=status,
                    case_id=case_id,
                    material_id=material_id,
                    review_task_id=review_task_id,
                    request_id=request_id,
                    duration_ms=duration_ms,
                    provider=provider,
                    model=model,
                    error_type=error_type,
                    safe_metadata=sanitized,
                )
            )
            session.commit()
    except Exception:
        # 观测系统不能影响主业务流程；生产环境可在这里扩展本地日志。
        return


def configure_langsmith_environment(settings: Settings) -> None:
    if not settings.langsmith_tracing:
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if settings.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
