import re


_PATTERNS = [
    (re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)"), "[手机号已脱敏]"),
    (
        re.compile(r"(?<![0-9Xx])(\d{6})(?:19|20)\d{2}\d{2}\d{2}\d{3}[0-9Xx](?![0-9Xx])"),
        r"\1************",
    ),
    (re.compile(r"(?<!\d)(?:\d[ -]?){16,19}(?!\d)"), "[银行卡号已脱敏]"),
]


def redact_sensitive_text(text: str) -> str:
    result = text
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result
