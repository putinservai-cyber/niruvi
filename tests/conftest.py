"""Global test isolation.

Every test gets its own data dir so no test can ever read or write the real
Niruvi registry/settings under ~/Applications/Niruvi/.niruvi. The
InstallationRegistry is a process-wide singleton; it is reset between tests
so records written by one test never leak into another.
"""

import shutil

import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "niruvi-data"
    data_dir.mkdir()
    monkeypatch.setenv("NIRUVI_DATA_DIR", str(data_dir))
    monkeypatch.delenv("APPIMAGE", raising=False)
    from niruvi.desktop.installation_registry import InstallationRegistry

    reg = InstallationRegistry()
    reg._records.clear()
    reg._path_index.clear()
    reg._loaded = False
    if reg._save_timer is not None:
        reg._save_timer.cancel()
        reg._save_timer = None
    reg._save_pending = False
    yield data_dir
    if reg._save_timer is not None:
        reg._save_timer.cancel()
    shutil.rmtree(data_dir, ignore_errors=True)
