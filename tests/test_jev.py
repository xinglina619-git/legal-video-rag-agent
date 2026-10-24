from uuid import uuid4

import pytest
import requests

from app.config import Settings
from app.schemas import ReportResponse


def _jev_module():
    try:
        from app.services import jev
    except ImportError as exc:  # 让 RED 阶段以断言失败呈现，而不是测试收集错误。
        pytest.fail(f"JEV client module is missing: {exc}")
    return jev


def test_jev_settings_are_disabled_by_default():
    settings = Settings(_env_file=None)

    assert settings.jev_enabled is False
    assert settings.jev_model == "jev-1.13"
    assert settings.jev_min_confidence == 0.85


def test_report_response_exposes_optional_quality_gate():
    assert "quality_gate" in ReportResponse.model_fields
    payload = ReportResponse(
        report_markdown="# 报告",
        citations=[],
        review_task_id=uuid4(),
        status="pending",
        legal_domain="general",
    )
    assert payload.quality_gate is None


def test_quality_gate_posts_typed_questions_and_parses_response(monkeypatch):
    jev = _jev_module()
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "dec_123",
                "model": "jev-1.13",
                "model_version": "jev-1.13-20260917",
                "answers": {
                    "grounding_ok": {"type": "noul", "noul": 0.96},
                    "authority_sufficient": {"type": "noul", "noul": 0.90},
                    "overclaiming_risk": {"type": "noul", "noul": 0.04},
                    "needs_human_review": {"type": "noul", "noul": 0.91},
                    "risk_level": {"type": "choice", "choice": "medium", "confidence": 0.88},
                    "evidence_sufficiency": {"type": "score", "score": 2.2, "confidence": 0.86},
                },
                "usage": {"input_tokens": 120, "charged_tokens": 120},
                "latency_ms": 220,
            }

    def fake_post(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return Response()

    monkeypatch.setattr(jev.requests, "post", fake_post)
    result = jev.evaluate_quality_gate(
        Settings(jev_enabled=True, jev_api_key="test"), "脱敏报告", request_id="r1"
    )

    assert result.available is True
    assert result.needs_human_review is True
    assert result.risk_level == "medium"
    assert captured["url"].endswith("/systemone/")
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer test"
    assert captured["kwargs"]["headers"]["Idempotency-Key"] == "r1"
    assert len(captured["kwargs"]["json"]["questions"]) == 6


def test_quality_gate_degrades_when_jev_is_unavailable(monkeypatch):
    jev = _jev_module()

    def fail_post(*_args, **_kwargs):
        raise requests.Timeout("timeout")

    monkeypatch.setattr(jev.requests, "post", fail_post)
    result = jev.evaluate_quality_gate(
        Settings(jev_enabled=True, jev_api_key="test"), "报告", request_id="r1"
    )

    assert result.available is False
    assert result.needs_human_review is True
    assert "timeout" in (result.reason or "").lower()


def test_quality_gate_redacts_sensitive_state_before_sending(monkeypatch):
    jev = _jev_module()
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            answers = {
                name: {"type": "noul", "noul": 0.9}
                for name in (
                    "grounding_ok",
                    "authority_sufficient",
                    "overclaiming_risk",
                    "needs_human_review",
                )
            }
            answers["risk_level"] = {"type": "choice", "choice": "medium", "confidence": 0.9}
            answers["evidence_sufficiency"] = {
                "type": "score",
                "score": 2.0,
                "confidence": 0.9,
            }
            return {"answers": answers, "model_version": "jev-1.13"}

    def fake_post(_url, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(jev.requests, "post", fake_post)
    jev.evaluate_quality_gate(
        Settings(jev_enabled=True, jev_api_key="test"),
        "手机号 13812345678，身份证 11010519491231002X",
        request_id="r2",
    )

    assert "13812345678" not in captured["json"]["state"]
    assert "19491231002X" not in captured["json"]["state"]


def test_quality_gate_summary_describes_available_and_unavailable_results():
    jev = _jev_module()

    available = jev.quality_gate_summary(
        {
            "available": True,
            "needs_human_review": True,
            "risk_level": "high",
            "confidence": 0.93,
            "model_version": "jev-1.13-20260917",
        }
    )
    unavailable = jev.quality_gate_summary(
        {"available": False, "needs_human_review": True, "reason": "请求超时"}
    )

    assert any("风险等级：高" in line for line in available)
    assert any("置信度：93%" in line for line in available)
    assert any("仍需人工复核" in line for line in available)
    assert any("不可用" in line for line in unavailable)
    assert any("请求超时" in line for line in unavailable)


def test_quality_gate_forces_human_review_for_high_risk_answer(monkeypatch):
    jev = _jev_module()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            answers = {
                "grounding_ok": {"type": "noul", "noul": 0.95},
                "authority_sufficient": {"type": "noul", "noul": 0.95},
                "overclaiming_risk": {"type": "noul", "noul": 0.95},
                "needs_human_review": {"type": "noul", "noul": 0.10},
                "risk_level": {"type": "choice", "choice": "high", "confidence": 0.95},
                "evidence_sufficiency": {"type": "score", "score": 2.0, "confidence": 0.95},
            }
            return {"answers": answers, "model_version": "jev-1.13"}

    monkeypatch.setattr(jev.requests, "post", lambda *_args, **_kwargs: Response())
    result = jev.evaluate_quality_gate(
        Settings(jev_enabled=True, jev_api_key="test"), "报告", request_id="r3"
    )

    assert result.risk_level == "high"
    assert result.needs_human_review is True
