"""Tests for the Verification Engine."""

import os
import tempfile

import pytest

from niruvi.core.verification import (
    sha256_file, verify_sha256, verify_essential_files,
    verify_apprun_executable, VerificationResult,
)


class TestSHA256:
    def test_sha256_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            tmp_path = f.name
        try:
            h = sha256_file(tmp_path)
            assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        finally:
            os.unlink(tmp_path)

    def test_sha256_empty_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp_path = f.name
        try:
            h = sha256_file(tmp_path)
            assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        finally:
            os.unlink(tmp_path)

    def test_sha256_large_chunks(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"a" * 100000)
            tmp_path = f.name
        try:
            h = sha256_file(tmp_path, chunk_size=4096)
            assert len(h) == 64
            assert h == sha256_file(tmp_path, chunk_size=65536)
        finally:
            os.unlink(tmp_path)


class TestVerifySHA256:
    def test_verify_match(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            tmp_path = f.name
        try:
            expected = sha256_file(tmp_path)
            result = verify_sha256(tmp_path, expected)
            assert result.passed is True
        finally:
            os.unlink(tmp_path)

    def test_verify_mismatch(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test data")
            tmp_path = f.name
        try:
            result = verify_sha256(tmp_path, "0" * 64)
            assert result.passed is False
            assert len(result.errors) > 0
        finally:
            os.unlink(tmp_path)


class TestVerifyEssentialFiles:
    def test_all_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            apprun = os.path.join(tmpdir, "AppRun")
            with open(apprun, "w") as f:
                f.write("#!/bin/bash\necho hello")
            os.chmod(apprun, 0o755)
            result = verify_essential_files(tmpdir)
            assert result.passed is True

    def test_missing_apprun(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = verify_essential_files(tmpdir)
            assert result.passed is False


class TestVerifyAppRun:
    def test_executable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            apprun = os.path.join(tmpdir, "AppRun")
            with open(apprun, "w") as f:
                f.write("#!/bin/bash")
            os.chmod(apprun, 0o755)
            result = verify_apprun_executable(tmpdir)
            assert result.passed is True

    def test_not_executable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            apprun = os.path.join(tmpdir, "AppRun")
            with open(apprun, "w") as f:
                f.write("#!/bin/bash")
            os.chmod(apprun, 0o644)
            result = verify_apprun_executable(tmpdir)
            assert result.passed is False

    def test_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = verify_apprun_executable(tmpdir)
            assert result.passed is False


class TestVerificationResult:
    def test_bool_true(self):
        assert VerificationResult(True, "OK")

    def test_bool_false(self):
        assert not VerificationResult(False, "Failed")

    def test_errors_list(self):
        r = VerificationResult(False, "Failed", ["err1", "err2"])
        assert len(r.errors) == 2
