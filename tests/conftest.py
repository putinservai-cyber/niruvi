"""Global test isolation.

Every test gets its own data dir so no test can ever read or write the real
Niruvi registry/settings under ~/Applications/Niruvi/.niruvi. The
InstallationRegistry is a process-wide singleton; it is reset between tests
so records written by one test never leak into another.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """A single QApplication for the whole test session.

    Only one QApplication may exist per process. Creating a second one tears
    down the first, which deletes the C++ objects it owned - notably the
    parentless QTimer the sound manager keeps - and leaves module-level
    wrappers pointing at freed memory. Scope this to the session so the
    application outlives individual test modules.
    """
    from PyQt6.QtWidgets import QApplication

    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "niruvi-data"
    data_dir.mkdir()
    monkeypatch.setenv("NIRUVI_DATA_DIR", str(data_dir))
    monkeypatch.delenv("APPIMAGE", raising=False)
    from niruvi.desktop.installation_registry import InstallationRegistry

    reg = InstallationRegistry()
    reg.reset()
    yield data_dir
    reg.reset()
