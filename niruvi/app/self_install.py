"""Self-install wizard for first run of Niruvi AppImage.

Detects when running from an AppImage that hasn't been installed yet,
prompts the user to install, and handles the extraction + desktop integration.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile

_DETACHED: list[subprocess.Popen] = []

_log = logging.getLogger(__name__)

try:
    from niruvi.utils.sound_manager import play as play_sound
except ImportError:

    def play_sound(sound_name: str) -> None:
        pass


from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QMessageBox,
    QProgressDialog,
)


def _fix_qt_platform_path():
    from niruvi.utils.qt_compat import fix_qt_platform_path

    fix_qt_platform_path()


def _copy_tree_resilient(src: str, dst: str) -> None:
    """Copy a directory tree, skipping files that cannot be read.

    Unlike shutil.copytree this tolerates broken symlinks and permission
    errors so that a partial extraction never crashes the installer.
    """
    os.makedirs(dst, exist_ok=True)
    for entry in os.scandir(src):
        s = entry.path
        d = os.path.join(dst, entry.name)
        try:
            if entry.is_symlink():
                target = os.readlink(s)
                if os.path.exists(s):
                    if os.path.isdir(s):
                        _copy_tree_resilient(s, d)
                    else:
                        shutil.copy2(s, d)
                else:
                    # Broken symlink — replicate as-is
                    os.symlink(target, d)
            elif entry.is_dir(follow_symlinks=False):
                _copy_tree_resilient(s, d)
            elif entry.is_file(follow_symlinks=False):
                shutil.copy2(s, d)
        except OSError as exc:
            _log.warning("Skipping %s: %s", s, exc)


from niruvi._version import __app_name__, __version__
from niruvi.desktop.desktop_utils import (
    create_desktop_entry,
    install_icon_to_theme,
    refresh_desktop_database,
    register_mime_handler,
)

INSTALL_DIR = os.path.expanduser("~/Applications/Niruvi")


def _is_installed():
    return os.path.isdir(INSTALL_DIR) and os.path.isfile(os.path.join(INSTALL_DIR, "AppRun"))


OLD_CONFIG_DIR = os.path.expanduser("~/.config/niruvi")


def _migrate_old_data(data_dir: str):
    """Migrate settings + registry from ~/.config/niruvi/ to the new data dir."""
    if not os.path.isdir(OLD_CONFIG_DIR):
        return
    for fname in ("settings.json", "registry.json", "registry.db"):
        src = os.path.join(OLD_CONFIG_DIR, fname)
        dst = os.path.join(data_dir, fname)
        if os.path.isfile(src) and not os.path.isfile(dst):
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass


def _self_extract(appimage_path, dest_dir):
    """Extract the AppImage to dest_dir using --appimage-extract."""
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(os.path.dirname(dest_dir), exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="niruvi-self-") as tmp:
        proc = subprocess.Popen(
            [appimage_path, "--appimage-extract"],
            cwd=tmp,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _stdout, stderr = proc.communicate(timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"Extraction failed: {stderr.strip()}")

        extracted = os.path.join(tmp, "squashfs-root")
        if not os.path.isdir(extracted):
            dirs = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
            if dirs:
                extracted = os.path.join(tmp, dirs[0])
            else:
                raise RuntimeError("No extracted directory found.")

        _copy_tree_resilient(extracted, dest_dir)

    apprun = os.path.join(dest_dir, "AppRun")
    if os.path.isfile(apprun):
        os.chmod(apprun, 0o755)

    # Create .niruvi/ data directory inside the install location
    data_dir = os.path.join(dest_dir, ".niruvi")
    os.makedirs(data_dir, exist_ok=True)
    _migrate_old_data(data_dir)


def _create_self_desktop_entry():
    """Create desktop integration for the installed copy."""
    for name in ("niruvi.png", "niruvi.svg"):
        icon_path = os.path.join(INSTALL_DIR, name)
        if os.path.exists(icon_path):
            install_icon_to_theme(icon_path, __app_name__)
            break
    desktop_file = create_desktop_entry(INSTALL_DIR, __app_name__)
    register_mime_handler(__app_name__)
    refresh_desktop_database()
    return desktop_file


def run_self_install():
    """Entry point: detect first run, optionally install, then launch GUI.

    Creates a single QApplication and reuses it — never creates two.
    """
    from niruvi.config import configure_runtime_env

    configure_runtime_env()
    appimage = os.environ.get("APPIMAGE")

    if appimage and not _is_installed():
        has_cli_flags = any(a in sys.argv for a in ("--help", "-h", "--version", "--install", "--uninstall"))
        if has_cli_flags:
            from niruvi.main import main

            main()
            return

        _fix_qt_platform_path()
        app = QApplication(sys.argv)
        app.setApplicationName(__app_name__)
        app.setApplicationVersion(__version__)

        try:
            from niruvi.utils.sound_manager import play as play_sound

            play_sound("interface")
        except ImportError:
            pass
        reply = QMessageBox.question(
            None,
            f"Install {__app_name__}",
            f"<b>{__app_name__}</b> is running as a portable AppImage and is not "
            f"installed on your system.<br><br>"
            f"Would you like to install it to <code>~/Applications/Niruvi/</code> "
            f"and create a desktop launcher?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            progress = QProgressDialog(
                f"Installing {__app_name__}...",
                None,
                0,
                0,
                None,
            )
            progress.setWindowTitle("Installing")
            progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            progress.show()
            QApplication.processEvents()

            try:
                _self_extract(appimage, INSTALL_DIR)
                progress.setLabelText("Creating desktop entries...")
                QApplication.processEvents()
                _create_self_desktop_entry()

                progress.close()
                play_sound("success")
                QMessageBox.information(
                    None,
                    "Installation Complete",
                    f"<b>{__app_name__}</b> has been installed to "
                    f"<code>~/Applications/Niruvi/</code>.<br><br>"
                    f"It will now launch from the installed location.",
                )

                p = subprocess.Popen(
                    [os.path.join(INSTALL_DIR, "AppRun")],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                import time

                time.sleep(1.5)
                if p.poll() is not None and p.returncode != 0:
                    # Installed launch failed — show AppManager from here
                    progress.close()
                    play_sound("warning")
                    QMessageBox.warning(
                        None,
                        "Launch Issue",
                        "Niruvi was installed but the launched instance exited "
                        "unexpectedly. The portable instance will open instead.",
                    )
                else:
                    _DETACHED.append(p)
                    sys.exit(0)
            except Exception as e:
                progress.close()
                play_sound("error")
                QMessageBox.critical(
                    None,
                    "Installation Failed",
                    f"Could not install {__app_name__}:<br><code>{e}</code><br><br>"
                    f"The AppImage will run in portable mode instead.",
                )
        else:
            app.quit()
            os._exit(0)

        # Reuse the existing QApplication — do NOT quit and recreate
        from niruvi.config import DEFAULT_INSTALL_DIR, DESKTOP_DIR, get_data_dir, load_settings
        from niruvi.ui.manager import AppManager

        os.makedirs(get_data_dir(), exist_ok=True)
        os.makedirs(DEFAULT_INSTALL_DIR, exist_ok=True)
        os.makedirs(DESKTOP_DIR, exist_ok=True)
        load_settings()

        window = AppManager()
        window.show()
        exit_code = app.exec()
        window.close()
        app.quit()
        os._exit(exit_code)

    from niruvi.main import main

    main()


if __name__ == "__main__":
    run_self_install()
