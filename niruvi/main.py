import argparse
import gc
import logging
import logging.handlers
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from PyQt6.QtWidgets import QApplication, QWidget

from niruvi._version import __version__
from niruvi.config import (
    DEFAULT_INSTALL_DIR,
    DESKTOP_DIR,
    INSTALLED_DIR,
    _settings,
    configure_runtime_env,
    get_data_dir,
    get_settings,
    load_settings,
)
from niruvi.desktop.desktop_utils import find_desktop_for_app, find_desktop_shortcut, refresh_desktop_database
from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry
from niruvi.utils.qt_compat import fix_qt_platform_path as _fix_qt_platform_path


def process_appimage(path_str: str, parent=None):
    from niruvi.ui.manager import get_appimage_metadata
    from niruvi.ui.wizard import InstallWizard
    from niruvi.utils import get_icon

    path = Path(path_str)
    info, icon_data = get_appimage_metadata(str(path))
    app_name = info.get("Name", path.stem)

    if parent is None:
        parent = QApplication.activeWindow() or QWidget()

    registry = InstallationRegistry()
    existing = registry.lookup_by_name(app_name) or registry.lookup_by_path(str(path))
    if existing:
        from PyQt6.QtWidgets import QDialog, QGridLayout, QLabel, QPushButton, QVBoxLayout

        dlg = QDialog(parent)
        dlg.setWindowTitle("Already Installed")
        dlg.setFixedSize(480, 260)
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = QLabel(f"<b>{app_name}</b> is already installed")
        title.setWordWrap(True)
        layout.addWidget(title)
        desc = QLabel("What would you like to do?")
        layout.addWidget(desc)
        layout.addStretch()

        btn_grid = QGridLayout()
        btn_grid.setSpacing(8)
        btn_reinstall = QPushButton(get_icon("view-refresh"), "Re-integrate")
        btn_reinstall.clicked.connect(lambda: dlg.done(1))
        btn_grid.addWidget(btn_reinstall, 0, 0)
        btn_sbs = QPushButton(get_icon("list-add"), "Install Side-by-Side")
        btn_sbs.clicked.connect(lambda: dlg.done(3))
        btn_grid.addWidget(btn_sbs, 0, 1)
        btn_remove = QPushButton(get_icon("edit-delete"), "Remove")
        btn_remove.clicked.connect(lambda: dlg.done(2))
        btn_grid.addWidget(btn_remove, 1, 0)
        btn_cancel = QPushButton(get_icon("dialog-cancel"), "Cancel")
        btn_cancel.clicked.connect(lambda: dlg.done(0))
        btn_grid.addWidget(btn_cancel, 1, 1)
        layout.addLayout(btn_grid)

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


def cli_install(path_str: str, dest_override: str | None = None):
    """Silent CLI install without GUI."""
    from niruvi.core.worker import extract_appimage_sync
    from niruvi.desktop.desktop_utils import create_desktop_entry, get_version
    from niruvi.installer.junest import JunestPathError, suggest_space_free_path
    from niruvi.ui.manager import get_appimage_metadata

    path = Path(path_str)
    info, _icon_data = get_appimage_metadata(str(path))
    app_name = info.get("Name", path.stem)
    install_dir = get_settings()["install_dir"]
    dest_dir = dest_override or os.path.join(install_dir, app_name)

    registry = InstallationRegistry()
    existing = registry.lookup_by_name(app_name) or registry.lookup_by_path(str(path))
    if existing:
        print(f"Already installed: {app_name}")
        sys.exit(0)

    print(f"Installing {app_name}...", end=" ", flush=True)
    os.makedirs(dest_dir, exist_ok=True)
    try:
        extract_appimage_sync(str(path), dest_dir)
    except JunestPathError as e:
        if dest_override:
            print(f"\nError: {e}", file=sys.stderr)
            print("The destination was chosen explicitly; reinstall with a space-free path.", file=sys.stderr)
            sys.exit(1)
        dest_dir = e.suggested_path or suggest_space_free_path(dest_dir)
        print(f"\nJuNest container requires a space-free path — installing to {dest_dir}")
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
    registry.flush()
    try:
        refresh_desktop_database()
    except Exception:
        pass
    print(f"done (v{version})")


def _resolve_path(raw: str) -> str:
    if raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)
    if "\0" in raw:
        raise ValueError("Path contains null byte")
    if ".." in raw.split(os.sep):
        raise ValueError(f"Path contains '..' traversal: {raw}")
    resolved = os.path.realpath(raw)
    if "\0" in resolved:
        raise ValueError("Resolved path contains null byte")
    return resolved


def _configure_logging():
    """Configure application-wide logging to a rotating file + stderr.

    Without this, the project's many ``logger.debug/info`` calls are silently
    dropped (Python's "last resort" handler only prints WARNING+ to stderr).
    """
    log_dir = Path(os.path.join(get_data_dir(), "logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    log_file = log_dir / "niruvi.log"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(
            logging.handlers.RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        )
    except OSError:
        pass
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )


def main():
    # Silence benign Qt/KDE/FFmpeg log noise and disable FFmpeg hardware decode
    # probing (triggers the libvdpau_nvidia.so warning) BEFORE QApplication.
    configure_runtime_env()
    _configure_logging()

    parser = argparse.ArgumentParser(
        prog="Niruvi",
        description="Niruvi — Universal Linux AppImage Manager",
    )
    parser.add_argument("file", nargs="?", help="AppImage file to open/manage")
    parser.add_argument(
        "--open", metavar="PATH", help="Open an AppImage file in the manager (for file association handlers)"
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--install", metavar="PATH", help="Install an AppImage (silent, no GUI)")
    parser.add_argument(
        "--dest",
        metavar="DIR",
        help="Install destination directory (overrides install_dir + app name)",
    )
    parser.add_argument("--uninstall", metavar="APP", help="Uninstall an installed app")
    parser.add_argument("--list", action="store_true", help="List installed apps (CLI)")
    parser.add_argument("--update-all", action="store_true", help="Check all apps for updates (CLI)")
    parser.add_argument("--update-check", metavar="APP", help="Check a specific app for updates (CLI)")
    parser.add_argument("--is-installed", metavar="PATH", help="Check if an AppImage is installed (CLI)")
    parser.add_argument(
        "--run",
        metavar="APP",
        help="Launch an installed app headlessly, applying its sandbox config "
        "(used by desktop entries; extra args go after '--')",
    )
    parser.add_argument(
        "--unsandboxed",
        action="store_true",
        help="With --run: launch without applying the app's sandbox",
    )
    parser.add_argument("--mute", action="store_true", help="Disable all sound effects")
    args, app_args = parser.parse_known_args()
    if app_args and app_args[0] == "--":
        app_args = app_args[1:]

    if args.version:
        print(f"Niruvi v{__version__}")
        sys.exit(0)

    os.makedirs(get_data_dir(), exist_ok=True)
    os.makedirs(DEFAULT_INSTALL_DIR, exist_ok=True)
    os.makedirs(DESKTOP_DIR, exist_ok=True)
    load_settings()

    if args.mute:
        _settings["sound_effects_enabled"] = False

    if args.install:
        try:
            raw = _resolve_path(args.install)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        if not os.path.exists(raw):
            print(f"Error: file not found: {raw}", file=sys.stderr)
            sys.exit(1)
        dest = os.path.realpath(os.path.expanduser(args.dest)) if args.dest else None
        cli_install(raw, dest)
        sys.exit(0)

    if args.uninstall:
        registry = InstallationRegistry()
        record = registry.lookup_by_name(args.uninstall)
        if not record:
            print(f"Error: not installed: {args.uninstall}", file=sys.stderr)
            sys.exit(1)
        if record.path and os.path.exists(record.path):
            shutil.rmtree(record.path)
        for suffix in (".home", ".config", ".prev"):
            side_dir = record.path + suffix
            if os.path.isdir(side_dir):
                shutil.rmtree(side_dir, ignore_errors=True)
        df = find_desktop_for_app(args.uninstall)
        if df and os.path.exists(df):
            os.remove(df)
        sc = find_desktop_shortcut(args.uninstall)
        if sc and os.path.exists(sc):
            os.remove(sc)
        registry.remove(args.uninstall)
        registry.flush()
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
        from niruvi.app.self_update import compare_versions
        from niruvi.app.update_sources import resolve_update_source

        updates = []
        for name, url, ver in apps:
            try:
                info = resolve_update_source(url, ver)
                if info and info.version and compare_versions(info.version, "gt", ver):
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
        from niruvi.app.self_update import compare_versions
        from niruvi.app.update_sources import resolve_update_source

        try:
            info = resolve_update_source(record.update_url, record.version)
            if info and info.version and compare_versions(info.version, "gt", record.version):
                print(f"{record.name}: {record.version} -> {info.version} (update available)")
            else:
                print(f"{record.name}: {record.version} (up to date)")
        except Exception as e:
            print(f"Error checking updates: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if args.is_installed:
        try:
            raw = _resolve_path(args.is_installed)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        registry = InstallationRegistry()
        record = registry.lookup_by_path(raw) or registry.lookup_by_name(Path(raw).stem)
        if record:
            print(f"Installed: {record.name} v{record.version}")
            sys.exit(0)
        else:
            print("Not installed.")
            sys.exit(1)

    if args.run:
        from niruvi.launcher import run_app_headless

        sys.exit(run_app_headless(args.run, app_args))

    # --- GUI path ---
    file_to_process = None
    src = args.open or args.file
    if src:
        try:
            raw = _resolve_path(src)
        except ValueError:
            pass
        else:
            p = Path(raw)
            if (p.is_file() and p.suffix.lower() in (".appimage", ".AppImage")) or p.is_file():
                file_to_process = str(p)

    _fix_qt_platform_path()
    app = QApplication(sys.argv)
    from niruvi.utils import _init_icon_theme

    _init_icon_theme()
    app.setApplicationName("Niruvi")
    app.setApplicationVersion(__version__)

    # Set restrictive permissions on data directory
    data_dir = get_data_dir()
    try:
        os.makedirs(data_dir, exist_ok=True)
        os.chmod(data_dir, 0o700)
    except OSError:
        pass

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

    from niruvi.ui.manager import AppManager

    window = AppManager()
    window.show()
    ret = app.exec()
    window.close()
    from niruvi.core.worker import wait_for_workers

    wait_for_workers(10000)
    for _ in range(3):
        gc.collect()
    for _attr in ("last_exc", "last_value", "last_traceback", "last_type"):
        try:
            setattr(sys, _attr, None)
        except AttributeError:
            pass
    for _ in range(3):
        gc.collect()
    window = None
    app = None
    from niruvi.utils.sound_manager import cleanup as _sound_cleanup

    _sound_cleanup()
    sys.exit(ret)


if __name__ == "__main__":
    main()
