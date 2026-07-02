"""Tests for the Signing Engine."""

import os
import tempfile

import pytest

from niruvi.core.signing import (
    gpg_available, list_secret_keys, get_default_key,
    SigningKey, SigningError, _extract_fingerprint_from_import,
    signing_info_for_manifest,
)


class TestGPGDetection:
    def test_gpg_available(self):
        result = gpg_available()
        assert isinstance(result, bool)


class TestKeyListing:
    def test_list_secret_keys(self):
        keys = list_secret_keys()
        assert isinstance(keys, list)
        for k in keys:
            assert isinstance(k, SigningKey)
            assert isinstance(k.fingerprint, str)
            assert isinstance(k.name, str)

    def test_get_default_key(self):
        key = get_default_key()
        if key is not None:
            assert key.can_sign is True
            assert len(key.fingerprint) > 0


class TestExtractFingerprint:
    def test_extract_from_stderr(self):
        stderr = (
            'gpg: key C0FFEE1234567890: public key "Test <test@example>" imported\n'
            'gpg: Total number processed: 1\n'
            'gpg:               imported: 1\n'
            'gpg: marginals needed: 3\n'
            'gpg: depth: 0  valid:   1  signed:   0  trust: 0-, 0q, 0n, 0m, 0f, 1u\n'
            'gpg: next trustdb check due at 2027-01-01\n'
            '             Fingerprint: DEAD BEEF 1234 5678 90AB  CDEF 1234 5678 9ABC DEF0'
        )
        fp = _extract_fingerprint_from_import(stderr)
        assert fp == "DEADBEEF1234567890ABCDEF123456789ABCDEF0"

    def test_extract_no_fingerprint(self):
        assert _extract_fingerprint_from_import("nothing here") == ""


class TestSigningInfo:
    def test_manifest_info(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sig") as f:
            f.write(b"fake signature data")
            sig_path = f.name
        try:
            info = signing_info_for_manifest("A" * 40, sig_path)
            assert info["algorithm"] == "gpg"
            assert info["fingerprint"] == "A" * 40
            assert len(info["signature"]) == 64
        finally:
            os.unlink(sig_path)

    def test_manifest_info_missing_sig(self):
        info = signing_info_for_manifest("A" * 40, "/nonexistent/file.sig")
        assert info["signature"] == ""


class TestSigningError:
    def test_signing_error_is_exception(self):
        assert issubclass(SigningError, Exception)

    def test_sign_file_no_gpg_raises(self, monkeypatch):
        monkeypatch.setattr("niruvi.core.signing.gpg_available", lambda: False)
        from niruvi.core.signing import sign_file
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            p = f.name
        try:
            with pytest.raises(SigningError, match="GPG is not available"):
                sign_file(p)
        finally:
            os.unlink(p)
