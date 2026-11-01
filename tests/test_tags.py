"""Tests for app tag inference and normalization."""

from niruvi.app.tags import DEFAULT_CATEGORIES, infer_tags, normalize_tags


class TestInferTags:
    def test_known_keyword(self):
        assert infer_tags("Steam") == ["Games"]
        assert infer_tags("Kdenlive") == ["Media"]
        assert infer_tags("Visual Studio Code") == ["Dev"]
        assert infer_tags("Blender") == ["Graphics"]
        assert infer_tags("LibreOffice Writer") == ["Office"]
        assert infer_tags("Firefox Browser") == ["Internet"]
        assert infer_tags("Discord") == ["Social"]
        assert infer_tags("System Monitor") == ["System"]

    def test_order_first_match_wins(self):
        assert infer_tags("Godot Engine") == ["Games"]

    def test_unknown_returns_empty(self):
        assert infer_tags("Something Random") == []
        assert infer_tags("") == []
        assert infer_tags(None) == []

    def test_substring_match(self):
        assert infer_tags("OpenCode Terminal") == ["Dev"]


class TestNormalizeTags:
    def test_dedupe_and_strip(self):
        assert normalize_tags([" Media ", "media", "Media", ""]) == ["Media"]

    def test_known_categories_title_cased(self):
        assert normalize_tags(["games", "dev"]) == ["Games", "Dev"]

    def test_freeform_preserved(self):
        assert normalize_tags(["Games", "my-own", "  ", "My Own"]) == ["Games", "my-own", "My Own"]

    def test_case_sensitive_freeform_deduped(self):
        result = normalize_tags(["Web", "web"])
        assert result == ["Web"]

    def test_empty(self):
        assert normalize_tags(None) == []
        assert normalize_tags([]) == []
        assert normalize_tags(["", "  "]) == []

    def test_non_string_entries_coerced(self):
        assert normalize_tags([42, "Games"]) == ["42", "Games"]

    def test_default_categories_are_known_and_unique(self):
        assert len(DEFAULT_CATEGORIES) == len(set(DEFAULT_CATEGORIES))
        assert "Utility" in DEFAULT_CATEGORIES
        for cat in DEFAULT_CATEGORIES:
            assert cat.istitle()
            assert normalize_tags([cat.lower()]) == [cat]
