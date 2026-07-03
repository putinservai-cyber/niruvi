"""Tests for the hooks system — security, execution, and management."""

import os
import stat
import tempfile

from niruvi.core.hooks import (
    _check_hook_secure,
    ensure_hooks_dir,
    list_hooks,
    remove_hook,
    run_hooks,
    write_hook,
)


class TestHookSecurity:
    def test_hook_owned_by_user_passes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".hook", delete=False) as f:
            f.write("#!/bin/bash\necho ok")
            path = f.name
        try:
            os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
            assert _check_hook_secure(path) is True
        finally:
            os.unlink(path)

    def test_world_writable_hook_fails(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".hook", delete=False) as f:
            f.write("#!/bin/bash\necho ok")
            path = f.name
        try:
            os.chmod(path, stat.S_IRWXU | stat.S_IWOTH)
            assert _check_hook_secure(path) is False
        finally:
            os.unlink(path)

    def test_nonexistent_hook_fails(self):
        assert _check_hook_secure("/nonexistent/hook.hook") is False

    def test_hook_not_executable_still_passes_security(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".hook", delete=False) as f:
            f.write("#!/bin/bash\necho ok")
            path = f.name
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            assert _check_hook_secure(path) is True
        finally:
            os.unlink(path)


class TestHookManagement:
    def test_write_and_list_hook(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import niruvi.core.hooks as hooks_mod
            orig = hooks_mod.HOOKS_DIR
            hooks_mod.HOOKS_DIR = os.path.join(tmpdir, "hooks")
            try:
                path = write_hook("testapp", "prelaunch", "#!/bin/bash\necho test")
                assert os.path.isfile(path)
                hooks = list_hooks("testapp")
                assert len(hooks) >= 1
                assert any("prelaunch" in h for h in hooks)
            finally:
                hooks_mod.HOOKS_DIR = orig

    def test_remove_hook(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import niruvi.core.hooks as hooks_mod
            orig = hooks_mod.HOOKS_DIR
            hooks_mod.HOOKS_DIR = os.path.join(tmpdir, "hooks")
            try:
                path = write_hook("testapp", "toremove", "#!/bin/bash\necho x")
                assert os.path.isfile(path)
                assert remove_hook("testapp", "toremove.hook") is True
                assert not os.path.isfile(path)
            finally:
                hooks_mod.HOOKS_DIR = orig

    def test_ensure_hooks_dir_creates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import niruvi.core.hooks as hooks_mod
            orig = hooks_mod.HOOKS_DIR
            hooks_mod.HOOKS_DIR = os.path.join(tmpdir, "niruvi_hooks")
            try:
                d = ensure_hooks_dir()
                assert os.path.isdir(d)
            finally:
                hooks_mod.HOOKS_DIR = orig


class TestRunHooks:
    def test_run_hooks_empty_when_no_hooks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import niruvi.core.hooks as hooks_mod
            orig = hooks_mod.HOOKS_DIR
            hooks_mod.HOOKS_DIR = os.path.join(tmpdir, "hooks")
            try:
                results = run_hooks("nonexistent", "/tmp")
                assert results == []
            finally:
                hooks_mod.HOOKS_DIR = orig

    def test_skips_insecure_hook(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import niruvi.core.hooks as hooks_mod
            orig = hooks_mod.HOOKS_DIR
            hooks_mod.HOOKS_DIR = os.path.join(tmpdir, "hooks")
            try:
                hook_dir = ensure_hooks_dir("testapp")
                path = os.path.join(hook_dir, "evil.hook")
                with open(path, "w") as f:
                    f.write("#!/bin/bash\necho pwned")
                os.chmod(path, stat.S_IRWXU | stat.S_IWOTH)
                results = run_hooks("testapp", "/tmp")
                assert len(results) >= 1
                assert "skipped" in results[0].get("stderr", "")
            finally:
                hooks_mod.HOOKS_DIR = orig
