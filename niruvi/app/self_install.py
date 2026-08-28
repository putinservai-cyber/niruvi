"""Self-install wizard for first run of Niruvi AppImage.

Detects when running from an AppImage that hasn't been installed yet,
prompts the user to install, and handles the extraction + desktop integration.
"""

import logging
import os
import sys

_DETACHED: list = []

_log = logging.getLogger(__name__)

try:
    from niruvi.utils.sound_manager import play as play_sound
except ImportError:

    def play_sound(sound_name: str) -> None:
        pass


from PyQt6.QtWidgets import QApplication


def _fix_qt_platform_path():
    from niruvi.utils.qt_compat import fix_qt_platform_path

    fix_qt_platform_path()


from niruvi._version import __app_name__, __version__

INSTALL_DIR = os.path.expanduser("~/Applications/Niruvi")


def _is_installed():
    return os.path.isdir(INSTALL_DIR) and os.path.isfile(os.path.join(INSTALL_DIR, "AppRun"))


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
        # First-run uses the Windows-style setup wizard (a Next/Next/Finish
        # flow). The wizard installs Niruvi itself and launches the installed
        # copy on success.
        try:
            from PyQt6.QtWidgets import QDialog

            from niruvi.ui.wizard import InstallWizard

            wiz = InstallWizard(appimage, None)
            wiz.setWindowTitle(f"Install {__app_name__}")
            play_sound("navigation")
            if wiz.exec() == QDialog.DialogCode.Accepted:
                # InstallWizard.accept() launches the freshly installed copy, so
                # exit here rather than also starting the portable instance.
                sys.exit(0)
        except Exception as e:
            _log.warning("Self-install wizard failed, falling back to portable mode: %s", e, exc_info=True)

        # Reuse the existing QApplication — do NOT quit and recreate
        from niruvi.config import DEFAULT_INSTALL_DIR, DESKTOP_DIR, get_data_dir, load_settings
        from niruvi.ui.manager import Niruvi

        os.makedirs(get_data_dir(), exist_ok=True)
        os.makedirs(DEFAULT_INSTALL_DIR, exist_ok=True)
        os.makedirs(DESKTOP_DIR, exist_ok=True)
        load_settings()

        window = Niruvi()
        window.show()
        exit_code = app.exec()
        window.close()
        app.quit()
        os._exit(exit_code)

    from niruvi.main import main

    main()


if __name__ == "__main__":
    run_self_install()
