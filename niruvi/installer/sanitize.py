import logging
import re

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9 ._+\-]+$")
_IDENTIFIER_RE = re.compile(r"[^a-zA-Z0-9._-]")


def sanitize_bash_string(value: str, field_name: str = "value") -> str:
    """Sanitize a string for safe use in bash double-quoted contexts."""
    if not value:
        logger.warning("sanitize_bash_string: %s is empty", field_name)
        return ""
    if not _SAFE_NAME_RE.match(value):
        safe = re.sub(r"[^a-zA-Z0-9 ._+\-]", "", value)
        safe = safe[:200]
        stripped = value != safe
        if stripped:
            logger.warning(
                "sanitize_bash_string: stripped unsafe characters from %s (original %r -> safe %r)",
                field_name,
                value,
                safe,
            )
        if not safe:
            logger.error(
                "sanitize_bash_string: %s is entirely unsafe after stripping: %r",
                field_name,
                value,
            )
        return safe
    return value[:200]


def sanitize_identifier(value: str, field_name: str = "value") -> str:
    """Strict sanitization for values used as filenames/identifiers."""
    safe = _IDENTIFIER_RE.sub("", value)[:200]
    if not safe:
        logger.error("sanitize_identifier: %s is empty after cleaning: %r", field_name, value)
    return safe


def escape_bash_double_quoted(value: str) -> str:
    """Escape a value for safe embedding in a bash double-quoted string.

    Unlike :func:`sanitize_bash_string`, this preserves the original content
    (URLs, paths with ``:``/``/``, etc.) and only escapes the characters that
    bash treats specially inside double quotes.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
