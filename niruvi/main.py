import argparse
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

from PyQt6.QtWidgets import QApplication, QWidget

from niruvi.settings import load_settings, get_data_dir, DEFAULT_INSTALL_DIR, DESKTOP_DIR
from niruvi.manager import AppManager, get_appimage_metadata
from niruvi.wizard import InstallWizard
from niruvi.installation_registry import InstallationRegistry, InstallationRecord
from niruvi.desktop_utils import (
    get_version, create_desktop_entry, find_desktop_for_app,
    find_desktop_shortcut, refresh_desktop_database,
)
from niruvi.worker import extract_appimage_sync
from niruvi._version import __version__
from niruvi.utils import get_icon


def process_appimage(path_str: str, parent=None):
    path = Path(path_str)
    info, icon_data = get_appimage_metadata(str(path))
    app_name = info.get("Name", path.stem)

    if parent is None:
        parent = QWidget()

    registry = InstallationRegistry()
    existing = registry.lookup_by_name(app_name) or registry.lookup_by_path(str(path))
    if existing:
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        from PyQt6.QtGui import QIcon

        dlg = QDialog(parent)
        dlg.setWindowTitle("Already Installed")
        dlg.setFixedSize(400, 200)
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel(f"<b>{app_name}</b> is already installed")
        title.setWordWrap(True)
        layout.addWidget(title)
        desc = QLabel("What would you like to do?")
        layout.addWidget(desc)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_reinstall = QPushButton(get_icon("view-refresh"), "Re-integrate")
        btn_reinstall.clicked.connect(lambda: dlg.done(1))
        btn_remove = QPushButton(get_icon("edit-delete"), "Remove")
        btn_remove.clicked.connect(lambda: dlg.done(2))
        btn_cancel = QPushButton(get_icon("dialog-cancel"), "Cancel")
        btn_cancel.clicked.connect(lambda: dlg.done(0))
        btn_row.addWidget(btn_reinstall)
        btn_row.addWidget(btn_remove)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        reply = dlg.exec()
        if reply == 0:
            return
        elif reply == 2:
            if existing.path and os.path.exists(existing.path):
                shutil.rmtree(existing.path)
            df = find_desktop_for_app(app_name)
            if df and os.path.exists(df):
                os.remove(df)
            sc = find_desktop_shortcut(app_name)
            if sc and os.path.exists(sc):
                os.remove(sc)
            registry.remove(app_name)
            try:
                refresh_desktop_database()
            except Exception:
                pass
            return

    wiz = InstallWizard(str(path), parent, appimage_info=info, icon_data=icon_data)
    wiz.exec()


def cli_install(path_str: str):
    """Silent CLI install without GUI."""
    path = Path(path_str)
    info, icon_data = get_appimage_metadata(str(path))
    app_name = info.get("Name", path.stem)
    install_dir = get_settings()["install_dir"]
    dest_dir = os.path.join(install_dir, app_name)

    registry = InstallationRegistry()
    existing = registry.lookup_by_name(app_name) or registry.lookup_by_path(str(path))
    if existing:
        print(f"Already installed: {app_name}")
        sys.exit(0)

    print(f"Installing {app_name}...", end=" ", flush=True)
    os.makedirs(dest_dir, exist_ok=True)
    extract_appimage_sync(str(path), dest_dir)
    version = get_version(dest_dir)

    metadata = {
        "version": version,
        "install_date": str(Path(dest_dir).stat().st_ctime),
    }
    with open(os.path.join(dest_dir, ".appimage-manager.json"), "w") as f:
        import json
        json.dump(metadata, f)

    desk = create_desktop_entry(dest_dir, app_name)
    record = InstallationRecord(
        name=app_name,
        path=dest_dir,
        version=version,
        desktop_file=desk or "",
        desktop_shortcut="",
    )
    registry.add(record)
    try:
        refresh_desktop_database()
    except Exception:
        pass
    print(f"done (v{version})")


def _resolve_path(raw: str) -> str:
    if raw.startswith("file://"):
        return unquote(urlparse(raw).path)
    return raw


def main():
    parser = argparse.ArgumentParser(
        prog="Niruvi",
        description="Niruvi — Universal Linux AppImage Manager",
    )
    parser.add_argument("file", nargs="?", help="AppImage file to install")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--install", metavar="PATH", help="Install an AppImage (silent, no GUI)")
    parser.add_argument("--uninstall", metavar="APP", help="Uninstall an installed app")
    args = parser.parse_args()

    if args.version:
        print(f"Niruvi v{__version__}")
        sys.exit(0)

    os.makedirs(get_data_dir(), exist_ok=True)
    os.makedirs(DEFAULT_INSTALL_DIR, exist_ok=True)
    os.makedirs(DESKTOP_DIR, exist_ok=True)
    load_settings()

    if args.install:
        raw = _resolve_path(args.install)
        if not os.path.exists(raw):
            print(f"Error: file not found: {raw}", file=sys.stderr)
            sys.exit(1)
        cli_install(raw)
        sys.exit(0)

    if args.uninstall:
        registry = InstallationRegistry()
        record = registry.lookup_by_name(args.uninstall)
        if not record:
            print(f"Error: not installed: {args.uninstall}", file=sys.stderr)
            sys.exit(1)
        if record.path and os.path.exists(record.path):
            shutil.rmtree(record.path)
        df = find_desktop_for_app(args.uninstall)
        if df and os.path.exists(df):
            os.remove(df)
        sc = find_desktop_shortcut(args.uninstall)
        if sc and os.path.exists(sc):
            os.remove(sc)
        registry.remove(args.uninstall)
        try:
            refresh_desktop_database()
        except Exception:
            pass
        print(f"Uninstalled: {args.uninstall}")
        sys.exit(0)

    # --- GUI path ---
    file_to_process = None
    if args.file:
        raw = _resolve_path(args.file)
        p = Path(raw)
        if p.is_file() and p.suffix.lower() == ".appimage":
            file_to_process = str(p)

    app = QApplication(sys.argv)
    app.setApplicationName("Niruvi")
    app.setApplicationVersion(__version__)

    icon_path = None
    appdir_env = os.environ.get("APPDIR")
    if appdir_env:
        for name in ("niruvi.png", "niruvi.svg"):
            p = os.path.join(appdir_env, name)
            if os.path.exists(p):
                icon_path = p
                break
    if not icon_path:
        icon_dir = os.environ.get("NIRUVI_ICON_DIR")
        if icon_dir:
            appdir = os.path.dirname(os.path.dirname(icon_dir))
            for name in ("niruvi.png", "niruvi.svg"):
                p = os.path.join(appdir, name)
                if os.path.exists(p):
                    icon_path = p
                    break
    if not icon_path:
        asset_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "asset")
        for name in ("niruvi.png", "niruvi.svg"):
            p = os.path.join(asset_dir, name)
            if os.path.exists(p):
                icon_path = p
                break
    if icon_path:
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon_path))

    if file_to_process:
        process_appimage(file_to_process)

    window = AppManager()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
