"""Tests for the VirusTotal integration — upload/poll flow and error paths
are exercised against a mocked transport (no network)."""

import niruvi.app.virustotal as vt


def _make_file(tmp_path, size=1024):
    path = tmp_path / "fake.AppImage"
    path.write_bytes(b"x" * size)
    return str(path)


class TestScanFile:
    def test_no_key_skips(self, tmp_path):
        result = vt.scan_file(_make_file(tmp_path), "")
        assert result["status"] == "skipped"
        assert "no API key" in result["error"]

    def test_missing_file_error_path(self, tmp_path):
        result = vt.scan_file(str(tmp_path / "missing"), "key")
        assert result["status"] == "skipped"

    def test_upload_and_poll_flagged(self, tmp_path, monkeypatch):
        calls = []

        def fake_request(method, path, key, body=None, content_type="", timeout=60):
            calls.append((method, path))
            if path == "files":
                assert "boundary=niruvi" in content_type
                assert b"Content-Type: application/octet-stream" in body
                assert b'filename="fake.AppImage"' in body
                return {"data": {"id": "A-123"}}
            if path == "analyses/A-123":
                return {
                    "data": {
                        "attributes": {
                            "status": "completed",
                            "stats": {"malicious": 3, "suspicious": 1, "harmless": 40, "undetected": 8},
                        }
                    }
                }
            raise AssertionError(path)

        monkeypatch.setattr(vt, "_request", fake_request)
        result = vt.scan_file(_make_file(tmp_path), "key")
        assert result["status"] == "flagged"
        assert result["malicious"] == 3
        assert result["permalink"].startswith("https://www.virustotal.com/gui/file/")
        assert len(result["sha256"]) == 64
        assert calls == [("POST", "files"), ("GET", "analyses/A-123")]

    def test_clean_result(self, tmp_path, monkeypatch):
        def fake_request(method, path, key, body=None, content_type="", timeout=60):
            if path == "files":
                return {"data": {"id": "A-1"}}
            return {
                "data": {
                    "attributes": {
                        "status": "completed",
                        "stats": {"malicious": 0, "suspicious": 0, "harmless": 60, "undetected": 5},
                    }
                }
            }

        monkeypatch.setattr(vt, "_request", fake_request)
        result = vt.scan_file(_make_file(tmp_path), "key")
        assert result["status"] == "clean"

    def test_rate_limit_skips(self, tmp_path, monkeypatch):
        def fake_request(method, path, key, body=None, content_type="", timeout=60):
            raise vt.VirusTotalError("rate limit exceeded")

        monkeypatch.setattr(vt, "_request", fake_request)
        result = vt.scan_file(_make_file(tmp_path), "key")
        assert result["status"] == "skipped"
        assert "rate limit" in result["error"]

    def test_poll_timeout_skips(self, tmp_path, monkeypatch):
        def fake_request(method, path, key, body=None, content_type="", timeout=60):
            if path == "files":
                return {"data": {"id": "A-9"}}
            return {"data": {"attributes": {"status": "queued"}}}

        monkeypatch.setattr(vt, "_request", fake_request)
        monkeypatch.setattr(vt, "POLL_TIMEOUT", 1)
        monkeypatch.setattr(vt, "POLL_INTERVAL", 0.01)
        result = vt.scan_file(_make_file(tmp_path), "key")
        assert result["status"] == "skipped"
        assert "timed out" in result["error"]


class TestKnownHash:
    def test_lookup(self, monkeypatch):
        def fake_request(method, path, key, body=None, content_type="", timeout=60):
            assert path == "files/" + "ab" * 32
            return {
                "data": {
                    "attributes": {
                        "last_analysis_stats": {"malicious": 0, "suspicious": 0, "harmless": 55, "undetected": 5}
                    }
                }
            }

        monkeypatch.setattr(vt, "_request", fake_request)
        result = vt.scan_known_hash("ab" * 32, "key")
        assert result["status"] == "clean"

    def test_lookup_error(self, monkeypatch):
        def fake_request(method, path, key, body=None, content_type="", timeout=60):
            raise vt.VirusTotalError("HTTP 404")

        monkeypatch.setattr(vt, "_request", fake_request)
        result = vt.scan_known_hash("ab" * 32, "key")
        assert result["status"] == "skipped"


class TestKey:
    def test_valid_key(self, monkeypatch):
        monkeypatch.setattr(vt, "_request", lambda *a, **k: {"data": {}})
        assert vt.test_key("valid") is True

    def test_invalid_key(self, monkeypatch):
        def fake_request(*a, **k):
            raise vt.VirusTotalError("invalid API key")

        monkeypatch.setattr(vt, "_request", fake_request)
        assert vt.test_key("bad") is False

    def test_network_error_not_locking_out(self, monkeypatch):
        def fake_request(*a, **k):
            raise vt.VirusTotalError("network error: timeout")

        monkeypatch.setattr(vt, "_request", fake_request)
        assert vt.test_key("key") is True
