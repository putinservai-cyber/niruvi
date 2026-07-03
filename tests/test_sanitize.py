"""Tests for the input sanitizer module."""


from niruvi.installer.sanitize import sanitize_bash_string


class TestSanitizeBashString:
    def test_safe_string_passes(self):
        assert sanitize_bash_string("MyApp", "app_name") == "MyApp"

    def test_safe_string_with_special_chars(self):
        assert sanitize_bash_string("My App 2.0_rc1", "app_name") == "My App 2.0_rc1"

    def test_strips_unsafe_chars(self):
        result = sanitize_bash_string('foo;rm $(id) `pwd`', "test")
        assert ";" not in result
        assert "$" not in result
        assert "`" not in result



    def test_strips_shell_metacharacters(self):
        result = sanitize_bash_string("$(id)", "test")
        assert "$" not in result
        assert "(" not in result
        assert result == "id"

    def test_strips_backtick(self):
        result = sanitize_bash_string("`id`", "test")
        assert "`" not in result
        assert result == "id"

    def test_empty_input_returns_empty(self):
        assert sanitize_bash_string("", "empty_field") == ""

    def test_truncates_long_values(self):
        long_val = "a" * 500
        result = sanitize_bash_string(long_val, "long")
        assert len(result) == 200

    def test_all_unsafe_becomes_empty(self):
        result = sanitize_bash_string("$(`;|&", "unsafe")
        assert result == ""

    def test_newline_stripped(self):
        result = sanitize_bash_string("hello\nworld", "nl")
        assert "\n" not in result

    def test_safe_chars_preserved(self):
        safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._+-@%/:=,"
        result = sanitize_bash_string(safe, "safe")
        assert result == safe

    def test_quotes_stripped(self):
        result = sanitize_bash_string('echo "hello"', "quotes")
        assert '"' not in result
        assert "'" not in result
