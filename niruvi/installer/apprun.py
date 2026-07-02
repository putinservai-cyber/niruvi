from niruvi.installer.sanitize import sanitize_bash_string


def apprun_appimagelauncher_detection() -> str:
    return '''# ── AppImageLauncher bypass ──────────────────────────────
# Force-disable AppImageLauncher immediately, before it can intercept.
# This must be set before any command that could trigger AIL.
export APPIMAGE_LAUNCHER_DISABLE=1

# Unset APPIMAGE so launcher-wrapping detection doesn't find us
unset APPIMAGE

# Detect if AppImageLauncher was already active in our parent chain.
# If so, warn the user that they should rename to .run instead.
_ail_detected=0
if command -v ps &>/dev/null; then
    _ail_pid=$PPID
    for _i in 1 2 3; do
        _ail_name=$(ps -o comm= "$_ail_pid" 2>/dev/null | tr '[:upper:]' '[:lower:]')
        [ -z "$_ail_name" ] && break
        case "$_ail_name" in
            *appimagelauncher*) _ail_detected=1; break ;;
        esac
        _ail_pid=$(ps -o ppid= "$_ail_pid" 2>/dev/null | tr -d ' ')
        [ -z "$_ail_pid" ] && break
    done
fi

if [ "$_ail_detected" = "1" ]; then
    _ail_msg="This AppImage has its own installation wizard.
AppImageLauncher has been bypassed for this session.

To avoid this message entirely, rename this file
from .AppImage to .run (for example MyAppInstaller.run)
so AppImageLauncher will ignore it entirely."
    if command -v zenity &>/dev/null; then
        zenity --info --title="AppImageLauncher Bypassed" --text="$_ail_msg" 2>/dev/null
    elif command -v kdialog &>/dev/null; then
        kdialog --msgbox "$_ail_msg" --title "AppImageLauncher Bypassed" 2>/dev/null
    fi
fi
unset _ail_detected _ail_pid _ail_name _ail_msg _i
# ──────────────────────────────────────────────────────────────
'''


def apprun_common_header(safe_name: str, safe_ver: str) -> str:
    return f'''HERE="$(dirname "$(readlink -f "$0")")"
APP_NAME="{safe_name}"
APP_VERSION="{safe_ver}"

INSTALL_DIR="${{INSTALL_DIR:-$HOME/Applications/$APP_NAME}}"
MARKER="$INSTALL_DIR/.installed"
META="$INSTALL_DIR/.appimage-manager.json"
'''


def apprun_common_help() -> str:
    return '''# ── Version / Help ──
if [ "$1" = "--version" ] || [ "$1" = "-v" ]; then
    INSTALLED_VER="?"
    if [ -f "$META" ]; then
        INSTALLED_VER=$(python3 -c "import json; print(json.load(open('$META')).get('version','?'))" 2>/dev/null || echo "?")
    fi
    echo "$APP_NAME $APP_VERSION (installed: $INSTALLED_VER)"
    exit 0
fi

if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "$APP_NAME $APP_VERSION — Self-Installing Application"
    echo ""
    echo "Usage: $APP_NAME [options]"
    echo ""
    echo "Options:"
    echo "  --help, -h       Show this help"
    echo "  --version, -v    Show version"
    echo "  --install        Run the installer"
    echo "  --uninstall      Run the uninstaller"
    echo "  --update         Check for and apply updates"
    echo "  --check-updates  Silently check for updates"
    echo ""
    echo "This AppImage uses a custom self-installer."
    echo "Run without arguments to install or launch."
    exit 0
fi
'''


def apprun_common_gui_functions() -> str:
    return '''
_gui_available() {
    command -v zenity &>/dev/null || command -v kdialog &>/dev/null
}

_msg() {
    local title="$1" text="$2" kind="$3"
    if command -v zenity &>/dev/null; then
        zenity --"$kind" --title="$title" --text="$text" 2>/dev/null
    elif command -v kdialog &>/dev/null; then
        local kk
        case "$kind" in info) kk="msgbox" ;; warning|warn) kk="sorry" ;; error) kk="error" ;; *) kk="msgbox" ;; esac
        kdialog --"$kk" "$text" --title "$title" 2>/dev/null
    else
        echo ""
        echo "  $title"
        echo "  $text"
        echo ""
    fi
}

_confirm() {
    local title="$1" text="$2"
    if command -v zenity &>/dev/null; then
        zenity --question --title="$title" --text="$text" --width=400 2>/dev/null
    elif command -v kdialog &>/dev/null; then
        kdialog --yesno "$text" --title "$title" 2>/dev/null
    else
        echo ""
        echo "  $title"
        echo "  $text"
        read -p "  [Y/n]: " REPLY
        [ "$REPLY" != "n" ] && [ "$REPLY" != "N" ]
    fi
}
'''


def apprun_update_check_section() -> str:
    return '''
# ── Check for update (auto-prompt on version mismatch) ──
if [ -f "$MARKER" ] && [ -f "$META" ] && [ -n "$APP_VERSION" ]; then
    INSTALLED_VER=$(python3 -c "import json; print(json.load(open('$META')).get('version',''))" 2>/dev/null || echo "")
    if [ -n "$INSTALLED_VER" ] && [ "$INSTALLED_VER" != "$APP_VERSION" ]; then
        if _confirm "Update Available" \\
"Version $APP_VERSION is available (installed: $INSTALLED_VER).

Update now?"; then
            if [ -f "$HERE/.niruvi-install/update.sh" ]; then
                exec "$HERE/.niruvi-install/update.sh" "$@"
            else
                exec "$HERE/.niruvi-install/install.sh" "$@"
            fi
            exit 0
        fi
    fi
fi

# ── Background update check (non-blocking, runs every 7 days) ──
if [ -f "$META" ] && [ -f "$HERE/.niruvi-install/update.sh" ]; then
    LAST_CHECK=$(python3 -c "import json; print(json.load(open('$META')).get('last_update_check',0))" 2>/dev/null || echo "0")
    NOW=$(date +%s)
    INTERVAL=$((7 * 86400))
    if [ $((NOW - LAST_CHECK)) -gt $INTERVAL ] 2>/dev/null; then
        "$HERE/.niruvi-install/update.sh" --check-silent &
    fi
fi
'''


def apprun_bash_content() -> str:
    return apprun_common_gui_functions() + apprun_common_help() + '''
if [ "$1" = "--uninstall" ]; then
    exec "$HERE/.niruvi-install/uninstall.sh" "$@"
    exit 0
fi

if [ "$1" = "--install" ]; then
    exec "$HERE/.niruvi-install/install.sh" "$@"
    exit 0
fi

if [ "$1" = "--update" ]; then
    if [ -f "$HERE/.niruvi-install/update.sh" ]; then
        exec "$HERE/.niruvi-install/update.sh" "$@"
    else
        _msg "No Updater" "This AppImage was built without an updater." "warning"
        exit 1
    fi
    exit 0
fi

if [ "$1" = "--check-updates" ]; then
    if [ -f "$HERE/.niruvi-install/update.sh" ]; then
        exec "$HERE/.niruvi-install/update.sh" --check-only "$@"
    fi
    exit 0
fi
''' + apprun_update_check_section() + '''
# ── Launch installed or run installer ──
if [ -f "$MARKER" ] && [ -f "$INSTALL_DIR/AppRun" ]; then
    exec "$INSTALL_DIR/AppRun" "$@"
fi

exec "$HERE/.niruvi-install/install.sh" "$@"
'''


def apprun_qt6_content() -> str:
    return '''
WIZARD="$HERE/.niruvi-install/self_install_wizard.py"

# Save original LD_LIBRARY_PATH for installed app launch
_SAVED_LD_LIBRARY_PATH="$LD_LIBRARY_PATH"

# Remove bundle lib paths so system python3/PyQt6 finds system Qt6
unset LD_LIBRARY_PATH

# Fix empty QT_QPA_PLATFORM_PLUGIN_PATH set by AppImage runtime
if [ -z "$QT_QPA_PLATFORM_PLUGIN_PATH" ] || [ ! -d "$QT_QPA_PLATFORM_PLUGIN_PATH" ]; then
    unset QT_QPA_PLATFORM_PLUGIN_PATH
fi

_pyqt6_available() {
    python3 -c "from PyQt6.QtWidgets import QApplication" 2>/dev/null
}

''' + apprun_common_gui_functions() + apprun_common_help() + '''
# ── CLI mode with PyQt6 wizard (falls back to bash if wizard fails) ──
if [ "$1" = "--uninstall" ]; then
    if _pyqt6_available && [ -f "$WIZARD" ]; then
        python3 "$WIZARD" "--uninstall" && exit 0
    fi
    exec "$HERE/.niruvi-install/uninstall.sh" "$@"
    exit 0
fi

if [ "$1" = "--install" ]; then
    if _pyqt6_available && [ -f "$WIZARD" ]; then
        python3 "$WIZARD" "--install" && exit 0
    fi
    exec "$HERE/.niruvi-install/install.sh" "$@"
    exit 0
fi

if [ "$1" = "--update" ]; then
    if _pyqt6_available && [ -f "$WIZARD" ]; then
        python3 "$WIZARD" "--update" && exit 0
    fi
    if [ -f "$HERE/.niruvi-install/update.sh" ]; then
        exec "$HERE/.niruvi-install/update.sh" "$@"
    else
        _msg "No Updater" "This AppImage was built without an updater." "warning"
        exit 1
    fi
    exit 0
fi

if [ "$1" = "--check-updates" ]; then
    if _pyqt6_available && [ -f "$WIZARD" ]; then
        python3 "$WIZARD" "--check-updates" && exit 0
    fi
    if [ -f "$HERE/.niruvi-install/update.sh" ]; then
        exec "$HERE/.niruvi-install/update.sh" --check-only "$@"
    fi
    exit 0
fi
''' + apprun_update_check_section() + '''
# ── Launch installed or run installer ──
if [ -f "$MARKER" ] && [ -f "$INSTALL_DIR/AppRun" ]; then
    export LD_LIBRARY_PATH="$_SAVED_LD_LIBRARY_PATH"
    exec "$INSTALL_DIR/AppRun" "$@"
fi

exec "$HERE/.niruvi-install/install.sh" "$@"
'''


def apprun_script(app_name: str, app_version: str = "", style: str = "bash") -> str:
    safe_name = sanitize_bash_string(app_name, "app_name")
    safe_ver = sanitize_bash_string(app_version, "app_version")

    header = apprun_common_header(safe_name, safe_ver)
    ail_detect = apprun_appimagelauncher_detection()

    if style == "qt6":
        return '#!/bin/bash\n' + header + ail_detect + apprun_qt6_content()

    return '#!/bin/bash\n' + header + ail_detect + apprun_bash_content()
