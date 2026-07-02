"""Tests for the Repair Engine."""

import os
import stat
import tempfile

import pytest

from niruvi.core.repair import RepairAction, RepairReport, repair_apprun


class TestRepairAction:
    def test_success(self):
        action = RepairAction("test", lambda: True)
        assert action.execute() is True
        assert action.success is True
        assert action.error is None

    def test_failure(self):
        action = RepairAction("test", lambda: False)
        assert action.execute() is False
        assert action.success is False

    def test_exception(self):
        def _fail():
            raise ValueError("oops")
        action = RepairAction("test", _fail)
        assert action.execute() is False
        assert action.error == "oops"


class TestRepairReport:
    def test_empty(self):
        report = RepairReport()
        assert report.success_count == 0
        assert report.failure_count == 0
        assert report.all_succeeded is True

    def test_mixed_results(self):
        report = RepairReport()
        report.add(RepairAction("ok", lambda: True))
        report.add(RepairAction("fail", lambda: False))
        for a in report.actions:
            a.execute()
        assert report.success_count == 1
        assert report.failure_count == 1
        assert report.all_succeeded is False

    def test_summary(self):
        report = RepairReport()
        report.add(RepairAction("ok", lambda: True))
        report.add(RepairAction("ok2", lambda: True))
        for a in report.actions:
            a.execute()
        s = report.summary()
        assert "2 succeeded" in s
        assert "0 failed" in s


class TestRepairAppRun:
    def test_fixes_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            apprun = os.path.join(tmpdir, "AppRun")
            with open(apprun, "w") as f:
                f.write("#!/bin/bash")
            os.chmod(apprun, 0o644)
            action = repair_apprun(tmpdir)
            assert action.execute() is True
            mode = os.stat(apprun).st_mode
            assert mode & stat.S_IXUSR
            assert mode & stat.S_IXGRP
            assert mode & stat.S_IXOTH

    def test_missing_apprun(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            action = repair_apprun(tmpdir)
            assert action.execute() is False

    def test_already_executable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            apprun = os.path.join(tmpdir, "AppRun")
            with open(apprun, "w") as f:
                f.write("#!/bin/bash")
            os.chmod(apprun, 0o755)
            action = repair_apprun(tmpdir)
            assert action.execute() is True
