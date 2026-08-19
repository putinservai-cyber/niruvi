"""Tests for JuNest container detection and path-with-spaces guidance."""

from niruvi.installer.junest import (
    is_junest_app,
    junest_destination_error,
    junest_install_warning,
    path_has_spaces,
    suggest_space_free_path,
)


class TestIsJunestApp:
    def test_dot_junest_marker(self, tmp_path):
        (tmp_path / ".junest" / "usr").mkdir(parents=True)
        assert is_junest_app(str(tmp_path))

    def test_local_share_junest_marker(self, tmp_path):
        (tmp_path / ".local" / "share" / "junest" / "bin").mkdir(parents=True)
        assert is_junest_app(str(tmp_path))

    def test_usr_share_junest_marker(self, tmp_path):
        (tmp_path / "usr" / "share" / "junest").mkdir(parents=True)
        assert is_junest_app(str(tmp_path))

    def test_namespace_sh_marker(self, tmp_path):
        core = tmp_path / "lib" / "core"
        core.mkdir(parents=True)
        (core / "namespace.sh").write_text("COMMON_BWRAP_OPTION=(...)\n")
        assert is_junest_app(str(tmp_path))

    def test_plain_appimage_not_junest(self, tmp_path):
        (tmp_path / "AppRun").write_text("#!/bin/sh\n")
        assert not is_junest_app(str(tmp_path))

    def test_missing_dir(self):
        assert not is_junest_app("/nonexistent/path")

    def test_empty_string(self):
        assert not is_junest_app("")


class TestPathHasSpaces:
    def test_spaces_detected(self):
        assert path_has_spaces("/home/user/My App")

    def test_no_spaces(self):
        assert not path_has_spaces("/home/user/MyApp")

    def test_empty(self):
        assert not path_has_spaces("")

    def test_tabs_detected(self):
        assert path_has_spaces("/home/user/My\tApp")


class TestSuggestSpaceFreePath:
    def test_replaces_spaces(self):
        assert suggest_space_free_path("/home/user/My App") == "/home/user/My-App"

    def test_collapses_multiple_spaces(self):
        assert suggest_space_free_path("/home/user/My  App") == "/home/user/My-App"

    def test_no_change_without_spaces(self):
        assert suggest_space_free_path("/home/user/MyApp") == "/home/user/MyApp"

    def test_keeps_directory_components(self):
        result = suggest_space_free_path("/media/Old Disk/My App")
        assert result == "/media/Old Disk/My-App"


class TestJunestInstallWarning:
    def test_warning_for_junest_in_spaced_path(self, tmp_path):
        (tmp_path / ".junest").mkdir()
        warning = junest_install_warning(str(tmp_path), "/home/user/My App")
        assert warning is not None
        assert "My-App" in warning

    def test_no_warning_without_spaces(self, tmp_path):
        (tmp_path / ".junest").mkdir()
        assert junest_install_warning(str(tmp_path), "/home/user/MyApp") is None

    def test_no_warning_for_plain_app(self, tmp_path):
        (tmp_path / "AppRun").write_text("#!/bin/sh\n")
        assert junest_install_warning(str(tmp_path), "/home/user/My App") is None


class TestJunestDestinationError:
    def _junest_dir(self, tmp_path, name="VLC media player"):
        d = tmp_path / name
        (d / ".junest" / "usr").mkdir(parents=True)
        return str(d)

    def test_blocking_error_on_space_path(self, tmp_path):
        app = tmp_path / "extracted"
        (app / ".junest").mkdir(parents=True)
        dest = str(tmp_path / "VLC media player")
        error = junest_destination_error(str(app), dest)
        assert error is not None
        assert "VLC-media-player" in error
        assert "bwrap" in error

    def test_no_error_without_spaces(self, tmp_path):
        app = tmp_path / "extracted"
        (app / ".junest").mkdir(parents=True)
        assert junest_destination_error(str(app), str(tmp_path / "VLC-media-player")) is None

    def test_no_error_for_plain_app(self, tmp_path):
        app = tmp_path / "plain"
        app.mkdir()
        (app / "AppRun").write_text("#!/bin/sh\n")
        assert junest_destination_error(str(app), str(tmp_path / "Plain App")) is None

    def test_no_error_without_spaces_no_junest(self, tmp_path):
        app = tmp_path / "plain"
        app.mkdir()
        assert junest_destination_error(str(app), str(tmp_path / "No Spaces")) is None

    def test_warning_matches_error(self, tmp_path):
        app = tmp_path / "extracted"
        (app / ".junest").mkdir(parents=True)
        dest = str(tmp_path / "VLC media player")
        assert junest_install_warning(str(app), dest) == junest_destination_error(str(app), dest)
