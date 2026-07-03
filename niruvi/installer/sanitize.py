import logging
import re

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9 ._+\-@%/=:,]+$")


def sanitize_bash_string(value: str, field_name: str = "value") -> str:
    if not value:
        logger.warning("sanitize_bash_string: %s is empty", field_name)
        return ""
    if not _SAFE_NAME_RE.match(value):
        safe = re.sub(r"[^a-zA-Z0-9 ._+\-@%/=:,]", "", value)
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
