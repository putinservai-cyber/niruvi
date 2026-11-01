"""Tests for the sandbox resource limits and watchdog."""

import time

from niruvi.core.sandbox import Shield, ShieldConfig, _parse_size_limit, _ProcessWatchdog


class TestParseSizeLimit:
    def test_bytes(self):
        assert _parse_size_limit("1073741824") == 1073741824

    def test_kib_mib_gib(self):
        assert _parse_size_limit("512M") == 512 * 1024 * 1024
        assert _parse_size_limit("2G") == 2 * 1024 * 1024 * 1024
        assert _parse_size_limit("64k") == 64 * 1024

    def test_case_and_float(self):
        assert _parse_size_limit("1.5G") == int(1.5 * 1024**3)
        assert _parse_size_limit("512m") == 512 * 1024 * 1024

    def test_empty_and_invalid(self):
        assert _parse_size_limit("") is None
        assert _parse_size_limit("zzz") is None
        assert _parse_size_limit("12z") is None


class TestWatchdog:
    def test_kills_after_timeout(self):
        import signal
        import subprocess

        proc = subprocess.Popen(
            ["/bin/sh", "-c", "sleep 5"],
            start_new_session=True,
        )
        watchdog = _ProcessWatchdog(proc, 1)
        watchdog.start()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        assert proc.returncode is not None
        assert proc.returncode == -signal.SIGKILL.value

    def test_no_kill_when_exits_early(self):
        import subprocess

        proc = subprocess.Popen(
            ["/bin/sh", "-c", "exit 0"],
            start_new_session=True,
        )
        watchdog = _ProcessWatchdog(proc, 30)
        watchdog.start()
        proc.wait(timeout=5)
        assert proc.returncode == 0


class TestShieldRun:
    def test_timeout_enforced(self):
        cfg = ShieldConfig(enabled=True, backend="shield", timeout=1)
        sb = Shield(cfg)
        p = sb.run(["/bin/sleep", "10"], cwd="/tmp")
        assert p is not None
        t0 = time.time()
        p.wait(timeout=10)
        elapsed = time.time() - t0
        assert p.returncode is not None
        assert elapsed < 8

    def test_private_tmp_env(self, tmp_path):
        app_dir = str(tmp_path / "appdir")
        import os

        os.makedirs(app_dir)
        cfg = ShieldConfig(enabled=True, backend="shield", private_tmp=True, hardening=False)
        sb = Shield(cfg)
        out = tmp_path / "out.txt"
        p = sb.run(
            ["/bin/sh", "-c", f"echo $TMPDIR > {out}"],
            cwd=app_dir,
        )
        p.wait(timeout=5)
        assert p.returncode == 0
        assert out.read_text().strip() == os.path.join(app_dir, ".tmp")

    def test_memory_limit_preexec(self):
        import os

        cfg = ShieldConfig(enabled=True, backend="shield", memory_limit="64M", hardening=False)
        sb = Shield(cfg)
        out = "/tmp/niruvi-rlimit-test.txt"
        try:
            os.unlink(out)
        except OSError:
            pass
        p = sb.run(
            ["/bin/sh", "-c", f"ulimit -v; ulimit -d > {out}"],
            cwd="/tmp",
        )
        p.wait(timeout=5)
        assert p.returncode == 0
        # 64M == 65536 KB for RLIMIT_AS/-v
        assert open(out).read().strip() == "65536"
