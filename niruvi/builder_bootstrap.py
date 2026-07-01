"""Bootstrap script generator for self-installing AppImages.

Generates AppRun, install.sh, and uninstall.sh that get injected
into the AppDir before appimagetool produces the final AppImage.
"""

import os
import re
import stat
from pathlib import Path

_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9 ._+-]+$')


def _sanitize_bash_string(value: str, field_name: str = "value") -> str:
    """Validate and sanitize a value for safe interpolation into bash scripts.
    
    Only allows safe characters and rejects anything that could break out
    of a bash string literal.
    """
    if not _SAFE_NAME_RE.match(value):
        safe = re.sub(r'[^a-zA-Z0-9 ._+-]', '', value)
        safe = safe[:100]
        return safe
    return value[:100]


def _apprun_script(app_name: str, app_version: str = "", style: str = "bash") -> str:
    safe_name = _sanitize_bash_string(app_name, "app_name")
    safe_ver = _sanitize_bash_string(app_version, "app_version")

    if style == "qt6":
        return _apprun_qt6_content(safe_name, safe_ver)

    return _apprun_bash_content(safe_name, safe_ver)


def _apprun_bash_content(safe_name: str, safe_ver: str) -> str:
    return f'''#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
APP_NAME="{safe_name}"
APP_VERSION="{safe_ver}"

INSTALL_DIR="${{INSTALL_DIR:-$HOME/Applications/$APP_NAME}}"
MARKER="$INSTALL_DIR/.installed"
META="$INSTALL_DIR/.appimage-manager.json"

_gui_available() {{
    command -v zenity &>/dev/null || command -v kdialog &>/dev/null
}}

_msg() {{
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
}}

_confirm() {{
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
}}

# ── Version / Help ──
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

# ── Launch installed or run installer ──
if [ -f "$MARKER" ] && [ -f "$INSTALL_DIR/AppRun" ]; then
    exec "$INSTALL_DIR/AppRun" "$@"
fi

exec "$HERE/.niruvi-install/install.sh" "$@"
'''


def _apprun_qt6_content(safe_name: str, safe_ver: str) -> str:
    return f'''#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
APP_NAME="{safe_name}"
APP_VERSION="{safe_ver}"

INSTALL_DIR="${{INSTALL_DIR:-$HOME/Applications/$APP_NAME}}"
MARKER="$INSTALL_DIR/.installed"
META="$INSTALL_DIR/.appimage-manager.json"
WIZARD="$HERE/.niruvi-install/self_install_wizard.py"

_pyqt6_available() {{
    python3 -c "from PyQt6.QtWidgets import QApplication" 2>/dev/null
}}

_gui_available() {{
    command -v zenity &>/dev/null || command -v kdialog &>/dev/null
}}

_msg() {{
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
}}

_confirm() {{
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
}}

# ── Version / Help ──
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

# ── Launch installed or run installer ──
if [ -f "$MARKER" ] && [ -f "$INSTALL_DIR/AppRun" ]; then
    exec "$INSTALL_DIR/AppRun" "$@"
fi

exec "$HERE/.niruvi-install/install.sh" "$@"
'''


def _build_config_to_bash(config: dict) -> str:
    """Convert installer config dict into bash variable declarations + auxiliary file references."""
    brand = config.get("brand_name") or config["app_name"]
    rollback = "true" if config.get("enable_rollback", True) else "false"
    silent = "true" if config.get("enable_silent", True) else "false"

    has_license = str(config.get("license_content") is not None).lower()
    has_pre = str(config.get("pre_install_content") is not None).lower()
    has_post = str(config.get("post_install_content") is not None).lower()
    has_components = str(bool(config.get("components"))).lower()

    welcome = _sanitize_bash_string(config.get("welcome_message", ""), "welcome")
    finish = _sanitize_bash_string(config.get("finish_message", ""), "finish")
    enable_launch = str(config.get("enable_launch_at_finish", True)).lower()
    updater_url = config.get("updater_url", "")

    lines = []
    safe_name = _sanitize_bash_string(config["app_name"], "app_name")
    safe_ver = _sanitize_bash_string(str(config.get("app_version", "")), "app_version")
    safe_exec = _sanitize_bash_string(config.get("exec_name", safe_name), "exec_name")
    safe_brand = _sanitize_bash_string(brand, "brand_name")
    lines.append(f'APP_NAME="{safe_name}"')
    lines.append(f'APP_VERSION="{safe_ver}"')
    lines.append(f'EXEC_NAME="{safe_exec}"')
    lines.append(f'BRAND_NAME="{safe_brand}"')
    lines.append(f'ENABLE_ROLLBACK={rollback}')
    lines.append(f'ENABLE_SILENT={silent}')
    lines.append(f'HAS_LICENSE={has_license}')
    lines.append(f'HAS_PRE_INSTALL={has_pre}')
    lines.append(f'HAS_POST_INSTALL={has_post}')
    lines.append(f'HAS_COMPONENTS={has_components}')
    lines.append(f'WELCOME_MSG="{welcome}"')
    lines.append(f'FINISH_MSG="{finish}"')
    lines.append(f'ENABLE_LAUNCH={enable_launch}')
    lines.append('')
    lines.append('HERE="$(dirname "$(readlink -f "$0")")"')
    lines.append('APPIMAGE_DIR="$(dirname "$HERE")"')
    lines.append('')
    lines.append('if [ -n "$APPIMAGE" ]; then')
    lines.append('    SELF="$APPIMAGE"')
    lines.append('else')
    lines.append('    SELF="$(readlink -f /proc/self/exe 2>/dev/null || echo "")"')
    lines.append('fi')
    return '\n'.join(lines)


def _common_install_functions() -> str:
    """Shared bash functions used by all installer styles."""
    return '''
# ── Handle --unattended / --silent flags ──
UNATTENDED=false
for arg in "$@"; do
    case "$arg" in
        --unattended|--silent|-q) UNATTENDED=true ;;
    esac
done

# ── Utility functions ──
_default_install_dir() {
    echo "$HOME/Applications/$APP_NAME"
}

_gui_available() {
    command -v zenity &>/dev/null || command -v kdialog &>/dev/null
}

_msg() {
    local title="$1" text="$2" kind="$3"
    if command -v zenity &>/dev/null; then
        zenity --"$kind" --title="$title" --text="$text" 2>/dev/null
    elif command -v kdialog &>/dev/null; then
        local kdialog_kind
        case "$kind" in
            info) kdialog_kind="msgbox" ;;
            warning|warn) kdialog_kind="sorry" ;;
            error) kdialog_kind="error" ;;
            *) kdialog_kind="msgbox" ;;
        esac
        kdialog --"$kdialog_kind" "$text" --title "$title" 2>/dev/null
    else
        echo ""
        echo "  $title"
        echo "  $text"
        echo ""
    fi
}

_confirm() {
    local title="$1" text="$2"
    if [ "$UNATTENDED" = "true" ]; then
        return 0
    fi
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

_choose_dir() {
    local title="$1" default="$2"
    if [ "$UNATTENDED" = "true" ]; then
        echo "$default"
        return 0
    fi
    if command -v zenity &>/dev/null; then
        zenity --file-selection --directory --title="$title" --filename="$default/" 2>/dev/null
    elif command -v kdialog &>/dev/null; then
        kdialog --getexistingdirectory "$default" 2>/dev/null
    else
        echo ""
        echo "  $title"
        echo "  Default: $default"
        read -p "  Install directory [$default]: " REPLY
        echo "${REPLY:-$default}"
    fi
}

_ask_run() {
    local title="$1" text="$2"
    if [ "$UNATTENDED" = "true" ]; then
        return 1
    fi
    if command -v zenity &>/dev/null; then
        zenity --question --title="$title" --text="$text" --ok-label="Run Now" --cancel-label="Close" --width=400 2>/dev/null
    elif command -v kdialog &>/dev/null; then
        kdialog --yesno "$text" --title "$title" --yes-label "Run Now" --no-label "Close" 2>/dev/null
    else
        echo ""
        echo "  $title"
        echo "  $text"
        read -p "  [Run/Close]: " REPLY
        [ "$REPLY" != "c" ] && [ "$REPLY" != "C" ]
    fi
}

# ── Rollback ──
BACKUP_DIR=""
_rollback_init() {
    if [ "$ENABLE_ROLLBACK" != "true" ]; then
        return 0
    fi
    local target="$1"
    if [ -d "$target" ]; then
        BACKUP_DIR="$(mktemp -d)"
        cp -a "$target"/* "$BACKUP_DIR/" 2>/dev/null || true
    fi
}

_rollback_restore() {
    local exit_code=$?
    if [ "$ENABLE_ROLLBACK" != "true" ] || [ -z "$BACKUP_DIR" ] || [ ! -d "$BACKUP_DIR" ]; then
        return 0
    fi
    if [ $exit_code -ne 0 ]; then
        local target="$1"
        _msg "Rolling Back" "An error occurred. Restoring from backup..." "error"
        if [ -d "$target" ]; then
            rm -rf "$target" 2>/dev/null || true
        fi
        if [ -d "$BACKUP_DIR" ]; then
            mkdir -p "$target" 2>/dev/null || true
            cp -a "$BACKUP_DIR"/* "$target/" 2>/dev/null || true
        fi
    fi
    rm -rf "$BACKUP_DIR" 2>/dev/null || true
}

# ── License handling ──
_handle_license() {
    if [ "$HAS_LICENSE" != "true" ]; then
        return 0
    fi
    local license_file="$HERE/license.txt"
    if [ ! -f "$license_file" ]; then
        return 0
    fi
    if [ "$UNATTENDED" = "true" ]; then
        return 0
    fi
    local license_text
    license_text=$(cat "$license_file")
    if command -v zenity &>/dev/null; then
        echo "$license_text" | zenity --text-info --title="$BRAND_NAME - License Agreement" \
            --width=600 --height=400 --ok-label="Accept" --cancel-label="Decline" 2>/dev/null
        return $?
    elif command -v kdialog &>/dev/null; then
        kdialog --textbox "$license_file" --title "$BRAND_NAME - License Agreement" --width=600 --height=400 2>/dev/null
        kdialog --yesno "Do you accept the license terms?" --title "$BRAND_NAME - License Agreement" --yes-label "Accept" --no-label "Decline" 2>/dev/null
        return $?
    else
        echo ""
        echo "  ===== License Agreement ====="
        echo "$license_text"
        echo ""
        read -p "  Do you accept the license terms? [Y/n]: " REPLY
        [ "$REPLY" != "n" ] && [ "$REPLY" != "N" ]
        return $?
    fi
}

# ── Component selection ──
_selected_components=()
_handle_components() {
    if [ "$HAS_COMPONENTS" != "true" ]; then
        return 0
    fi
    local comp_file="$HERE/components.cfg"
    if [ ! -f "$comp_file" ]; then
        return 0
    fi
    if [ "$UNATTENDED" = "true" ]; then
        while IFS=: read -r id label default_enabled description; do
            _selected_components+=("$id:$label:$default_enabled")
        done < "$comp_file"
        return 0
    fi

    local comps=()
    while IFS=: read -r id label default_enabled description; do
        local checked
        [ "$default_enabled" = "true" ] && checked="TRUE" || checked="FALSE"
        comps+=("$checked" "$label ($id)")
    done < "$comp_file"

    if command -v zenity &>/dev/null; then
        local selections
        selections=$(zenity --list --checklist --title="$BRAND_NAME - Components" \
            --text="Select components to install:" --width=500 --height=300 \
            --column="Install" --column="Component" \
            --separator="|" \
            "${comps[@]}" 2>/dev/null)
        if [ -z "$selections" ]; then
            return 1
        fi
        while IFS=: read -r id label default_enabled description; do
            local match=" ($id)"
            if echo "$selections" | grep -F -q "$match"; then
                _selected_components+=("$id:$label:true")
            else
                _selected_components+=("$id:$label:false")
            fi
        done < "$comp_file"
    elif command -v kdialog &>/dev/null; then
        local args=()
        while IFS=: read -r id label default_enabled description; do
            if [ "$default_enabled" = "true" ]; then
                args+=("$id" "$label" "on")
            else
                args+=("$id" "$label" "off")
            fi
        done < "$comp_file"
        local selections
        selections=$(kdialog --checklist "$BRAND_NAME - Components" "${args[@]}" 2>/dev/null)
        local kdialog_exit=$?
        if [ -z "$selections" ] && [ $kdialog_exit -ne 0 ]; then
            return 1
        fi
        local selections_space=" $selections "
        while IFS=: read -r id label default_enabled description; do
            if echo "$selections_space" | grep -q " $id "; then
                _selected_components+=("$id:$label:true")
            else
                _selected_components+=("$id:$label:false")
            fi
        done < "$comp_file"
    else
        echo ""
        echo "  ===== Select Components ====="
        local i=0
        local choices=()
        while IFS=: read -r id label default_enabled description; do
            i=$((i+1))
            local default_char=" "
            [ "$default_enabled" = "true" ] && default_char="*"
            echo "  [$default_char] $i. $label"
            echo "       $description"
            choices+=("$id:$label:$default_enabled")
        done < "$comp_file"
        echo ""
        echo "  Enter numbers to toggle (e.g. 1 3), or leave blank for defaults:"
        read -p "  Selection: " -a toggles
        local idx=0
        for choice in "${choices[@]}"; do
            IFS=: read -r id label default_enabled <<< "$choice"
            local selected="$default_enabled"
            for t in "${toggles[@]}"; do
                if [ "$t" = "$((idx+1))" ]; then
                    [ "$selected" = "true" ] && selected="false" || selected="true"
                fi
            done
            _selected_components+=("$id:$label:$selected")
            idx=$((idx+1))
        done
    fi
}

# ── Pre/post script execution ──
_run_pre_install() {
    if [ "$HAS_PRE_INSTALL" != "true" ]; then
        return 0
    fi
    local pre_file="$HERE/pre-install.sh"
    if [ -f "$pre_file" ]; then
        _msg "Pre-Install" "Running pre-installation script..." "info"
        bash "$pre_file"
    fi
}

_run_post_install() {
    if [ "$HAS_POST_INSTALL" != "true" ]; then
        return 0
    fi
    local post_file="$HERE/post-install.sh"
    if [ -f "$post_file" ]; then
        _msg "Post-Install" "Running post-installation script..." "info"
        bash "$post_file"
    fi
}

# ── Save selected components ──
_save_component_selection() {
    local target="$1"
    for comp in "${_selected_components[@]}"; do
        echo "$comp" >> "$target/.niruvi-components"
    done
}

# ── Core extraction function ──
_extract_appimage() {
    local dest="$1"
    mkdir -p "$dest"
    local tmpdir
    tmpdir="$(mktemp -d)"

    (
        cd "$tmpdir" || exit 1
        if [ -n "$SELF" ] && [ -f "$SELF" ]; then
            "$SELF" --appimage-extract 2>/dev/null || true
        fi
    )

    local squashfs=""
    for d in "$tmpdir/squashfs-root" "$tmpdir"/*/; do
        if [ -d "$d" ] && [ -f "$d/AppRun" ]; then
            squashfs="$d"
            break
        fi
    done
    if [ -z "$squashfs" ]; then
        squashfs="$tmpdir/squashfs-root"
    fi
    if [ -z "$squashfs" ] || [ ! -d "$squashfs" ]; then
        _msg "Error" "Extraction failed. Could not find extracted files." "error"
        rm -rf "$tmpdir"
        exit 1
    fi

    cp -a "$squashfs"/* "$dest/"
    rm -rf "$tmpdir"
    chmod +x "$dest/AppRun" 2>/dev/null || true
    echo "installed" > "$dest/.installed"

    # Restore real application launcher (self-installing mode only — no-op if absent)
    local backup="$dest/.niruvi-install/apprun-backup.sh"
    if [ -f "$backup" ]; then
        cp "$backup" "$dest/AppRun"
        chmod +x "$dest/AppRun"
    fi
}

# ── Desktop integration ──
_install_desktop_entries() {
    local target="$1"
    local desktop_dir="$HOME/.local/share/applications"
    mkdir -p "$desktop_dir"

    local icon_path="$target/.DirIcon"
    if [ ! -f "$icon_path" ]; then
        icon_path=$(find "$target" -maxdepth 4 -name "*.png" -o -name "*.svg" -o -name "*.xpm" 2>/dev/null | head -1)
        [ -n "$icon_path" ] || icon_path="system-software-install"
    fi

    cat > "$desktop_dir/$APP_NAME.desktop" << DESKTOPEOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Exec=$target/AppRun %F
Icon=$icon_path
Terminal=false
Categories=Utility;
StartupNotify=true
X-created-by=Niruvi-Builder
DESKTOPEOF
    chmod 644 "$desktop_dir/$APP_NAME.desktop"

    local icons_dir="$HOME/.local/share/icons/hicolor"
    if [ -d "$target/usr/share/icons" ]; then
        cp -a "$target/usr/share/icons/"* "$icons_dir/" 2>/dev/null || true
    fi
}

_install_uninstall_entry() {
    local target="$1"
    local desktop_dir="$HOME/.local/share/applications"
    local uninstall_script="$target/.niruvi-uninstall.sh"
    cp "$HERE/uninstall.sh" "$uninstall_script" 2>/dev/null
    chmod +x "$uninstall_script" 2>/dev/null || true

    cat > "$desktop_dir/uninstall-$APP_NAME.desktop" << UNINSTALLEOF
[Desktop Entry]
Type=Application
Name=Uninstall $APP_NAME
Exec=$uninstall_script
Icon=computer
Terminal=false
Categories=Utility;
StartupNotify=false
UNINSTALLEOF
    chmod 644 "$desktop_dir/uninstall-$APP_NAME.desktop"
}

_save_metadata() {
    local target="$1"
    cat > "$target/.appimage-manager.json" << METAMEOF
{"version": "$APP_VERSION", "install_date": "$(date -Iseconds)"}
METAMEOF
}

_refresh_desktop_db() {
    for cmd in gtk-update-icon-cache xdg-desktop-menu forceupdate update-desktop-database; do
        command -v "$cmd" &>/dev/null && "$cmd" 2>/dev/null || true
    done
    for kde in kbuildsycoca6 kbuildsycoca5; do
        command -v "$kde" &>/dev/null && "$kde" 2>/dev/null || true
    done
}
'''


def _wizard_install_body() -> str:
    return '''
# ═══════════════════════════════════════════
#  Handle License
# ═══════════════════════════════════════════
if ! _handle_license; then
    _msg "License Declined" "You must accept the license agreement to install." "error"
    exit 1
fi

# ═══════════════════════════════════════════
#  Handle Components
# ═══════════════════════════════════════════
if ! _handle_components; then
    _msg "Cancelled" "No components selected." "error"
    exit 1
fi

# ═══════════════════════════════════════════
#  Choose location
# ═══════════════════════════════════════════
DEFAULT_DIR="$(_default_install_dir)"
INSTALL_DIR="$(_choose_dir "Choose install location for $APP_NAME" "$DEFAULT_DIR")"
if [ -z "$INSTALL_DIR" ]; then
    _msg "Cancelled" "Installation cancelled." "error"
    exit 1
fi

# ═══════════════════════════════════════════
#  Check if already installed + rollback init
# ═══════════════════════════════════════════
if [ -f "$INSTALL_DIR/.installed" ]; then
    if ! _confirm "Already Installed" \\
"$APP_NAME is already installed in: $INSTALL_DIR\n\nOverwrite?"; then
        exit 1
    fi
    rm -rf "$INSTALL_DIR"
fi

_rollback_init "$INSTALL_DIR"
trap "_rollback_restore $INSTALL_DIR" EXIT

# ═══════════════════════════════════════════
#  Pre-install script
# ═══════════════════════════════════════════
_run_pre_install

# ═══════════════════════════════════════════
#  Extract
# ═══════════════════════════════════════════
_msg "Installing" "Extracting $APP_NAME to: $INSTALL_DIR" "info"
_extract_appimage "$INSTALL_DIR"
_save_metadata "$INSTALL_DIR"
_save_component_selection "$INSTALL_DIR"

# ═══════════════════════════════════════════
#  Post-install script
# ═══════════════════════════════════════════
_run_post_install

# ═══════════════════════════════════════════
#  Desktop integration
# ═══════════════════════════════════════════
_install_desktop_entries "$INSTALL_DIR"
_install_uninstall_entry "$INSTALL_DIR"
_refresh_desktop_db

# ═══════════════════════════════════════════
#  Done
# ═══════════════════════════════════════════
_msg "Installation Complete" \\
"$APP_NAME has been installed to: $INSTALL_DIR\n\nYou can launch it from your application menu." \\
"info"

if _ask_run "Run $APP_NAME?" "Launch $APP_NAME now?"; then
    "$INSTALL_DIR/AppRun" &
fi

exit 0
'''


def _macos_install_body() -> str:
    return '''
# ═══════════════════════════════════════════
#  Step 1: Welcome
# ═══════════════════════════════════════════
_msg "Welcome to $BRAND_NAME" \\
"[Step 1/5] Welcome

This installer will guide you through installing
$BRAND_NAME on your system.

Version: $APP_VERSION
Type: Self-Installing AppImage

Click OK to continue." \\
"info"

# ═══════════════════════════════════════════
#  Step 2: License + Components
# ═══════════════════════════════════════════
if ! _handle_license; then
    _msg "License Declined" "You must accept the license agreement to install." "error"
    exit 1
fi

if ! _handle_components; then
    _msg "Cancelled" "No components selected." "error"
    exit 1
fi

# ═══════════════════════════════════════════
#  Step 3: Destination
# ═══════════════════════════════════════════
DEFAULT_DIR="$(_default_install_dir)"
INSTALL_DIR="$(_choose_dir "[Step 3/5] Choose Install Location" "$DEFAULT_DIR")"
if [ -z "$INSTALL_DIR" ]; then
    _msg "Installation Cancelled" "Installation cancelled." "error"
    exit 1
fi

# ═══════════════════════════════════════════
#  Step 4: Confirm, Extract, Install
# ═══════════════════════════════════════════
if [ -f "$INSTALL_DIR/.installed" ]; then
    if ! _confirm "[Step 4/5] Already Installed" \\
"$APP_NAME is already installed in:
$INSTALL_DIR

Overwrite existing installation?"; then
        exit 1
    fi
    rm -rf "$INSTALL_DIR"
fi

if ! _confirm "[Step 4/5] Ready to Install" \\
"$APP_NAME will be installed to:
  $INSTALL_DIR

Disk space required: approximately 500 MB

Proceed with installation?"; then
    exit 1
fi

_rollback_init "$INSTALL_DIR"
trap "_rollback_restore $INSTALL_DIR" EXIT
_run_pre_install

_msg "Installing" "Extracting $APP_NAME to: $INSTALL_DIR" "info"
_extract_appimage "$INSTALL_DIR"
_save_metadata "$INSTALL_DIR"
_save_component_selection "$INSTALL_DIR"
_run_post_install

_msg "Installing" "Setting up desktop integration..." "info"
_install_desktop_entries "$INSTALL_DIR"
_install_uninstall_entry "$INSTALL_DIR"
_refresh_desktop_db

# ═══════════════════════════════════════════
#  Step 5: Summary
# ═══════════════════════════════════════════
_msg "Installation Complete" \\
"[Step 5/5] Summary

$APP_NAME has been installed successfully.

  Location:  $INSTALL_DIR
  Launcher:  Desktop Menu
  Uninstall: Desktop Menu \\u2192 Uninstall $APP_NAME
  Version:   $APP_VERSION

You can launch $APP_NAME from your application menu." \\
"info"

if _ask_run "Run $APP_NAME?" "Launch $APP_NAME now?"; then
    "$INSTALL_DIR/AppRun" &
fi

exit 0
'''


def _minimal_install_body() -> str:
    return '''
# ═══════════════════════════════════════════
#  License Agreement
# ═══════════════════════════════════════════
if [ "$HAS_LICENSE" = "true" ] && [ -f "$HERE/license.txt" ]; then
    echo ""
    echo "========================================"
    echo "  License Agreement"
    echo "========================================"
    cat "$HERE/license.txt"
    echo ""
    if [ "$UNATTENDED" != "true" ]; then
        read -p "  Accept? [Y/n]: " REPLY
        if [ "$REPLY" = "n" ] || [ "$REPLY" = "N" ]; then
            echo "License declined. Exiting."
            exit 1
        fi
    fi
    echo ""
fi

# ═══════════════════════════════════════════
#  Component Selection
# ═══════════════════════════════════════════
if [ "$HAS_COMPONENTS" = "true" ] && [ -f "$HERE/components.cfg" ]; then
    echo ""
    echo "========================================"
    echo "  Components"
    echo "========================================"
    local i=0
    while IFS=: read -r id label default_enabled description; do
        i=$((i+1))
        local mark=" "
        [ "$default_enabled" = "true" ] && mark="*"
        echo "  [$mark] $i. $label"
    done < "$HERE/components.cfg"
    if [ "$UNATTENDED" != "true" ]; then
        echo ""
        echo "  Enter numbers to toggle, or leave blank for defaults:"
        read -p "  Selection: " -a toggles
    fi
    local i=0
    while IFS=: read -r id label default_enabled description; do
        i=$((i+1))
        local sel="$default_enabled"
        for t in "${toggles[@]}"; do
            if [ "$t" = "$i" ]; then
                [ "$sel" = "true" ] && sel="false" || sel="true"
            fi
        done
        _selected_components+=("$id:$label:$sel")
    done < "$HERE/components.cfg"
    echo ""
fi

# ═══════════════════════════════════════════
#  Install
# ═══════════════════════════════════════════
echo ""
echo "========================================"
echo "  $BRAND_NAME Installer"
echo "========================================"
echo ""
echo "Version: $APP_VERSION"
echo ""

DEFAULT_DIR="$HOME/Applications/$APP_NAME"
if [ "$UNATTENDED" = "true" ]; then
    INSTALL_DIR="$DEFAULT_DIR"
else
    read -p "Install directory [$DEFAULT_DIR]: " INSTALL_DIR
    INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_DIR}"
fi
echo ""

if [ -f "$INSTALL_DIR/.installed" ]; then
    echo "Warning: $APP_NAME is already installed in: $INSTALL_DIR"
    if [ "$UNATTENDED" != "true" ]; then
        read -p "Overwrite? [y/N]: " REPLY
        [ "$REPLY" != "y" ] && [ "$REPLY" != "Y" ] && exit 1
    fi
    rm -rf "$INSTALL_DIR"
fi

_rollback_init "$INSTALL_DIR"
trap "_rollback_restore $INSTALL_DIR" EXIT

_run_pre_install
echo "Extracting $APP_NAME..."
_extract_appimage "$INSTALL_DIR"
_save_metadata "$INSTALL_DIR"
_save_component_selection "$INSTALL_DIR"
_run_post_install

echo "Setting up desktop entries..."
_install_desktop_entries "$INSTALL_DIR"
_install_uninstall_entry "$INSTALL_DIR"
_refresh_desktop_db

echo ""
echo "========================================"
echo "  Installation Complete"
echo "========================================"
echo ""
echo "  Location:  $INSTALL_DIR"
echo "  Launcher:  Desktop Menu"
echo "  Uninstall: Desktop Menu"
echo ""
echo "Run '$INSTALL_DIR/AppRun' to launch."
echo ""
exit 0
'''


def _get_install_body(style: str) -> str:
    """Lazy resolver for install body functions to avoid forward-reference issues."""
    bodies = {
        "wizard": _wizard_install_body,
        "macos": _macos_install_body,
        "minimal": _minimal_install_body,
        "installbuilder": _installbuilder_install_body,
    }
    fn = bodies.get(style, bodies["wizard"])
    return fn()


def _build_install_script(config: dict) -> str:
    """Generate install.sh for the given config."""
    preamble = _build_config_to_bash(config)
    functions = _common_install_functions()
    style = config.get("installer_style", "wizard")
    body = _get_install_body(style)
    return '#!/bin/bash\nset -e\n' + preamble + functions + body


def _uninstall_script(app_name: str) -> str:
    safe_name = _sanitize_bash_string(app_name, "app_name")
    return f'''#!/bin/bash
set -e

APP_NAME="{safe_name}"
INSTALL_DIR="$HOME/Applications/$APP_NAME"

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
if [ -f "$SCRIPT_DIR/.appimage-manager.json" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
elif [ -f "$INSTALL_DIR/.appimage-manager.json" ]; then
    true
elif [ -f "$SCRIPT_DIR/../.installed" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
fi

if [ ! -f "$INSTALL_DIR/.installed" ]; then
    echo "Error: $APP_NAME installation not found at: $INSTALL_DIR"
    exit 1
fi

# ── Handle --unattended / --silent flags ──
UNATTENDED=false
for arg in "$@"; do
    case "$arg" in
        --unattended|--silent|-q) UNATTENDED=true ;;
    esac
done

_gui_available() {{
    command -v zenity &>/dev/null || command -v kdialog &>/dev/null
}}

_confirm() {{
    local title="$1" text="$2"
    if [ "$UNATTENDED" = "true" ]; then
        return 0
    fi
    if command -v zenity &>/dev/null; then
        zenity --question --title="$title" --text="$text" --width=400 2>/dev/null
    elif command -v kdialog &>/dev/null; then
        kdialog --yesno "$text" --title "$title" 2>/dev/null
    else
        echo ""
        echo "  $title"
        echo "  $text"
        read -p "  [y/N]: " REPLY
        [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]
    fi
}}

_msg() {{
    local title="$1" text="$2" kind="$3"
    if command -v zenity &>/dev/null; then
        zenity --"$kind" --title="$title" --text="$text" 2>/dev/null
    elif command -v kdialog &>/dev/null; then
        local kdialog_kind
        case "$kind" in
            info) kdialog_kind="msgbox" ;;
            warning|warn) kdialog_kind="sorry" ;;
            error) kdialog_kind="error" ;;
            *) kdialog_kind="msgbox" ;;
        esac
        kdialog --"$kdialog_kind" "$text" --title "$title" 2>/dev/null
    else
        echo ""
        echo "  $title"
        echo "  $text"
    fi
}}

# ── Page: Welcome (unattended skips to confirm) ──
if [ "$UNATTENDED" != "true" ]; then
    _msg "Uninstall $APP_NAME" \\
"The following will be removed:

  • Application files: $INSTALL_DIR
  • Desktop shortcut
  • Application icon
  • Uninstaller entry

Total size: $(du -sh "$INSTALL_DIR" 2>/dev/null | cut -f1 || echo "unknown")" \\
"info"
fi

# ── Page: Confirm ──
if ! _confirm "Confirm Uninstall" \\
"Remove $APP_NAME and all its files?

Location: $INSTALL_DIR
Components: All files will be permanently deleted."; then
    exit 0
fi

# ── Page: Progress ──
REMOVED_ITEMS=0
TOTAL_STEPS=4

_do_step() {{
    local msg="$1" cmd="$2"
    REMOVED_ITEMS=$((REMOVED_ITEMS + 1))
    local pct=$((REMOVED_ITEMS * 100 / TOTAL_STEPS))
    if command -v zenity &>/dev/null && [ "$UNATTENDED" != "true" ]; then
        echo "$pct"
        echo "# $msg"
    fi
    eval "$cmd"
}}

# Run removal with progress pipe
if command -v zenity &>/dev/null && [ "$UNATTENDED" != "true" ]; then
    exec 3>&1
    (
        _do_step "Removing desktop entries..." \\
            'rm -f "$HOME/.local/share/applications/$APP_NAME.desktop" "$HOME/.local/share/applications/uninstall-$APP_NAME.desktop"'
        _do_step "Removing icons..." \\
            'find "$HOME/.local/share/icons" -name "$APP_NAME.*" -type f,l -delete 2>/dev/null || true; find "$HOME/.local/share/icons" -path "*/apps/$APP_NAME.*" -type f,l -delete 2>/dev/null || true'
        _do_step "Removing application files..." \\
            'if [ -d "$INSTALL_DIR" ]; then rm -rf "$INSTALL_DIR"; fi'
        _do_step "Refreshing desktop database..." \\
            'for cmd in gtk-update-icon-cache xdg-desktop-menu forceupdate update-desktop-database; do command -v "$cmd" &>/dev/null && "$cmd" 2>/dev/null || true; done; for kde in kbuildsycoca6 kbuildsycoca5; do command -v "$kde" &>/dev/null && "$kde" 2>/dev/null || true; done'
        echo "100"
        echo "# Uninstall complete!"
    ) | zenity --progress --title="Uninstalling $APP_NAME" --text="Starting..." --percentage=0 --auto-close 2>/dev/null || true
    exec 3>&-
else
    # Non-GUI: sequential steps
    _do_step "Removing desktop entries..." \\
            'rm -f "$HOME/.local/share/applications/$APP_NAME.desktop" "$HOME/.local/share/applications/uninstall-$APP_NAME.desktop"'
    _do_step "Removing icons..." \\
            'find "$HOME/.local/share/icons" -name "$APP_NAME.*" -type f,l -delete 2>/dev/null || true; find "$HOME/.local/share/icons" -path "*/apps/$APP_NAME.*" -type f,l -delete 2>/dev/null || true'
    _do_step "Removing application files..." \\
            'if [ -d "$INSTALL_DIR" ]; then rm -rf "$INSTALL_DIR"; fi'
    _do_step "Refreshing desktop database..." \\
            'for cmd in gtk-update-icon-cache xdg-desktop-menu forceupdate update-desktop-database; do command -v "$cmd" &>/dev/null && "$cmd" 2>/dev/null || true; done; for kde in kbuildsycoca6 kbuildsycoca5; do command -v "$kde" &>/dev/null && "$kde" 2>/dev/null || true; done'
fi

REMOVED_ITEMS=0

# ── Page: Finish ──
_msg "Uninstalled" "$APP_NAME has been completely removed." "info"
exit 0
'''


def _updater_script(app_name: str, app_version: str = "", updater_url: str = "") -> str:
    """Generate update.sh — GUI updater that checks a JSON endpoint, downloads, and installs."""
    safe_name = _sanitize_bash_string(app_name, "app_name")
    safe_ver = _sanitize_bash_string(app_version, "app_version")
    if not updater_url:
        updater_url = "https://example.com/updates/update.json"
    return f'''#!/bin/bash
set -e

APP_NAME="{safe_name}"
APP_VERSION="{safe_ver}"
UPDATE_URL="{updater_url}"
INSTALL_DIR="$HOME/Applications/$APP_NAME"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

if [ -f "$SCRIPT_DIR/.appimage-manager.json" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
fi

MARKER="$INSTALL_DIR/.installed"
META="$INSTALL_DIR/.appimage-manager.json"

if [ ! -f "$MARKER" ]; then
    echo "Error: $APP_NAME is not installed."
    exit 1
fi

# ── Handle flags ──
CHECK_ONLY=false
SILENT=false
for arg in "$@"; do
    case "$arg" in
        --check-only) CHECK_ONLY=true ;;
        --check-silent) SILENT=true; CHECK_ONLY=true ;;
        --unattended|--silent|-q) SILENT=true ;;
    esac
done

_gui_available() {{
    command -v zenity &>/dev/null || command -v kdialog &>/dev/null
}}

_msg() {{
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
    fi
}}

_confirm() {{
    local title="$1" text="$2"
    if [ "$SILENT" = "true" ]; then
        return 0
    fi
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
}}

# ── Load installed version ──
INSTALLED_VER="?"
if [ -f "$META" ]; then
    INSTALLED_VER=$(python3 -c "import json; print(json.load(open('$META')).get('version','?'))" 2>/dev/null || echo "?")
fi

# ── Page: Check for updates ──
if [ "$SILENT" = "true" ]; then
    # Silent check: just update last_update_check timestamp
    if [ -f "$META" ]; then
        python3 -c "
import json
with open('$META') as f:
    d = json.load(f)
d['last_update_check'] = $(date +%s)
with open('$META', 'w') as f:
    json.dump(d, f)
" 2>/dev/null || true
    fi
fi

if [ "$CHECK_ONLY" = "true" ]; then
    exit 0
fi

_msg "Checking for Updates" "Checking for updates for $APP_NAME..." "info"

# ── Fetch update info ──
REMOTE_VER=""
REMOTE_URL=""
REMOTE_CHANGELOG=""
REMOTE_SHA256=""

if command -v curl &>/dev/null; then
    FETCH_CMD="curl -sL --connect-timeout 10 \"$UPDATE_URL\""
elif command -v wget &>/dev/null; then
    FETCH_CMD="wget -q -O - --timeout=10 \"$UPDATE_URL\""
else
    _msg "No Internet" "Cannot check for updates: neither curl nor wget found." "error"
    exit 1
fi

JSON_DATA=$(curl -sL -- "$UPDATE_URL" 2>/dev/null || echo "")

if [ -z "$JSON_DATA" ]; then
    _msg "Update Check Failed" "Could not reach update server.

URL: $UPDATE_URL

Check your internet connection and try again." "error"
    exit 1
fi

REMOTE_VER=$(echo "$JSON_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version',''))" 2>/dev/null || echo "")
REMOTE_URL=$(echo "$JSON_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('download_url',''))" 2>/dev/null || echo "")
REMOTE_CHANGELOG=$(echo "$JSON_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('changelog',''))" 2>/dev/null || echo "")
REMOTE_SHA256=$(echo "$JSON_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sha256',''))" 2>/dev/null || echo "")

# ── Page: No update available ──
if [ -z "$REMOTE_VER" ] || [ "$REMOTE_VER" = "$INSTALLED_VER" ]; then
    _msg "Up to Date" "$APP_NAME is already at the latest version ($INSTALLED_VER)." "info"
    exit 0
fi

# ── Page: Update available ──
SUMMARY="Version $REMOTE_VER is available.

Current version: $INSTALLED_VER
New version:     $REMOTE_VER"

if [ -n "$REMOTE_CHANGELOG" ]; then
    SUMMARY="$SUMMARY

Changelog:
$REMOTE_CHANGELOG"
fi

if ! _confirm "Update Available" "$SUMMARY

Download and install the update?"; then
    _msg "Update Cancelled" "Update cancelled. You can update later by running: $APP_NAME --update" "info"
    exit 0
fi

# ── Page: Download ──
if [ -z "$REMOTE_URL" ]; then
    _msg "Download Error" "No download URL found in update manifest." "error"
    exit 1
fi

TMP_FILE="$(mktemp -d)/$APP_NAME-update.AppImage"
_msg "Downloading" "Downloading $APP_NAME $REMOTE_VER..." "info"

if command -v curl &>/dev/null; then
    if command -v zenity &>/dev/null && [ "$SILENT" != "true" ]; then
        curl -L --connect-timeout 30 "$REMOTE_URL" -o "$TMP_FILE" 2>&1 | \\
            zenity --progress --title="Downloading $APP_NAME $REMOTE_VER" --text="Downloading update..." \\
            --percentage=0 --auto-close 2>/dev/null || true
    else
        curl -L --connect-timeout 30 "$REMOTE_URL" -o "$TMP_FILE"
    fi
elif command -v wget &>/dev/null; then
    if command -v zenity &>/dev/null && [ "$SILENT" != "true" ]; then
        wget --timeout=30 -O "$TMP_FILE" "$REMOTE_URL" 2>&1 | \\
            zenity --progress --title="Downloading $APP_NAME $REMOTE_VER" --text="Downloading update..." \\
            --percentage=0 --auto-close 2>/dev/null || true
    else
        wget --timeout=30 -O "$TMP_FILE" "$REMOTE_URL"
    fi
fi

if [ ! -f "$TMP_FILE" ] || [ ! -s "$TMP_FILE" ]; then
    _msg "Download Failed" "Failed to download update from:
$REMOTE_URL" "error"
    rm -f "$TMP_FILE" 2>/dev/null || true
    exit 1
fi

# ── Verify SHA256 ──
if [ -n "$REMOTE_SHA256" ]; then
    FILE_HASH=$(sha256sum "$TMP_FILE" | cut -d' ' -f1)
    if [ "$FILE_HASH" != "$REMOTE_SHA256" ]; then
        _msg "Verification Failed" "SHA256 checksum mismatch.

Expected: $REMOTE_SHA256
Got:      $FILE_HASH

The download may be corrupted. Update cancelled." "error"
        rm -f "$TMP_FILE" 2>/dev/null || true
        exit 1
    fi
    _msg "Verified" "Download verified (SHA256: ${{REMOTE_SHA256:0:16}}...)." "info"
fi

# ── Page: Install update ──
_msg "Installing Update" "Extracting update to $INSTALL_DIR..." "info"

# Backup current install
BACKUP_DIR="$(mktemp -d)"
cp -a "$INSTALL_DIR"/* "$BACKUP_DIR/" 2>/dev/null || true

# Extract new version
chmod +x "$TMP_FILE"
TMP_EXTRACT="$(mktemp -d)"
(
    cd "$TMP_EXTRACT" || exit 1
    "$TMP_FILE" --appimage-extract 2>/dev/null || true
)
SQUASHFS=""
for d in "$TMP_EXTRACT/squashfs-root" "$TMP_EXTRACT"/*/; do
    if [ -d "$d" ] && [ -f "$d/AppRun" ]; then
        SQUASHFS="$d"
        break
    fi
done
if [ -z "$SQUASHFS" ] || [ ! -d "$SQUASHFS" ]; then
    _msg "Update Failed" "Failed to extract update. Restoring from backup..." "error"
    rm -rf "$INSTALL_DIR" 2>/dev/null || true
    cp -a "$BACKUP_DIR"/* "$INSTALL_DIR/" 2>/dev/null || true
    rm -rf "$BACKUP_DIR" "$TMP_EXTRACT" "$TMP_FILE" 2>/dev/null || true
    exit 1
fi

rm -rf "$INSTALL_DIR" 2>/dev/null || true
cp -a "$SQUASHFS"/* "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/AppRun" 2>/dev/null || true

# Update metadata
python3 -c "
import json
d = {{}}
if open('$META','r'):
    try:
        d = json.load(open('$META'))
    except: pass
d['version'] = '$REMOTE_VER'
d['install_date'] = '$(date -Iseconds)'
d['last_update_check'] = $(date +%s)
json.dump(d, open('$META','w'))
" 2>/dev/null || true

# Cleanup
rm -rf "$BACKUP_DIR" "$TMP_EXTRACT" "$TMP_FILE" 2>/dev/null || true

# ── Page: Finish ──
_msg "Update Complete" "$APP_NAME has been updated to version $REMOTE_VER." "info"

if _confirm "Restart $APP_NAME?" "Launch $APP_NAME now to use the new version?"; then
    "$INSTALL_DIR/AppRun" &
fi

exit 0
'''


def _installbuilder_install_body() -> str:
    """State machine wizard with Back/Next/Cancel using zenity --extra-button.

    Pages: Welcome → License → Directory → Components → Summary → Progress → Finish.
    """
    return '''
# ═══════════════════════════════════════════════════════════════
#  InstallBuilder-style state machine wizard
# ═══════════════════════════════════════════════════════════════

INSTALL_DIR=""
SELECTED_COMPONENTS=()

_page_welcome() {
    local msg
    if [ -n "$WELCOME_MSG" ]; then
        msg="$WELCOME_MSG"
    else
        msg="Welcome to the $BRAND_NAME Setup Wizard.

This wizard will guide you through installing $APP_NAME on your system.

Version: $APP_VERSION"
    fi
    if command -v zenity &>/dev/null; then
        local result
        result=$(zenity --info --title="Setup - $BRAND_NAME" --text="$msg" \\
            --width=500 --ok-label="Next" --extra-button="Cancel" 2>/dev/null)
        if [ "$result" = "Cancel" ]; then
            return 2
        fi
        return 0
    elif command -v kdialog &>/dev/null; then
        kdialog --msgbox "$msg" --title "Setup - $BRAND_NAME" 2>/dev/null
        return $?
    else
        echo ""
        echo "  ===== $BRAND_NAME Setup ====="
        echo ""
        echo "$msg"
        echo ""
        read -p "  Press Enter to continue..." REPLY
        return 0
    fi
}

_page_license() {
    if [ "$HAS_LICENSE" != "true" ] || [ ! -f "$HERE/license.txt" ]; then
        return 0
    fi
    _handle_license
    return $?
}

_page_directory() {
    DEFAULT_DIR="$(_default_install_dir)"
    local dir
    if command -v zenity &>/dev/null; then
        dir=$(zenity --entry --title="Setup - $BRAND_NAME" --text="Installation Directory:" \
            --entry-text="$DEFAULT_DIR" \
            --ok-label="Next" --extra-button="Back" --extra-button="Cancel" --width=500 2>/dev/null)
        if [ -z "$dir" ] || [ "$dir" = "Cancel" ]; then
            return 2
        fi
        if [ "$dir" = "Back" ]; then
            return 1
        fi
        INSTALL_DIR="$dir"
    elif command -v kdialog &>/dev/null; then
        dir=$(kdialog --getexistingdirectory "$DEFAULT_DIR" 2>/dev/null)
        if [ -z "$dir" ]; then
            return 2
        fi
        INSTALL_DIR="$dir"
    else
        echo ""
        echo "  Installation Directory"
        echo "  Default: $DEFAULT_DIR"
        read -p "  Install path [$DEFAULT_DIR]: " dir
        INSTALL_DIR="${dir:-$DEFAULT_DIR}"
    fi
    if [ -z "$INSTALL_DIR" ]; then
        INSTALL_DIR="$DEFAULT_DIR"
    fi
    return 0
}

_page_components() {
    if [ "$HAS_COMPONENTS" != "true" ] || [ ! -f "$HERE/components.cfg" ]; then
        return 0
    fi
    _handle_components
    local rc=$?
    if [ $rc -ne 0 ]; then
        # User cancelled component selection — treat as "use defaults"
        _selected_components=()
        while IFS=: read -r id label default_enabled description; do
            _selected_components+=("$id:$label:$default_enabled")
        done < "$HERE/components.cfg"
        return 0
    fi
    return 0
}

_page_summary() {
    local summary="Please review your selections before installing.

Installation Directory: $INSTALL_DIR"

    if [ "$HAS_COMPONENTS" = "true" ] && [ ${#_selected_components[@]} -gt 0 ]; then
        summary="$summary

Components to install:"
        for comp in "${_selected_components[@]}"; do
            IFS=: read -r id label enabled <<< "$comp"
            if [ "$enabled" = "true" ]; then
                summary="$summary\\n  - $label"
            fi
        done
    fi

    summary="$summary

Disk space required: approximately 500 MB"

    if ! _confirm "Ready to Install" "$summary\\n\\nProceed with installation?"; then
        return 1
    fi
    return 0
}

_page_progress() {
    _msg "Installing" "Installing $APP_NAME to $INSTALL_DIR..." "info"
    _extract_appimage "$INSTALL_DIR"
    _save_metadata "$INSTALL_DIR"
    _save_component_selection "$INSTALL_DIR"
    _run_post_install
    _install_desktop_entries "$INSTALL_DIR"
    _install_uninstall_entry "$INSTALL_DIR"
    _refresh_desktop_db
    return 0
}

_page_finish() {
    local msg
    if [ -n "$FINISH_MSG" ]; then
        msg="$FINISH_MSG"
    else
        msg="Setup Complete

$APP_NAME has been installed successfully.

  Location:  $INSTALL_DIR
  Launcher:  Desktop Menu
  Uninstall: Desktop Menu"
    fi
    if [ "$ENABLE_LAUNCH" = "true" ]; then
        if _ask_run "Run $APP_NAME?" "Launch $APP_NAME now?"; then
            "$INSTALL_DIR/AppRun" &
        fi
    fi
    _msg "Setup Complete" "$msg" "info"
    return 0
}

# ═══════════════════════════════════════════════
#  License + overwrite check (non-navigable)
# ═══════════════════════════════════════════════
if [ "$UNATTENDED" = "true" ]; then
    # Unattended: use defaults for everything
    INSTALL_DIR="$(_default_install_dir)"
    _handle_components 2>/dev/null || true
else
    # ── Page: Welcome ──
    _page_welcome
    rc=$?
    [ $rc -eq 2 ] && exit 1

    # ── Page: License ──
    _page_license
    rc=$?
    [ $rc -eq 2 ] && exit 1
    if [ $rc -ne 0 ]; then
        _msg "License Declined" "You must accept the license agreement to install." "error"
        exit 1
    fi

    # ── Page: Directory ──
    while true; do
        _page_directory
        rc=$?
        [ $rc -eq 2 ] && exit 1
        [ $rc -eq 0 ] && break
        # Back: go to license
        _page_license
        rc=$?
        [ $rc -eq 2 ] && exit 1
    done

    # ── Check if already installed ──
    if [ -f "$INSTALL_DIR/.installed" ]; then
        if ! _confirm "Already Installed" "$APP_NAME is already installed in: $INSTALL_DIR\\n\\nOverwrite?"; then
            exit 1
        fi
        rm -rf "$INSTALL_DIR"
    fi

    # ── Page: Components ──
    _page_components
    rc=$?
    [ $rc -eq 2 ] && exit 1

    # ── Page: Summary ──
    while true; do
        _page_summary
        rc=$?
        [ $rc -eq 0 ] && break
        # Back: go to components
        _page_components
        rc=$?
        [ $rc -eq 2 ] && exit 1
    done
fi

# ── Execute installation ──
_rollback_init "$INSTALL_DIR"
trap "_rollback_restore $INSTALL_DIR" EXIT
_run_pre_install

_page_progress

# ── Page: Finish ──
_page_finish
exit 0
'''


_INSTALLER_STYLES = {
    "wizard": "wizard",
    "macos": "macos",
    "minimal": "minimal",
    "installbuilder": "installbuilder",
    "qt6": "qt6",
}


def inject_bootstrap(appdir: str, app_name: str, app_version: str = "",
                     exec_name: str = "", installer_style: str = "wizard",
                     brand_name: str = "", license_file: str = "",
                     components: list = None,
                     pre_install_script: str = "",
                     post_install_script: str = "",
                     enable_rollback: bool = True,
                     enable_silent: bool = True,
                     updater_url: str = "",
                     welcome_message: str = "",
                     finish_message: str = "",
                     enable_launch_at_finish: bool = True):
    """Write AppRun, install.sh, and uninstall.sh into the AppDir.

    Args:
        appdir: Target AppDir path
        app_name: Application name
        app_version: Version string
        exec_name: Executable name within AppDir
        installer_style: 'qt6' | 'wizard' | 'macos' | 'minimal' | 'installbuilder'
        brand_name: Custom installer title (defaults to app_name)
        license_file: Path to EULA text file (embedded in install dir)
        components: List of dicts: [{'id','label','default':True,'description':''}]
        pre_install_script: Path or content of pre-install bash script
        post_install_script: Path or content of post-install bash script
        enable_rollback: Backup and restore on failure
        enable_silent: Support --unattended/--silent flag
        updater_url: URL to update.json manifest for automatic updates
        welcome_message: Custom welcome page text
        finish_message: Custom finish page text
        enable_launch_at_finish: Show launch prompt on finish
    """
    appdir_path = Path(appdir)
    install_dir = appdir_path / ".niruvi-install"
    install_dir.mkdir(parents=True, exist_ok=True)

    # Build config dict
    config = {
        "app_name": app_name,
        "app_version": app_version,
        "exec_name": exec_name or app_name,
        "installer_style": installer_style,
        "brand_name": brand_name or app_name,
        "license_content": None,
        "components": components or [],
        "pre_install_content": None,
        "post_install_content": None,
        "enable_rollback": enable_rollback,
        "enable_silent": enable_silent,
        "updater_url": updater_url,
        "welcome_message": welcome_message,
        "finish_message": finish_message,
        "enable_launch_at_finish": enable_launch_at_finish,
    }

    # Copy auxiliary files
    if license_file and os.path.isfile(license_file):
        dest = install_dir / "license.txt"
        dest.write_bytes(Path(license_file).read_bytes())
        config["license_content"] = "embedded"

    if components:
        cfg_lines = []
        for c in components:
            cid = c.get("id", "")
            label = c.get("label", cid)
            default = "true" if c.get("default", True) else "false"
            desc = c.get("description", "")
            cfg_lines.append(f"{cid}:{label}:{default}:{desc}")
        comp_path = install_dir / "components.cfg"
        comp_path.write_text("\n".join(cfg_lines) + "\n")

    if pre_install_script and os.path.isfile(pre_install_script):
        dest = install_dir / "pre-install.sh"
        dest.write_bytes(Path(pre_install_script).read_bytes())
        dest.chmod(0o755)
        config["pre_install_content"] = "embedded"

    if post_install_script and os.path.isfile(post_install_script):
        dest = install_dir / "post-install.sh"
        dest.write_bytes(Path(post_install_script).read_bytes())
        dest.chmod(0o755)
        config["post_install_content"] = "embedded"

    # Write config.json for the Python wizard
    config_json = {
        k: v for k, v in config.items()
        if k not in ("license_content", "pre_install_content", "post_install_content")
    }
    config_json["install_dir"] = os.path.expanduser("~/Applications")
    (install_dir / "config.json").write_text(
        __import__("json").dumps(config_json, indent=2)
    )

    # Inject self_install_wizard.py (standalone PyQt6 installer wizard)
    wizard_src_path = Path(__file__).parent / "self_installer_wizard.py"
    if wizard_src_path.exists():
        dest_wizard = install_dir / "self_install_wizard.py"
        dest_wizard.write_bytes(wizard_src_path.read_bytes())
        dest_wizard.chmod(0o755)

    # AppRun
    apprun_style = "qt6" if installer_style == "qt6" else "bash"
    apprun_content = _apprun_script(app_name, app_version, style=apprun_style)
    apprun_path = appdir_path / "AppRun"
    apprun_path.write_text(apprun_content)
    apprun_path.chmod(0o755)

    # install.sh — build from config (always keep as fallback)
    install_content = _build_install_script(config)
    install_path = install_dir / "install.sh"
    install_path.write_text(install_content)
    install_path.chmod(0o755)

    # uninstall.sh (always keep as fallback)
    uninstall_content = _uninstall_script(app_name)
    uninstall_path = install_dir / "uninstall.sh"
    uninstall_path.write_text(uninstall_content)
    uninstall_path.chmod(0o755)

    # update.sh (always keep as fallback)
    if updater_url:
        update_content = _updater_script(app_name, app_version, updater_url)
        update_path = install_dir / "update.sh"
        update_path.write_text(update_content)
        update_path.chmod(0o755)
