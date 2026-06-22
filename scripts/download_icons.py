#!/usr/bin/env python3
"""Download Phosphor icons for Niruvi AppImage bundling."""

import os
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS_DIR = os.path.join(BASE_DIR, "asset", "icons", "Phosphor")
SVG_DIR = os.path.join(ICONS_DIR, "scalable", "actions")

REPO_URL = "https://raw.githubusercontent.com/phosphor-icons/core/main/raw/regular"

# freedesktop-name -> Phosphor-name mapping
ICON_MAP = {
    # Actions / toolbar
    "document-open": "file",
    "document-save": "floppy-disk",
    "document-properties": "gear",
    "document-export": "upload",
    "folder-open": "folder-open",
    "edit-copy": "copy",
    "edit-cut": "scissors",
    "edit-paste": "clipboard-text",
    "edit-delete": "trash",
    "edit-find": "magnifying-glass",
    "edit-clear": "eraser",
    "list-add": "plus",
    "list-remove": "minus",
    "preferences-system": "gear",
    "preferences-other": "sliders",
    "view-refresh": "arrows-clockwise",
    "media-playback-start": "play",
    "format-justify-left": "align-left",
    "go-previous": "arrow-left",
    "go-next": "arrow-right",
    "arrow-right": "arrow-right",
    "media-skip-forward": "fast-forward",
    "user-desktop": "desktop",
    "applications-utilities": "toolbox",
    "application-exit": "door",
    "scroll": "scroll",
    "archive": "archive",

    # Dialogs
    "dialog-cancel": "x",
    "dialog-close": "x-circle",
    "dialog-error": "warning-circle",
    "dialog-ok": "check",
    "dialog-ok-apply": "check-circle",
    "dialog-warning": "warning",
    "dialog-information": "info",
    "help-about": "info",
    "help-contents": "book-open",
    "bug": "bug",
    "tools-report-bug": "bug",

    # Emblems / status
    "emblem-system": "gear",
    "emblem-downloads": "download",
    "emblem-ok": "check-circle",
    "emblem-default": "star",
    "emblem-documents": "file-text",
    "download": "download",

    # MIME types
    "package-x-generic": "package",
    "application-x-archive": "archive",
    "application-x-executable": "cube",

    # Misc
    "computer": "monitor",
    "clock": "clock",
    "help-faq": "question",
}

# Additional fallback symlinks (same Phosphor icon, different freedesktop name)
SYMLINKS = {
    "go-previous": "arrow-left",
    "go-next": "arrow-right",
    "tools-report-bug": "bug",
    "application-x-archive": "package",
    "preferences-system": "gear",
    "emblem-system": "gear",
    "emblem-ok": "check-circle",
    "application-exit": "door",
    "media-playback-start": "play",
    "format-justify-left": "align-left",
    "media-skip-forward": "fast-forward",
    "application-x-executable": "cube",
    "dialog-information": "info",
    "document-properties": "gear",
    "document-export": "upload",
}


def download_icon(name):
    url = f"{REPO_URL}/{name}.svg"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception:
        return None


def main():
    os.makedirs(SVG_DIR, exist_ok=True)

    downloaded = {}
    failed = []

    for fdo_name, ph_name in ICON_MAP.items():
        target = os.path.join(SVG_DIR, f"{fdo_name}.svg")
        if os.path.exists(target):
            downloaded[fdo_name] = ph_name
            continue

        svg = download_icon(ph_name)
        if svg:
            with open(target, "w") as f:
                f.write(svg)
            downloaded[fdo_name] = ph_name
            print(f"  OK {fdo_name}.svg <- {ph_name}.svg")
        else:
            failed.append(fdo_name)
            print(f"  FAIL {fdo_name}.svg <- {ph_name}.svg")

    # Create symlinks (just copy for AppImage compatibility)
    for link_name, target_name in SYMLINKS.items():
        link_path = os.path.join(SVG_DIR, f"{link_name}.svg")
        if os.path.exists(link_path):
            continue
        target_path = os.path.join(SVG_DIR, f"{target_name}.svg")
        if os.path.exists(target_path):
            with open(link_path, "w") as f:
                with open(target_path) as src:
                    f.write(src.read())
            print(f"  LINK {link_name}.svg -> {target_name}.svg")

    # Write index.theme
    theme_file = os.path.join(ICONS_DIR, "index.theme")
    with open(theme_file, "w") as f:
        f.write("""[Icon Theme]
Name=Phosphor
Comment=Bundled Phosphor icons for Niruvi AppImage Manager
Inherits=hicolor
Hidden=false
Example=document-open

[scalable/actions]
Size=48
Type=Scalable
Context=Actions
MinSize=16
MaxSize=256
""")

    print(f"\nDone. {len(downloaded)} icons downloaded, {len(failed)} failed.")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
