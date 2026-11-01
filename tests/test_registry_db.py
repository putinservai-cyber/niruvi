"""Tests for the SQLite-backed installation registry and legacy JSON migration."""

import json
import os

from niruvi.config import get_data_dir
from niruvi.desktop.installation_registry import InstallationRecord, InstallationRegistry
from niruvi.utils.integrity import write_json_with_hmac


def _make_record(name="app", path=None, version="1.2.3", **kwargs):
    return InstallationRecord(
        name=name,
        path=path or f"/tmp/{name}",
        version=version,
        update_url=f"https://example.com/{name}/latest",
        tags=["utility", "editor"],
        **kwargs,
    )


def test_add_get_remove_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
    reg = InstallationRegistry()
    reg.reset()

    reg.add(_make_record("alpha"))
    reg.add(_make_record("beta", path="/tmp/beta", size=2048))
    assert reg.get("alpha") is not None
    assert reg.get("beta").size == 2048
    reg.flush()

    assert os.path.isfile(os.path.join(str(tmp_path), "registry.db"))
    assert not os.path.isfile(os.path.join(str(tmp_path), "registry.json"))

    fresh = InstallationRegistry()
    fresh.reset()
    fresh.add(_make_record("alpha"))  # triggers load of existing db
    records = fresh.get_all()
    assert {r.name for r in records} == {"alpha", "beta"}
    beta = fresh.lookup_by_name("beta")
    assert beta.path == "/tmp/beta"
    assert beta.tags == ["utility", "editor"]
    assert fresh.lookup_by_path("/tmp/beta").name == "beta"

    fresh.remove("beta")
    fresh.flush()
    reloaded = InstallationRegistry()
    reloaded.reset()
    assert reloaded.get("beta") is None
    assert reloaded.get("alpha") is not None


def test_migrates_legacy_json(tmp_path, monkeypatch):
    monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
    data_dir = str(tmp_path)
    legacy = os.path.join(data_dir, "registry.json")
    write_json_with_hmac(
        legacy,
        {
            "records": [
                _make_record("old1").to_dict(),
                _make_record("old2", version="9.9.9").to_dict(),
            ]
        },
    )

    reg = InstallationRegistry()
    reg.reset()
    assert reg.get("old1") is not None
    assert reg.get("old2").version == "9.9.9"
    assert os.path.isfile(os.path.join(data_dir, "registry.db"))

    reloaded = InstallationRegistry()
    reloaded.reset()
    reloaded.add(_make_record("old1"))
    assert {r.name for r in reloaded.get_all()} == {"old1", "old2"}


def test_tampered_row_is_dropped(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
    reg = InstallationRegistry()
    reg.reset()
    reg.add(_make_record("good"))
    reg.add(_make_record("evil", path="/tmp/evil"))
    reg.flush()

    db = os.path.join(str(tmp_path), "registry.db")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE installed_apps SET version='99.99' WHERE name='evil'")
    conn.commit()
    conn.close()

    fresh = InstallationRegistry()
    fresh.reset()
    fresh.add(_make_record("good"))
    assert fresh.get("good") is not None
    assert fresh.get("evil") is None


def test_empty_legacy_json(tmp_path, monkeypatch):
    monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
    legacy = os.path.join(str(tmp_path), "registry.json")
    with open(legacy, "w") as f:
        json.dump({"records": []}, f)

    reg = InstallationRegistry()
    reg.reset()
    assert reg.get_all() == []


def test_metadata_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("NIRUVI_DATA_DIR", str(tmp_path))
    reg = InstallationRegistry()
    reg.reset()
    record = _make_record(
        "full",
        install_type="custom",
        source_sha256="abc123",
        desktop_file="/usr/share/applications/full.desktop",
        architecture="x86_64",
        env_vars={"FOO": "bar"},
        run_args="--fullscreen",
        auto_update=True,
        update_channel="beta",
        sandbox_config={"backend": "firejail"},
        custom_icon_path="/tmp/icon.png",
    )
    reg.add(record)
    reg.flush()

    fresh = InstallationRegistry()
    fresh.reset()
    got = fresh.get("full")
    assert got.install_type == "custom"
    assert got.source_sha256 == "abc123"
    assert got.auto_update is True
    assert got.update_channel == "beta"
    assert got.env_vars == {"FOO": "bar"}
    assert got.sandbox_config == {"backend": "firejail"}
    assert got.run_args == "--fullscreen"
    assert got.custom_icon_path == "/tmp/icon.png"
    assert get_data_dir() == str(tmp_path)
