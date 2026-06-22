"""Self-install wizard for first run of Niruvi AppImage.

Detects when running from an AppImage that hasn't been installed yet,
prompts the user to install, and handles the extraction + desktop integration.
"""

import os
import shutil
import subprocess
import sys
import tempfile

from PyQt6.QtWidgets import (
    QApplication, QMessageBox, QProgressDialog,
)
from PyQt6.QtCore import Qt

from niruvi.desktop_utils import (
    create_desktop_entry, install_icon_to_theme, refresh_desktop_database,
)
from niruvi._version import __app_name__, __version__


INSTALL_DIR = os.path.expanduser("~/Applications/Niruvi")


def _is_installed():
    return os.path.isdir(INSTALL_DIR) and os.path.isfile(os.path.join(INSTALL_DIR, "AppRun"))


OLD_CONFIG_DIR = os.path.expanduser("~/.config/niruvi")


def _migrate_old_data(data_dir: str):
    """Migrate settings + registry from ~/.config/niruvi/ to the new data dir."""
    if not os.path.isdir(OLD_CONFIG_DIR):
        return
    for fname in ("settings.json", "registry.json"):
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
        stdout, stderr = proc.communicate(timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"Extraction failed: {stderr.strip()}")

        extracted = os.path.join(tmp, "squashfs-root")
        if not os.path.isdir(extracted):
            dirs = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
            if dirs:
                extracted = os.path.join(tmp, dirs[0])
            else:
                raise RuntimeError("No extracted directory found.")

        shutil.copytree(extracted, dest_dir, dirs_exist_ok=True)

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
    refresh_desktop_database()
    return desktop_file


def run_self_install():
    """Entry point: detect first run, optionally install, then launch GUI.

    This function creates its own QApplication if one doesn't exist yet.
    """
    from niruvi.main import main

    appimage = os.environ.get("APPIMAGE")

    if appimage and not _is_installed():
        has_cli_flags = any(a in sys.argv for a in ("--help", "-h", "--version", "--install", "--uninstall"))
        if has_cli_flags:
            main()
            return

        app = QApplication(sys.argv)
        app.setApplicationName(__app_name__)
        app.setApplicationVersion(__version__)

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
                f"Installing {__app_name__}...", None, 0, 0, None,
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

                QMessageBox.information(
                    None,
                    "Installation Complete",
                    f"<b>{__app_name__}</b> has been installed to "
                    f"<code>~/Applications/Niruvi/</code>.<br><br>"
                    f"It will now launch from the installed location.",
                )

                subprocess.Popen([os.path.join(INSTALL_DIR, "AppRun")])
                sys.exit(0)
            except Exception as e:
                progress.close()
                QMessageBox.critical(
                    None,
                    "Installation Failed",
                    f"Could not install {__app_name__}:<br><code>{e}</code><br><br>"
                    f"The AppImage will run in portable mode instead.",
                )

        app.quit()

    main()


if __name__ == "__main__":
    run_self_install()
