"""Safe naming utilities for filesystem paths, registry keys, and command lines.

Provides functions to convert display names (which may contain spaces, Unicode,
and other problematic characters) into safe ASCII identifiers suitable for
filesystem paths, registry keys, and command-line arguments.
"""

import re
import unicodedata
from pathlib import Path


def safe_name(name: str) -> str:
    """Convert a display name into a safe filesystem/registry identifier.

    Rules:
    - Normalize Unicode to ASCII (NFKD, drop non-ASCII)
    - Replace any run of characters outside [A-Za-z0-9._-] with a single "-"
    - Strip leading/trailing "-", ".", "_"
    - Return "app" if the result is empty

    Args:
        name: Display name (e.g., "VLC media player", "Café Télé")

    Returns:
        Safe identifier (e.g., "VLC-media-player", "Caf-T-l", "app")

    Examples:
        >>> safe_name("VLC media player")
        'VLC-media-player'
        >>> safe_name("Café Télé")
        'Caf-T-l'
        >>> safe_name("!!!")
        'app'
    """
    if not isinstance(name, str):
        name = str(name)

    # Normalize Unicode to ASCII (NFKD decomposition, drop non-ASCII)
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")

    # Replace runs of unsafe characters with a single hyphen
    # Safe chars: ASCII letters, digits, period, hyphen, underscore
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_only)

    # Collapse multiple consecutive hyphens
    sanitized = re.sub(r"-+", "-", sanitized)

    # Strip leading/trailing unsafe characters
    sanitized = sanitized.strip("-._")

    # Return fallback if empty
    return sanitized or "app"


def side_by_side_name(base: str, n: int) -> str:
    """Generate a side-by-side installation name.

    Args:
        base: Base display name (will be passed through safe_name)
        n: Suffix number (2, 3, 4...)

    Returns:
        Safe name with numeric suffix, e.g., "App-2", "VLC-media-player-3"

    Examples:
        >>> side_by_side_name("My App", 2)
        'My-App-2'
        >>> side_by_side_name("Test", 5)
        'Test-5'
    """
    base_safe = safe_name(base)
    return f"{base_safe}-{n}"


def assert_no_spaces(path: Path) -> None:
    """Assert that a path contains no whitespace characters.

    Raises:
        ValueError: If any component of the path contains whitespace.

    Args:
        path: Path to validate.

    Raises:
        ValueError: If any path component contains whitespace.

    Examples:
        >>> assert_no_spaces(Path("a-b"))  # passes
        >>> assert_no_spaces(Path("a b"))  # raises ValueError
    """
    for part in path.parts:
        if any(c.isspace() for c in part):
            raise ValueError(f"Path component contains whitespace: {part!r}")
