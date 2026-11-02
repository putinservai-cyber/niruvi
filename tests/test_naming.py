"""Tests for niruvi.naming module."""

from pathlib import Path

import pytest

from niruvi.naming import assert_no_spaces, safe_name, side_by_side_name


class TestSafeName:
    def test_basic_spaces(self):
        assert safe_name("VLC media player") == "VLC-media-player"

    def test_unicode_accents(self):
        # Unicode accents should be normalized to ASCII
        result = safe_name("Café Télé")
        assert result == "Cafe-Tele"

    def test_only_punctuation(self):
        assert safe_name("!!!") == "app"
        assert safe_name("...") == "app"
        assert safe_name("@#$%") == "app"

    def test_already_safe(self):
        assert safe_name("MyApp") == "MyApp"
        assert safe_name("my-app") == "my-app"
        assert safe_name("my_app") == "my_app"
        assert safe_name("app.test") == "app.test"

    def test_multiple_spaces(self):
        assert safe_name("a  b  c") == "a-b-c"

    def test_leading_trailing_spaces(self):
        assert safe_name("  app  ") == "app"

    def test_unicode_normalization(self):
        # NFKD should decompose composed characters
        assert safe_name("café") == "cafe"
        assert safe_name("naïve") == "naive"

    def test_empty_string(self):
        assert safe_name("") == "app"

    def test_non_string_input(self):
        assert safe_name(123) == "123"
        assert safe_name(None) == "None"


class TestSideBySideName:
    def test_basic(self):
        assert side_by_side_name("App", 2) == "App-2"

    def test_with_spaces(self):
        assert side_by_side_name("My App", 2) == "My-App-2"

    def test_multiple(self):
        assert side_by_side_name("App", 3) == "App-3"
        assert side_by_side_name("App", 4) == "App-4"
        assert side_by_side_name("App", 5) == "App-5"

    def test_with_special_chars(self):
        assert side_by_side_name("Café App", 2) == "Cafe-App-2"


class TestAssertNoSpaces:
    def test_passes_on_safe_path(self):
        assert_no_spaces(Path("a-b"))
        assert_no_spaces(Path("a_b"))
        assert_no_spaces(Path("a.b"))
        assert_no_spaces(Path("a-b-c"))
        assert_no_spaces(Path("a-b/c-d"))  # path with multiple components

    def test_raises_on_space(self):
        with pytest.raises(ValueError):
            assert_no_spaces(Path("a b"))
        with pytest.raises(ValueError):
            assert_no_spaces(Path("a/b c"))  # space in component
        with pytest.raises(ValueError):
            assert_no_spaces(Path("a/b/c d"))  # space in last component

    def test_error_message(self):
        with pytest.raises(ValueError) as exc:
            assert_no_spaces(Path("my app"))
        assert "my app" in str(exc.value)
