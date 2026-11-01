"""Storage accounting and safe temp cleanup for Niruvi.

Why storage grows (verified root causes)
----------------------------------------
Niruvi does **not** extract an AppImage just to launch it.  The normal launch
path runs the already-installed ``<app_dir>/AppRun`` (``manager._run_app``),
and the sandbox (``core.sandbox``) only re-points environment/paths — it never
copies application files.  Portable ``.home``/``.config`` are per-app *user
data* directories, not duplicates of the application.

The real contributors, in order of impact:

* the delta-update **seed AppImage** copied into every install dir
  (``{app}.AppImage``) while ``delta_updates`` is enabled (default on);
* intentional ``.prev`` **rollback** copies kept per updated app;
* obsolete ``.bak``/``.backup``/``.staging`` leftovers from interrupted
  operations;
* **temporary extraction** trees (``/tmp/niruvi-*``, ``aim-rollback-*``,
  ``aim-extract-*`` …) that are only removed on a clean exit and therefore
  survive a crash/KILL.

This module is the single, testable source of truth for measuring those
categories without double-counting, and for removing *stale* temp dirs safely
(only well-known Niruvi prefixes, only when older than a threshold, never an
active path).
"""

import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Directory names/prefixes Niruvi creates under the system temp directory.
# Cleanup only ever touches entries matching these, so unrelated user files
# can never be deleted.
TEMP_DIR_PREFIXES = (
    "niruvi-",
    "niruvi_",
    "aim-rollback-",
    "aim-extract-",
    "aim-info-",
    "aim-meta-",
    "aim-builder-",
)
TEMP_DIR_NAMES = ("niruvi_local_copy",)

# Suffixes of side directories that are safe to remove once an operation has
# finished; they are leftovers, not rollback data the user asked us to keep.
OBSOLETE_BACKUP_SUFFIXES = (".bak", ".backup", ".staging", ".rollback_failed")

# The intentional rollback copy.  Never auto-removed.
ROLLBACK_SUFFIX = ".prev"
PORTABLE_HOME_SUFFIX = ".home"
PORTABLE_CONFIG_SUFFIX = ".config"

DEFAULT_STALE_AGE_SECONDS = 6 * 60 * 60


def _dir_size(path: str) -> int:
    """Total size of regular files under ``path``.

    Symlinked files are skipped so a link to an in-tree file cannot be counted
    twice, and symlinked directories are not followed (``os.walk`` default).
    """
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    if os.path.islink(full):
                        continue
                    total += os.path.getsize(full)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _app_dir_size_split(path: str) -> tuple[int, int]:
    """Return ``(app_files, seed_images)`` for an install directory.

    ``app_files`` excludes ``.AppImage`` seed copies so the two are never
    summed twice.
    """
    app_files = 0
    seed_images = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    if os.path.islink(full):
                        continue
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if name.lower().endswith(".appimage"):
                    seed_images += size
                else:
                    app_files += size
    except OSError:
        pass
    return app_files, seed_images


def app_dir_size(path: str, include_seed: bool = False) -> int:
    """Size of an install directory.

    By default the delta seed AppImage (``*.AppImage``) is excluded so the
    application footprint is not double-counted; pass ``include_seed=True`` for
    the raw on-disk total.
    """
    app_files, seed_images = _app_dir_size_split(path)
    return app_files + seed_images if include_seed else app_files


@dataclass
class AppStorage:
    name: str
    path: str
    app_files: int = 0
    seed_images: int = 0
    portable_home: int = 0
    portable_config: int = 0
    rollback: int = 0
    obsolete: int = 0

    @property
    def total(self) -> int:
        return (
            self.app_files
            + self.seed_images
            + self.portable_home
            + self.portable_config
            + self.rollback
            + self.obsolete
        )

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "app_files": self.app_files,
            "seed_images": self.seed_images,
            "portable_home": self.portable_home,
            "portable_config": self.portable_config,
            "rollback": self.rollback,
            "obsolete": self.obsolete,
            "total": self.total,
        }


@dataclass
class StorageReport:
    apps: list[AppStorage] = field(default_factory=list)
    temp_dirs: list[tuple[str, int]] = field(default_factory=list)

    @property
    def temp_total(self) -> int:
        return sum(size for _path, size in self.temp_dirs)

    @property
    def apps_total(self) -> int:
        return sum(a.total for a in self.apps)

    @property
    def grand_total(self) -> int:
        return self.apps_total + self.temp_total


def analyze_storage(apps, temp_root: str | None = None) -> StorageReport:
    """Measure storage used by Niruvi.

    ``apps`` is an iterable of ``(name, path)`` pairs.  Each install dir is
    walked once; seed AppImages, portable data, the rollback copy and obsolete
    leftovers are accounted separately so nothing is double-counted.
    """
    report = StorageReport()
    for name, path in apps:
        entry = AppStorage(name=name, path=path or "")
        if path and os.path.isdir(path):
            entry.app_files, entry.seed_images = _app_dir_size_split(path)
            for suffix, attr in (
                (PORTABLE_HOME_SUFFIX, "portable_home"),
                (PORTABLE_CONFIG_SUFFIX, "portable_config"),
                (ROLLBACK_SUFFIX, "rollback"),
            ):
                side = path + suffix
                if os.path.isdir(side):
                    setattr(entry, attr, _dir_size(side))
            for suffix in OBSOLETE_BACKUP_SUFFIXES:
                side = path + suffix
                if os.path.isdir(side):
                    entry.obsolete += _dir_size(side)
        report.apps.append(entry)
    report.temp_dirs = list(iter_temp_usage(temp_root))
    return report


def _temp_root(temp_root: str | None = None) -> str:
    return temp_root or tempfile.gettempdir()


def iter_temp_dirs(temp_root: str | None = None):
    """Yield paths of Niruvi-owned temp directories (no size walk)."""
    root = _temp_root(temp_root)
    try:
        entries = os.listdir(root)
    except OSError:
        return
    for name in entries:
        if name in TEMP_DIR_NAMES or name.startswith(TEMP_DIR_PREFIXES):
            full = os.path.join(root, name)
            if os.path.isdir(full) and not os.path.islink(full):
                yield full


def iter_temp_usage(temp_root: str | None = None):
    """Yield ``(path, size_bytes)`` for Niruvi-owned temp directories."""
    for path in iter_temp_dirs(temp_root):
        yield path, _dir_size(path)


def _protected_paths() -> set[str]:
    """Real paths that must never be removed by temp cleanup."""
    protected: set[str] = set()
    try:
        from niruvi.config import get_data_dir

        protected.add(os.path.realpath(get_data_dir()))
    except Exception:
        pass
    return protected


def cleanup_stale_temp_dirs(
    max_age_seconds: int = DEFAULT_STALE_AGE_SECONDS,
    temp_root: str | None = None,
    active_paths=(),
) -> tuple[list[str], int]:
    """Remove Niruvi temp dirs that are old enough to be safe.

    Only directories created under a known Niruvi prefix AND last modified more
    than ``max_age_seconds`` ago are removed, and never a path in
    ``active_paths`` or the Niruvi data directory.  This reclaims extraction
    trees orphaned by a crash/KILL without risking a currently-running
    operation, user data, or unrelated files.

    Returns ``(removed_paths, bytes_freed)``.
    """
    now = time.time()
    active = {os.path.realpath(p) for p in active_paths}
    protected = _protected_paths()
    removed: list[str] = []
    freed = 0
    for path in iter_temp_dirs(temp_root):
        try:
            real = os.path.realpath(path)
            if real in active or real in protected:
                continue
            if now - os.path.getmtime(path) < max_age_seconds:
                continue
            size = _dir_size(path)
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                removed.append(path)
                freed += size
        except OSError as e:
            logger.debug("Could not clean temp dir %s: %s", path, e, exc_info=True)
    if removed:
        logger.info("Storage cleanup removed %d stale temp dir(s), %d bytes freed", len(removed), freed)
    return removed, freed


def cleanup_obsolete_backups(app_name: str, app_dir: str) -> int:
    """Remove ``.bak``/``.backup``/``.staging`` leftovers for one app.

    Never touches the rollback ``.prev`` copy or the application directory.  To
    be used only after an operation has definitively finished.  Returns bytes
    freed.
    """
    freed = 0
    for suffix in OBSOLETE_BACKUP_SUFFIXES:
        side = app_dir + suffix
        if os.path.isdir(side) and not os.path.islink(side):
            size = _dir_size(side)
            shutil.rmtree(side, ignore_errors=True)
            if not os.path.exists(side):
                freed += size
        elif os.path.isfile(side):
            try:
                size = os.path.getsize(side)
                os.remove(side)
                freed += size
            except OSError:
                pass
    if freed:
        logger.info("Removed obsolete backups for %s (%d bytes)", app_name, freed)
    return freed
