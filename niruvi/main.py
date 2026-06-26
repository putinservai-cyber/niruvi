import argparse
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

from PyQt6.QtWidgets import QApplication, QWidget

from niruvi.settings import load_settings, get_settings, get_data_dir, DEFAULT_INSTALL_DIR, INSTALLED_DIR, DESKTOP_DIR
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
        parent = QApplication.activeWindow() or QWidget()

    registry = InstallationRegistry()
    existing = registry.lookup_by_name(app_name) or registry.lookup_by_path(str(path))
    if existing:
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        from PyQt6.QtGui import QIcon

        dlg = QDialog(parent)
        dlg.setWindowTitle("Already Installed")
        dlg.setFixedSize(420, 240)
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
        btn_sbs = QPushButton(get_icon("list-add"), "Install Side-by-Side")
        btn_sbs.clicked.connect(lambda: dlg.done(3))
        btn_remove = QPushButton(get_icon("edit-delete"), "Remove")
        btn_remove.clicked.connect(lambda: dlg.done(2))
        btn_cancel = QPushButton(get_icon("dialog-cancel"), "Cancel")
        btn_cancel.clicked.connect(lambda: dlg.done(0))
        btn_row.addWidget(btn_reinstall)
        btn_row.addWidget(btn_sbs)
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
        elif reply == 3:
            suffix = 2
            base_name = app_name
            while registry.lookup_by_name(base_name):
                base_name = f"{app_name}-{suffix}"
                suffix += 1
            app_name = base_name
            info = info.copy() if info else {}
            info["Name"] = app_name

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
    # Suppress Qt Wayland debug noise ("plugin supports grabbing the mouse only for popup windows")
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.wayland.warning=false")

    parser = argparse.ArgumentParser(
        prog="Niruvi",
        description="Niruvi — Universal Linux AppImage Manager",
    )
    parser.add_argument("file", nargs="?", help="AppImage file to install")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--install", metavar="PATH", help="Install an AppImage (silent, no GUI)")
    parser.add_argument("--uninstall", metavar="APP", help="Uninstall an installed app")
    parser.add_argument("--list", action="store_true", help="List installed apps (CLI)")
    parser.add_argument("--update-all", action="store_true", help="Check all apps for updates (CLI)")
    parser.add_argument("--update-check", metavar="APP", help="Check a specific app for updates (CLI)")
    parser.add_argument("--is-installed", metavar="PATH", help="Check if an AppImage is installed (CLI)")
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

    if args.list:
        registry = InstallationRegistry()
        records = registry.get_all()
        if not records:
            print("No apps installed.")
        else:
            print(f"{'Name':<30} {'Version':<20} {'Path'}")
            print("-" * 80)
            for r in sorted(records, key=lambda x: x.name.lower()):
                print(f"{r.name:<30} {r.version:<20} {r.path}")
        sys.exit(0)

    if args.update_all:
        registry = InstallationRegistry()
        records = registry.get_all()
        apps = [(r.name, r.update_url, r.version) for r in records if r.update_url]
        if not apps:
            print("No apps with update URLs configured.")
            sys.exit(0)
        print(f"Checking {len(apps)} app(s) for updates...")
        from niruvi.update_sources import resolve_update_source
        from niruvi.self_update import compare_versions
        updates = []
        for name, url, ver in apps:
            try:
                info = resolve_update_source(url, ver)
                if info and info.version and compare_versions(info.version, 'gt', ver):
                    updates.append((name, ver, info.version, info.download_url))
                    print(f"  {name}: {ver} -> {info.version} (update available)")
                else:
                    print(f"  {name}: {ver} (up to date)")
            except Exception as e:
                print(f"  {name}: error - {e}")
        if updates:
            print(f"\n{len(updates)} update(s) available. Use 'niruvi' GUI to install them.")
        else:
            print("\nAll apps are up to date.")
        sys.exit(0)

    if args.update_check:
        registry = InstallationRegistry()
        record = registry.lookup_by_name(args.update_check)
        if not record:
            print(f"Error: '{args.update_check}' is not installed.", file=sys.stderr)
            sys.exit(1)
        if not record.update_url:
            print(f"No update URL configured for '{args.update_check}'.")
            sys.exit(0)
        from niruvi.update_sources import resolve_update_source
        from niruvi.self_update import compare_versions
        try:
            info = resolve_update_source(record.update_url, record.version)
            if info and info.version and compare_versions(info.version, 'gt', record.version):
                print(f"{record.name}: {record.version} -> {info.version} (update available)")
            else:
                print(f"{record.name}: {record.version} (up to date)")
        except Exception as e:
            print(f"Error checking updates: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if args.is_installed:
        raw = _resolve_path(args.is_installed)
        registry = InstallationRegistry()
        record = registry.lookup_by_path(raw) or registry.lookup_by_name(Path(raw).stem)
        if record:
            print(f"Installed: {record.name} v{record.version}")
            sys.exit(0)
        else:
            print("Not installed.")
            sys.exit(1)

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
        for d in (INSTALLED_DIR, os.path.dirname(os.path.dirname(__file__))):
            for name in ("niruvi.png", "niruvi.svg"):
                p = os.path.join(d, name)
                if os.path.exists(p):
                    icon_path = p
                    break
            if icon_path:
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
