#!/usr/bin/env python3
"""
Download Fluent UI System Icons for Niruvi.

Maps freedesktop icon names to Fluent icon names and downloads SVGs.
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, List, Tuple

# Mapping from freedesktop icon names to Fluent UI System Icons names
FREEDESKTOP_TO_FLUENT = {
    # Navigation / Actions
    "view-refresh": "Arrow Clockwise",
    "list-add": "Add",
    "edit-delete": "Delete",
    "dialog-cancel": "Dismiss",
    "go-previous": "Arrow Previous",
    "go-next": "Arrow Next",
    "go-up": "Arrow Up",
    "go-home": "Home",
    "arrow-right": "Arrow Right",
    "arrow-left": "Arrow Left",
    "arrow-down": "Arrow Down",
    "arrow-up": "Arrow Up",

    # Dialog / Status
    "dialog-ok": "Checkmark",
    "dialog-ok-apply": "Checkmark Circle",
    "dialog-error": "Error Circle",
    "dialog-warning": "Warning",
    "dialog-information": "Info",
    "dialog-info": "Info",
    "dialog-close": "Dismiss",
    "dialog-password": "Lock Closed",

    # File / Document
    "package-x-generic": "Box",
    "application-x-archive": "Folder Zip",
    "application-x-executable": "App Generic",
    "document-open": "Folder Open",
    "document-open-recent": "Clock",
    "document-save": "Save",
    "document-save-as": "Save Copy",
    "document-revert": "Arrow Undo",
    "document-export": "Arrow Export",
    "document-properties": "Info",
    "document-new": "Document Add",
    "folder": "Folder",
    "folder-open": "Folder Open",
    "folder-new": "Folder Add",
    "folder-download": "Folder Arrow Down",

    # Edit
    "edit-copy": "Clipboard",
    "edit-copy-symbolic": "Clipboard",
    "edit-cut": "Cut",
    "edit-paste": "Clipboard Paste",
    "edit-undo": "Arrow Undo",
    "edit-redo": "Arrow Redo",
    "edit-find": "Search",
    "edit-clear": "Eraser Tool",
    "format-justify-left": "Text Align Left",

    # Applications
    "applications-internet": "Globe",
    "applications-system": "Window Dev Tools",
    "applications-utilities": "Toolbox",
    "preferences-system": "Settings",
    "help-contents": "Book Open",
    "help-about": "Info",
    "application-exit": "Sign Out",

    # Media
    "media-playback-start": "Play",
    "media-skip-forward": "Arrow Next",
    "media-skip-backward": "Arrow Previous",

    # System / Hardware
    "computer": "Desktop",
    "drive-harddisk": "Hard Drive",
    "device-laptop": "Laptop",
    "video-display": "Video",
    "monitor": "Desktop",
    "cpu": "Chip",
    "database": "Database",
    "network-server": "Globe",
    "security-high": "Shield",
    "security-low": "Shield Off",

    # Emblems
    "emblem-downloads": "Arrow Download",
    "emblem-favorite": "Star",
    "emblem-documents": "Document Multiple",
    "emblem-system": "Settings",
    "emblem-warning": "Warning",
    "emblem-important": "Alert",
    "emblem-ok": "Checkmark Circle",
    "emblem-default": "Circle",
    "emblem-photos": "Image",
    "emblem-mail": "Mail",

    # Status / Feedback
    "bug": "Bug",
    "tools-report-bug": "Bug",
    "warning": "Warning",
    "error": "Error Circle",
    "info": "Info",
    "question": "Help",

    # Text / Code
    "text-x-generic": "Document Text",
    "text-x-script": "Document Code",
    "image-x-generic": "Image",
    "video-x-generic": "Video",
    "audio-x-generic": "Music",

    # List / View
    "list-add": "Add",
    "list-remove": "Delete",
    "view-restore": "Arrow Counterclockwise",
    "view-refresh": "Arrow Clockwise",

    # Window
    "window-close": "Window",
    "window-new": "Window New",

    # Process
    "process-stop": "Stop",
    "utilities-terminal": "Window Console",

    # User
    "user-desktop": "Person",
    "user-trash": "Delete",
    "favorites": "Star",
    "starred": "Star",

    # Download / Upload
    "download": "Arrow Download",
    "upload": "Arrow Upload",

    # Clipboard
    "clipboard-copy": "Clipboard",
    "clipboard-paste": "Clipboard Paste",

    # Misc
    "clock": "Clock",
    "code": "Code",
    "check": "Checkmark",
    "checkmark": "Checkmark",
}


def fluent_to_url_name(fluent_name: str) -> str:
    """Convert Fluent icon name to URL-friendly format (directory name)."""
    return fluent_name


def download_svg(url: str, dest_path: Path) -> bool:
    """Download SVG from URL to destination path."""
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        # URL encode the path part
        parsed = urllib.parse.urlparse(url)
        encoded_path = urllib.parse.quote(parsed.path)
        encoded_url = urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, encoded_path,
            parsed.params, parsed.query, parsed.fragment
        ))
        urllib.request.urlretrieve(encoded_url, dest_path)
        print(f"Downloaded: {dest_path}")
        return True
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return False


def get_fluent_svg_url(fluent_name: str, style: str, size: int = 24) -> str:
    """Generate URL for Fluent icon SVG."""
    # Directory name matches the icon name with spaces
    dir_name = fluent_name

    # Generate filename: ic_fluent_arrow_clockwise_24_regular.svg
    file_name = f"ic_fluent_{fluent_name.lower().replace(' ', '_')}_{size}_{style.lower()}.svg"

    return f"https://raw.githubusercontent.com/microsoft/fluentui-system-icons/main/assets/{dir_name}/SVG/{file_name}"


def main():
    base_dir = Path("/home/putinservai/Project/appimage/Niruvi/asset/icons/fluent")

    # Create directory structure
    dirs = [
        base_dir / "Regular" / "24",
        base_dir / "Filled" / "24",
        base_dir / "light",
        base_dir / "dark",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Track downloaded icons
    downloaded = []
    failed = []

    # Process each freedesktop icon name
    for fd_name, fluent_name in FREEDESKTOP_TO_FLUENT.items():
        # Download Regular (for light theme)
        regular_url = get_fluent_svg_url(fluent_name, "Regular", 24)
        regular_dest = base_dir / "Regular" / "24" / f"{fd_name}.svg"

        if download_svg(regular_url, regular_dest):
            downloaded.append((fd_name, "Regular"))

            # Copy to light theme (not symlink - copy the file)
            light_link = base_dir / "light" / f"{fd_name}.svg"
            try:
                import shutil
                shutil.copy2(regular_dest, light_link)
            except Exception as e:
                print(f"Failed to copy to light theme for {fd_name}: {e}")
        else:
            failed.append((fd_name, "Regular", regular_url))

        # Download Filled (for dark theme)
        filled_url = get_fluent_svg_url(fluent_name, "Filled", 24)
        filled_dest = base_dir / "Filled" / "24" / f"{fd_name}.svg"

        if download_svg(filled_url, filled_dest):
            downloaded.append((fd_name, "Filled"))

            # Copy to dark theme
            dark_link = base_dir / "dark" / f"{fd_name}.svg"
            try:
                import shutil
                shutil.copy2(filled_dest, dark_link)
            except Exception as e:
                print(f"Failed to copy to dark theme for {fd_name}: {e}")
        else:
            failed.append((fd_name, "Filled", filled_url))

    # Create index.theme file
    create_index_theme(base_dir)

    # Summary
    print("\n" + "="*60)
    print(f"Downloaded: {len(downloaded)} icon files")
    print(f"Failed: {len(failed)} icon files")

    if failed:
        print("\nFailed downloads:")
        for fd_name, style, url in failed:
            print(f"  {fd_name} ({style}): {url}")

    print(f"\nIcon theme created at: {base_dir}")


def create_index_theme(base_dir: Path):
    """Create index.theme file for the icon theme."""
    theme_content = """[Icon Theme]
Name=Fluent
Comment=Fluent UI System Icons for Niruvi
Inherits=hicolor,breeze,gnome,Adwaita
Directories=Regular/24,Filled/24,light,dark,scalable/actions,scalable/apps,scalable/categories,scalable/devices,scalable/emblems,scalable/mimetypes,scalable/places,scalable/status

[Regular/24]
Size=24
Context=Actions
Type=Fixed

[Filled/24]
Size=24
Context=Actions
Type=Fixed

[light]
Size=24
Context=Actions
Type=Threshold

[dark]
Size=24
Context=Actions
Type=Threshold

[scalable/actions]
Size=24
Context=Actions
Type=Scalable
MinSize=8
MaxSize=256

[scalable/apps]
Size=24
Context=Applications
Type=Scalable
MinSize=8
MaxSize=256

[scalable/categories]
Size=24
Context=Categories
Type=Scalable
MinSize=8
MaxSize=256

[scalable/devices]
Size=24
Context=Devices
Type=Scalable
MinSize=8
MaxSize=256

[scalable/emblems]
Size=24
Context=Emblems
Type=Scalable
MinSize=8
MaxSize=256

[scalable/mimetypes]
Size=24
Context=MimeTypes
Type=Scalable
MinSize=8
MaxSize=256

[scalable/places]
Size=24
Context=Places
Type=Scalable
MinSize=8
MaxSize=256

[scalable/status]
Size=24
Context=Status
Type=Scalable
MinSize=8
MaxSize=256
"""
    theme_file = base_dir / "index.theme"
    theme_file.write_text(theme_content)
    print(f"Created index.theme at {theme_file}")


if __name__ == "__main__":
    main()