"""Tests for generated installer shell scripts (path-with-spaces safety)."""

from niruvi.installer.scripts import build_install_script, uninstall_script, updater_script

SPACED_CONFIG = {
    "app_name": "VLC media player",
    "app_version": "4.0.0",
    "installer_style": "wizard",
    "brand_name": "VLC",
    "enable_rollback": True,
    "enable_silent": True,
    "updater_url": "https://example.com/updates/update.json",
    "welcome_message": "Welcome",
    "finish_message": "Done",
    "enable_launch_at_finish": True,
    "exec_name": "vlc",
    "license_content": None,
    "pre_install_content": None,
    "post_install_content": None,
    "components": [],
}


class TestRollbackTrapQuoting:
    def test_trap_uses_single_quotes(self):
        for style in ("wizard", "macos", "minimal", "installbuilder"):
            config = dict(SPACED_CONFIG, installer_style=style)
            script = build_install_script(config)
            assert 'trap "_rollback_restore $INSTALL_DIR" EXIT' not in script, style
            assert "trap '_rollback_restore \"$INSTALL_DIR\"' EXIT" in script, style

    def test_no_unquoted_dollar_in_trap(self):
        script = build_install_script(SPACED_CONFIG)
        assert "_rollback_restore $INSTALL_DIR" not in script


class TestMinimalLocalKeyword:
    def test_no_top_level_local_keyword(self):
        config = dict(SPACED_CONFIG, installer_style="minimal")
        script = build_install_script(config)
        # `local` outside a function aborts the script under `set -e`
        lines = script.splitlines()
        func_depth = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("local "):
                assert func_depth > 0, f"'local' used outside a function: {stripped}"
            if stripped.startswith("}") and func_depth > 0:
                func_depth -= 1
            elif stripped.endswith("()") or stripped.endswith("() {"):
                func_depth += 1


class TestDesktopEntryQuoting:
    def test_exec_and_icon_quoted(self):
        script = build_install_script(SPACED_CONFIG)
        assert 'Exec="$target/AppRun" %F' in script
        assert 'Icon="$icon_path"' in script
        assert 'Exec="$uninstall_script"' in script


class TestUpdaterScript:
    def test_url_preserved_not_stripped(self):
        script = updater_script("VLC media player", "1.0", "https://example.com/up.json")
        assert 'UPDATE_URL="https://example.com/up.json"' in script

    def test_url_escapes_quotes(self):
        script = updater_script("VLC", "1.0", 'https://example.com/x?a="1"')
        assert '\\"' in script.split("UPDATE_URL=")[1]

    def test_fetch_uses_wget_fallback(self):
        script = updater_script("VLC", "1.0", "https://example.com/up.json")
        assert "wget -q -O -" in script

    def test_no_remote_ver_injection_in_python(self):
        script = updater_script("VLC", "1.0", "https://example.com/up.json")
        # REMOTE_VER must be passed via environment, never interpolated into python code
        assert 'NIRUVI_REMOTE_VER="$REMOTE_VER"' in script
        assert "d['version'] = '$REMOTE_VER'" not in script

    def test_junest_warning_in_generated_install(self):
        script = build_install_script(SPACED_CONFIG)
        assert ".junest" in script
        assert "Container Warning" in script


class TestUninstallScript:
    def test_generates_with_spaced_name(self):
        script = uninstall_script("VLC media player")
        assert 'APP_NAME="VLC media player"' in script
        assert "Uninstalled" in script
