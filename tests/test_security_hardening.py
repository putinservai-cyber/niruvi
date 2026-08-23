"""Security-hardening regression tests.

Covers:
  - XdgOpenDaemon URL filter: DNS-resolution of hostnames so names pointing
    at link-local/metadata space cannot bypass the private-range blocklist.
  - Checksum sidecar discovery for GitLab/direct update sources.
  - Confined (--appimage-extract) execution in the thumbnailer.
"""

import socket

import pytest

from niruvi.core.sandbox import XdgOpenDaemon
from niruvi.desktop.thumbnailer import _build_extract_command


def _fake_resolver(mapping):
    """Return a getaddrinfo replacement honoring {host: [addr, ...]}."""

    def _getaddrinfo(host, *args, **kwargs):
        addrs = mapping.get(host)
        if addrs is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addrs]

    return _getaddrinfo


class TestAddrAllowed:
    def test_loopback_allowed(self):
        assert XdgOpenDaemon._addr_allowed("127.0.0.1")
        assert XdgOpenDaemon._addr_allowed("::1")

    @pytest.mark.parametrize(
        "addr",
        [
            "10.0.0.1",
            "192.168.1.1",
            "172.16.0.5",
            "169.254.169.254",  # cloud metadata endpoint
            "100.64.0.1",  # CGNAT
            "fd00::1",
            "fe80::1",
        ],
    )
    def test_private_and_reserved_blocked(self, addr):
        assert not XdgOpenDaemon._addr_allowed(addr)

    def test_ipv4_mapped_ipv6_blocked(self):
        assert not XdgOpenDaemon._addr_allowed("::ffff:10.0.0.1")

    def test_public_ip_allowed(self):
        assert XdgOpenDaemon._addr_allowed("8.8.8.8")
        assert XdgOpenDaemon._addr_allowed("2606:4700:4700::1111")

    def test_multicast_and_unspecified_blocked(self):
        assert not XdgOpenDaemon._addr_allowed("224.0.0.1")
        assert not XdgOpenDaemon._addr_allowed("0.0.0.0")

    def test_garbage_rejected(self):
        assert not XdgOpenDaemon._addr_allowed("not-an-ip")


class TestHostAllowed:
    def test_empty_hostname(self):
        assert not XdgOpenDaemon._host_allowed(None)
        assert not XdgOpenDaemon._host_allowed("")

    def test_localhost_names_without_dns(self):
        assert XdgOpenDaemon._host_allowed("localhost")
        assert XdgOpenDaemon._host_allowed("foo.localhost")
        assert XdgOpenDaemon._host_allowed("[::1]")

    def test_mdns_denied(self):
        assert not XdgOpenDaemon._host_allowed("printer.local")

    def test_ip_literal_metadata(self):
        assert not XdgOpenDaemon._host_allowed("169.254.169.254")

    def test_dns_name_pointing_at_metadata_blocked(self, monkeypatch):
        # e.g. 169.254.169.254.nip.io style rebinding to the metadata service
        monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver({"rebind.example": ["169.254.169.254"]}))
        assert not XdgOpenDaemon._host_allowed("rebind.example")

    def test_dns_name_pointing_at_intranet_blocked(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver({"intranet.example": ["192.168.1.10"]}))
        assert not XdgOpenDaemon._host_allowed("intranet.example")

    def test_dns_name_public_allowed(self, monkeypatch):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            _fake_resolver({"example.com": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]}),
        )
        assert XdgOpenDaemon._host_allowed("example.com")

    def test_mixed_resolution_blocked(self, monkeypatch):
        # One bad record poisons the whole name
        monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver({"mixed.example": ["93.184.216.34", "10.9.8.7"]}))
        assert not XdgOpenDaemon._host_allowed("mixed.example")

    def test_unresolvable_blocked(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver({}))
        assert not XdgOpenDaemon._host_allowed("nonexistent.invalid")


class TestSafeUrlIntegration:
    def test_schemeless_still_blocked(self):
        assert not XdgOpenDaemon._is_safe_url("169.254.169.254/latest/meta-data/")

    def test_file_scheme_blocked(self):
        assert not XdgOpenDaemon._is_safe_url("file:///etc/passwd")

    def test_metadata_url_blocked(self):
        assert not XdgOpenDaemon._is_safe_url("http://169.254.169.254/latest/meta-data/")

    def test_loopback_url_allowed(self):
        assert XdgOpenDaemon._is_safe_url("http://localhost:8080/oauth/callback")

    def test_public_url_resolves_before_allow(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver({"metadata.evil": ["169.254.169.254"]}))
        assert not XdgOpenDaemon._is_safe_url("http://metadata.evil/latest/meta-data/")


class TestParseSha256Text:
    def test_plain_hash(self):
        from niruvi.app.update_sources import _parse_sha256_text

        h = "a" * 64
        assert _parse_sha256_text(h) == h

    def test_bsd_style(self):
        from niruvi.app.update_sources import _parse_sha256_text

        h = "B" * 64
        assert _parse_sha256_text(f"SHA256 (my.AppImage) = {h}") == h.lower()

    def test_hash_with_filename(self):
        from niruvi.app.update_sources import _parse_sha256_text

        h = "c" * 64
        assert _parse_sha256_text(f"{h} *myapp-x86_64.AppImage") == h

    def test_no_hash(self):
        from niruvi.app.update_sources import _parse_sha256_text

        assert _parse_sha256_text("no digest here") is None
        assert _parse_sha256_text("") is None


class TestFetchSidecarSha256:
    def test_finds_first_sidecar(self, monkeypatch):
        from niruvi.app import update_sources

        h = "d" * 64

        class Resp:
            def read(self, n=-1):
                return f"{h}  my.AppImage\n".encode()

        calls = []

        def fake_urlopen(req, timeout=0, context=None):
            calls.append(req.full_url)
            if req.full_url.endswith(".sha256"):
                return Resp()
            raise OSError("HTTP 404")

        monkeypatch.setattr(update_sources.urllib.request, "urlopen", fake_urlopen)
        assert update_sources.fetch_sidecar_sha256("https://gitlab.com/x/y/-/releases/v1/download") == h
        assert calls[0].endswith(".sha256")

    def test_falls_through_to_sha256sum(self, monkeypatch):
        from niruvi.app import update_sources

        h = "e" * 64

        class Resp:
            def read(self, n=-1):
                return h.encode()

        def fake_urlopen(req, timeout=0, context=None):
            if req.full_url.endswith(".sha256"):
                raise OSError("404")
            assert req.full_url.endswith(".sha256sum")
            return Resp()

        monkeypatch.setattr(update_sources.urllib.request, "urlopen", fake_urlopen)
        assert update_sources.fetch_sidecar_sha256("https://x.example/app.AppImage") == h

    def test_none_when_no_sidecar(self, monkeypatch):
        from niruvi.app import update_sources

        def fake_urlopen(req, timeout=0, context=None):
            raise OSError("404")

        monkeypatch.setattr(update_sources.urllib.request, "urlopen", fake_urlopen)
        assert update_sources.fetch_sidecar_sha256("https://x.example/app.AppImage") is None


class TestUpdateResultCarriesHash:
    def test_dataclass_field(self):
        from niruvi.app.background_updater import UpdateResult

        r = UpdateResult("app", "1.0", "2.0", "https://x/a.AppImage", sha256="f" * 64)
        assert r.sha256 == "f" * 64
        assert UpdateResult("app", "1.0", "2.0", "u").sha256 is None


class TestThumbnailerExtractionCommand:
    APP = "/apps/mytool/mytool.AppImage"

    def test_direct_when_no_bwrap(self, monkeypatch):
        monkeypatch.setattr("niruvi.desktop.thumbnailer.shutil.which", lambda _: None)
        cmd = _build_extract_command(self.APP, "/tmp/ex")
        assert cmd == [self.APP, "--appimage-extract"]

    def test_confined_command_shape(self, monkeypatch):
        monkeypatch.setattr("niruvi.desktop.thumbnailer.shutil.which", lambda name: "/usr/bin/bwrap")
        cmd = _build_extract_command(self.APP, "/tmp/ex")
        assert cmd[0] == "/usr/bin/bwrap"
        assert "--unshare-all" in cmd
        assert "--die-with-parent" in cmd
        # image dir mounted read-only, extraction dir writable at same path
        i = cmd.index("--ro-bind")
        assert cmd[i + 1 : i + 3] == ["/apps/mytool", "/apps/mytool"]
        j = cmd.index("--bind")
        assert cmd[j + 1 : j + 3] == ["/tmp/ex", "/tmp/ex"]
        k = cmd.index("--chdir")
        assert cmd[k + 1] == "/tmp/ex"
        # payload appended verbatim at the end
        assert cmd[-2:] == [self.APP, "--appimage-extract"]
        # no network namespace shared and env cleared
        assert "--share-net" not in cmd
        assert "--clearenv" in cmd
