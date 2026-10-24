from __future__ import annotations

from typing import Any

import requests

from app.config import Settings
from app.schemas import QualityGateAssessment
from app.services.privacy import redact_sensitive_text

QUALITY_GATE_QUESTIONS: dict[str, dict[str, Any]] = {
    "grounding_ok": {
        "type": "noul",
        "instructions": "报告中的主要事实是否都能被提供的本案证据支持？",
    },
    "authority_sufficient": {
        "type": "noul",
        "instructions": "当前检索到的权威依据是否足以支撑报告中的法律分析？",
    },
    "overclaiming_risk": {
        "type": "noul",
        "instructions": "报告是否存在确定性责任、胜诉或其他超出证据范围的法律结论？",
    },
    "needs_human_review": {
        "type": "noul",
        "instructions": "这份报告是否必须由人工复核后才能展示给使用者？",
    },
    "risk_level": {
        "type": "choice",
        "instructions": "判断这份报告交给使用者前需要关注的风险等级。",
        "criteria": {
            "low": "仅做材料整理，未涉及明显法律判断。",
            "medium": "包含初步分析，需要人工确认引用和表述。",
            "high": "存在证据缺失、结论越界或重大法律风险。",
        },
    },
    "evidence_sufficiency": {
        "type": "score",
        "instructions": "评价当前证据是否足以支撑报告中的初步分析。",
        "criteria": ["不足", "部分足够", "基本足够", "充分"],
    },
}

RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}


def _fallback(reason: str) -> QualityGateAssessment:
    return QualityGateAssessment(
        available=False,
        needs_human_review=True,
        reason=reason[:500],
    )


def _noul_probability(answer: dict[str, Any]) -> float:
    value = answer.get("noul")
    if not isinstance(value, int | float):
        raise ValueError("JEV noul answer is missing a probability")
    return max(0.0, min(float(value), 1.0))


def _answer_confidence(answer: dict[str, Any]) -> float:
    explicit = answer.get("confidence")
    if isinstance(explicit, int | float):
        return max(0.0, min(float(explicit), 1.0))
    if answer.get("type") == "noul":
        probability = _noul_probability(answer)
        return max(probability, 1.0 - probability)
    return 0.0


def _quality_confidence(answers: dict[str, Any]) -> float:
    confidences = [_answer_confidence(answers[name]) for name in QUALITY_GATE_QUESTIONS]
    return min(confidences)


def _parse_response(payload: dict[str, Any], settings: Settings) -> QualityGateAssessment:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("JEV response does not contain answers")
    missing = sorted(set(QUALITY_GATE_QUESTIONS) - set(answers))
    if missing:
        raise ValueError(f"JEV response is missing answers: {', '.join(missing)}")

    risk_answer = answers["risk_level"]
    risk_level = risk_answer.get("choice")
    if risk_level not in {"low", "medium", "high"}:
        raise ValueError("JEV risk_level answer is invalid")
    confidence = _quality_confidence(answers)
    needs_human = _noul_probability(answers["needs_human_review"]) >= 0.5
    overclaiming_risk = _noul_probability(answers["overclaiming_risk"])
    if risk_level == "high" or overclaiming_risk >= 0.5:
        needs_human = True
    if confidence < settings.jev_min_confidence:
        needs_human = True
    return QualityGateAssessment(
        available=True,
        needs_human_review=needs_human,
        risk_level=risk_level,
        confidence=confidence,
        answers=answers,
        model_version=payload.get("model_version") or payload.get("model"),
        request_id=payload.get("id"),
        latency_ms=payload.get("latency_ms"),
        reason=(
            "JEV 置信度低于阈值，保留人工复核"
            if confidence < settings.jev_min_confidence
            else None
        ),
    )


def evaluate_quality_gate(
    settings: Settings,
    state: str,
    *,
    request_id: str,
) -> QualityGateAssessment:
    """调用 JEV 评估报告质量；未启用或调用失败时安全降级为人工复核。"""
    if not settings.jev_enabled:
        return _fallback("JEV 未启用")
    if not settings.jev_api_key:
        return _fallback("JEV 未配置 API Key")
    try:
        response = requests.post(
            f"{settings.jev_base_url.rstrip('/')}/systemone/",
            headers={
                "Authorization": f"Bearer {settings.jev_api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": request_id,
            },
            json={
                "model": settings.jev_model,
                "state": redact_sensitive_text(state[:100_000]),
                "questions": QUALITY_GATE_QUESTIONS,
            },
            timeout=settings.jev_timeout_seconds,
        )
        response.raise_for_status()
        return _parse_response(response.json(), settings)
    except (requests.RequestException, AttributeError, TypeError, ValueError, KeyError) as exc:
        return _fallback(f"JEV 质量门不可用：{type(exc).__name__}: {exc}")


def quality_gate_summary(quality_gate: dict[str, Any] | None) -> list[str]:
    """把质量门结果转换为前端可直接展示的安全文案。"""
    if not quality_gate or not quality_gate.get("available"):
        reason = quality_gate.get("reason") if quality_gate else None
        return [
            "JEV 质量门：不可用",
            "建议：仍需人工复核，报告不会自动放行",
            f"原因：{reason or '未配置或未找到质量门结果'}",
        ]
    risk = RISK_LABELS.get(str(quality_gate.get("risk_level")), "未知")
    confidence = quality_gate.get("confidence")
    confidence_text = (
        f"{round(float(confidence) * 100)}%"
        if isinstance(confidence, int | float)
        else "未知"
    )
    model_version = quality_gate.get("model_version") or "未知版本"
    return [
        f"JEV 质量门：已完成（{model_version}）",
        f"风险等级：{risk}",
        f"置信度：{confidence_text}",
        "建议：仍需人工复核，报告不会自动放行",
        *( [f"提示：{quality_gate['reason']}"] if quality_gate.get("reason") else [] ),
    ]
