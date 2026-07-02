import re

_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9 ._+-]+$')


def sanitize_bash_string(value: str, field_name: str = "value") -> str:
    if not _SAFE_NAME_RE.match(value):
        safe = re.sub(r'[^a-zA-Z0-9 ._+-]', '', value)
        safe = safe[:100]
        return safe
    return value[:100]
