from app.services.privacy import redact_sensitive_text


def test_sensitive_fields_are_redacted():
    text = "电话13800138000，身份证110101199001011234，银行卡6222021234567890123"
    result = redact_sensitive_text(text)
    assert "13800138000" not in result
    assert "199001011234" not in result
    assert "6222021234567890123" not in result
