"""App category/tag helpers.

Provides the canonical category list, keyword-based tag inference for apps
that have no explicit tags yet, and validation helpers.
"""

DEFAULT_CATEGORIES = [
    "Dev",
    "Media",
    "Games",
    "Office",
    "Graphics",
    "Internet",
    "Social",
    "System",
    "Utility",
]

# Keyword map for inferring tags from an app name. Order matters: the first
# matching group wins.
_TAG_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Games", ("game", "steam", "lutris", "heroic", "minecraft", "godot", "unity", "retroarch", "emulator")),
    (
        "Dev",
        ("code", "studio", "terminal", "git", "docker", "kube", "android", "ide", "python", "rust", "go ", "niruvi"),
    ),
    (
        "Media",
        (
            "media",
            "video",
            "vlc",
            "spotify",
            "music",
            "player",
            "kdenlive",
            "obs",
            "ffmpeg",
            "audacity",
            "gimp",
            "paint",
            "audio",
            "sound",
        ),
    ),
    ("Graphics", ("blender", "krita", "inkscape", "gimp", "paint", "draw", "design", "photo")),
    ("Office", ("office", "libre", "document", "pdf", "word", "spreadsheet", "slides", "notes", "writer")),
    ("Internet", ("browser", "firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "tor", "web", "internet")),
    ("Social", ("discord", "slack", "telegram", "whatsapp", "signal", "messenger", "matrix", "element")),
    ("System", ("system", "monitor", "settings", "disk", "task", "manager", "terminal", "top", "hwinfo", "cpu")),
]


def infer_tags(app_name: str) -> list[str]:
    """Infer suggested tags from an app name using a keyword map."""
    if not app_name:
        return []
    low = app_name.lower()
    tokens = set(low.split())
    for category, keywords in _TAG_KEYWORDS:
        for kw in keywords:
            k = kw.strip()
            # Short keywords must be whole words ("tor" must not match "monitor").
            if k in tokens or (len(k) >= 4 and k in low):
                return [category]
    return []


def normalize_tags(tags) -> list[str]:
    """Clean user-supplied tags: dedupe, strip, title-case the known categories."""
    if not tags:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        tag = str(tag).strip()
        if not tag:
            continue
        lowered = tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        for cat in DEFAULT_CATEGORIES:
            if cat.lower() == lowered:
                tag = cat
                break
        result.append(tag)
    return result
