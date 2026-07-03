"""Tests for the update source resolution module."""


from niruvi.app.update_sources import (
    _get_arch_filter,
    _score_asset,
    detect_source_type,
    normalize_update_url,
    parse_github_repo,
    parse_gitlab_project,
)


class TestDetectSourceType:
    def test_github_url(self):
        assert detect_source_type("https://github.com/user/repo") == "github"

    def test_gitlab_url(self):
        assert detect_source_type("https://gitlab.com/user/project") == "gitlab"

    def test_direct_url(self):
        assert detect_source_type("https://example.com/file.AppImage") == "direct"


class TestParseGitHubRepo:
    def test_standard_url(self):
        r = parse_github_repo("https://github.com/user/repo")
        assert r == ("user", "repo")

    def test_with_git_suffix(self):
        r = parse_github_repo("https://github.com/user/repo.git")
        assert r == ("user", "repo")

    def test_with_trailing_slash(self):
        r = parse_github_repo("https://github.com/user/repo/")
        assert r == ("user", "repo")

    def test_no_match(self):
        assert parse_github_repo("https://example.com") is None


class TestParseGitLabProject:
    def test_standard_url(self):
        r = parse_gitlab_project("https://gitlab.com/user/project")
        assert r == "user/project"

    def test_no_match(self):
        assert parse_gitlab_project("https://example.com") is None


class TestNormalizeUpdateUrl:
    def test_adds_https(self):
        assert normalize_update_url("github.com/user/repo").startswith("https://")

    def test_removes_trailing_git(self):
        url = normalize_update_url("https://github.com/user/repo.git")
        assert not url.endswith(".git")

    def test_removes_trailing_releases(self):
        url = normalize_update_url("https://github.com/user/repo/releases")
        assert not url.endswith("/releases")

    def test_strips_trailing_slash(self):
        url = normalize_update_url("https://github.com/user/repo/")
        assert not url.endswith("/")


class TestScoreAsset:
    def test_prefers_appimage(self):
        score = _score_asset("myapp-x86_64.AppImage", "x86_64")
        assert score > 0

    def test_preferred_arch_scores_higher(self):
        appimage = _score_asset("myapp-x86_64.AppImage", "x86_64")
        other = _score_asset("myarm.AppImage", "x86_64")
        assert appimage > other

    def test_beta_penalized(self):
        stable = _score_asset("myapp-x86_64.AppImage", "x86_64")
        beta = _score_asset("myapp-x86_64-beta.AppImage", "x86_64")
        assert stable > beta


class TestGetArchFilter:
    def test_returns_string(self):
        arch = _get_arch_filter()
        assert isinstance(arch, str)
        assert len(arch) > 0
