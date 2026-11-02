# STEP 1 Audit Report: Current Install Workflow

## Overview
This audit examines the current AppImage installation workflow in Niruvi, focusing on extraction, installation, registration, update, and uninstall processes.

---

## 1. AppImage Extraction Points

### Files that run `--appimage-extract`, `unsquashfs`, or execute AppImage binary:

| File | Function | Working Directory | Notes |
|------|----------|-------------------|-------|
| `niruvi/core/worker.py` | `_run_extraction()` → `_try_extract()` | `extract_dir` (temp dir) | Runs `appimage_path --appimage-extract` via subprocess |
| `niruvi/core/worker.py` | `_try_extract()` | `extract_dir` (temp dir) | Fallback when safe extraction fails |
| `niruvi/desktop/appimage_assets.py` | `_extract_once()` | `tmp.name` (temp dir) | Falls back to `--appimage-extract` if safe extraction fails |
| `niruvi/desktop/appimage_assets.py` | `_extract_once()` | `tmp.name` (temp dir) | Runs `appimage_path --appimage-extract` |
| `niruvi/core/scanner.py` | `_extract_squashfs()` | `dest` (passed in) | Uses `unsquashfs -q -o <offset> -d <dest> <appimage>` |
| `niruvi/core/scanner.py` | `_extract_dwarfs()` | `dest` (passed in) | Uses `dwarfsextract -o <offset> -d <dest> <appimage>` |
| `niruvi/ui/wizard.py` | `ExtractionWorker.run()` | Temp dir | Uses `ExtractionWorker` → `_run_extraction` |

### Key Issue: Working Directory
- **`_run_extraction`** creates a temp directory (`tempfile.TemporaryDirectory()`) and runs extraction there - **GOOD**
- **`_try_extract`** runs `--appimage-extract` in that temp directory - **GOOD**
- **`_extract_once`** in `appimage_assets.py` creates a temp dir and runs extraction - **GOOD**

---

## 2. Temp/Extracted Directory Cleanup

### Current Behavior:
| Location | Temp Dir Creation | Cleanup Method | Issue |
|----------|-------------------|----------------|-------|
| `worker.py` `_run_extraction` | `tempfile.TemporaryDirectory()` | Context manager (auto) | **OK** |
| `worker.py` `_try_extract` | Uses same temp dir | Context manager | **OK** |
| `appimage_assets.py` `_extract_once` | `tempfile.TemporaryDirectory()` | `tmp.cleanup()` in `finally` | **OK** |
| `scanner.py` `extract_safely` | `dest` passed by caller | Caller responsibility | **RISK** - caller must cleanup |
| `wizard.py` `ExtractionWorker.run()` | `tempfile.TemporaryDirectory()` | Context manager | **OK** |

### Leftover Artifacts Found:
- **`squashfs-root/`** can be left in working directory if:
  - `extract_safely()` in `scanner.py` is called with a destination that's not a temp dir
  - `--appimage-extract` creates `squashfs-root/` in CWD when run without `-d`
  - Failed extractions may leave staging dirs (`dest_dir + ".staging"`)

---

## 3. .desktop File and Icon Handling

### Finding .desktop and Icon:
- `appimage_assets.py`: `_get_icon_name_from_desktop()` reads `Icon=` from .desktop
- `appimage_assets.py`: `_find_icon_in_dir()` searches standard XDG icon paths
- `desktop_utils.py`: `_resolve_icon()` finds icon from .desktop `Icon=` field
- `desktop_utils.py`: `install_icon_to_theme()` copies icon to `~/.local/share/icons/hicolor/<size>/apps/`

### Exec= and Icon= Rewriting:
- `desktop_utils.py`: `create_desktop_entry()` rewrites:
  - `Exec=` → points to installed `AppRun` with sandbox wrapper if enabled
  - `TryExec=` → points to `AppRun` (quoted if path has spaces)
  - `Icon=` → uses installed theme icon name (not path)
- `create_portable_desktop_entry()` writes `Exec=` pointing directly to AppImage file

### Quoting for Spaces:
- `_format_exec_path()`: Quotes path if it contains spaces: `'"{apprun}" %F'`
- `_format_tryexec_path()`: Quotes if spaces present
- `create_portable_desktop_entry()`: Quotes AppImage path if spaces present

---

## 4. Installation Registration

### Current Implementation:
- **Backend**: SQLite (`registry.db`) with HMAC-signed rows
- **Migration**: Auto-migrates from legacy `registry.json`
- **Atomic Write**: Uses `INSERT ... ON CONFLICT` upsert with transaction
- **Backup**: No explicit backup before write, but SQLite transactions provide atomicity
- **Flush**: `flush()` method waits for debounced write (0.1s debounce)

### Data Stored:
- `name`, `path`, `version`, `install_date`, `install_type`, `source_sha256`
- `desktop_file`, `desktop_shortcut`, `update_url`, `architecture`
- `sandbox_config_json`, `size`, `tags_json`, `hmac`

---

## 5. Update Checking and Download

### Update Flow:
1. `self_update.py`: `check_for_updates()` fetches `update.json` from GitHub
2. `_verify_update_manifest()`: GPG verifies detached signature
3. `resolve_update_source()` in `update_sources.py`: Detects GitHub/GitLab/direct
4. `DownloadWorker`: Downloads to temp file with SHA256 verification
5. `_InstallCoordinator._install()`: 
   - Backs up current AppImage (`dest + ".backup"`)
   - Replaces with `os.replace(temp_path, dest)`
   - Updates `.appimage-manager.json` with version

### SHA256 Verification:
- **Before install**: `DownloadWorker` computes SHA256 during download, verifies against manifest
- **After download**: `_InstallCoordinator.on_finished()` compares actual vs expected SHA256
- **Sidecar check**: `fetch_sidecar_sha256()` probes `.sha256`/`.sha256sum` files

### Gaps:
- No verification of the AppImage's internal payload after extraction
- No rollback if new version fails to run

---

## 6. Broad Exception Handling (Silent Failures)

### Found Silent `except:` or `except Exception: pass`:

| File | Location | Issue |
|------|----------|-------|
| `scanner.py` | `scan_file()` | `except (OSError, MemoryError): pass` - hides read errors |
| `scanner.py` | `extract_safely()` | `except Exception as e:` - logs debug but returns False silently |
| `appimage_assets.py` | `_extract_once()` | `except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:` - raises RuntimeError but cleanup may fail |
| `worker.py` | `_run_extraction()` | Multiple broad `except Exception:` blocks |
| `worker.py` | `_try_extract()` | `except OSError as e:` - logs but continues |
| `worker.py` | `ExtractionWorker.run()` | `except Exception as e:` - emits error signal |
| `desktop_utils.py` | `create_desktop_entry()` | `except OSError:` - shows dialog but returns None |
| `desktop_utils.py` | `refresh_desktop_database()` | `except Exception as e:` - logs debug, continues |
| `desktop_utils.py` | `register_mime_handler()` | `except Exception as e:` - logs debug, continues |
| `desktop_utils.py` | `unregister_mime_handler()` | `except OSError:` - returns False |
| `installation_registry.py` | `_load_db()` | `except sqlite3.Error as e:` - logs warning |
| `installation_registry.py` | `_save()` | `except sqlite3.OperationalError as e:` - retries 3x |
| `installation_registry.py` | `_migrate_from_json()` | `except Exception:` - silently skips items |
| `icon_utils.py` | `get_pixmap_from_file()` | `except Exception:` - logs debug, returns None |
| `icon_utils.py` | `_svg_to_png()` | `except Exception:` - logs debug, tries fallback |
| `self_update.py` | `_fetch_json()` | `except Exception:` - propagates |
| `self_update.py` | `_verify_update_manifest()` | `except subprocess.CalledProcessError:` - returns False |
| `update_sources.py` | Multiple functions | `except Exception as e:` - logs debug, returns None |
| `health_check.py` | `scan_missing_libs()` | `except (OSError, subprocess.TimeoutExpired):` - returns [] |

---

## Summary of Critical Issues

1. **squashfs-root cleanup**: Not guaranteed when extraction fails mid-way
2. **No DwarFS detection in worker**: Only `scanner.py` has `_extract_dwarfs()`
3. **Atomic install missing fsync**: `os.replace()` without `fsync()` before
4. **No side-by-side install**: Overwrites existing app instead of suffixing
5. **Registry write not fully atomic**: Debounced write, no guaranteed flush on crash
6. **Update rollback incomplete**: Restores backup but doesn't verify new version works
6. **No payload verification**: Only SHA256 of downloaded file, not extracted contents
7. **Silent exception handling**: Many places log at debug level but don't surface errors
8. **Temp file cleanup on failure**: Some paths leave `.staging` dirs on failure

---

## Recommendations Priority

1. **HIGH**: Fix temp extraction cleanup, add DwarFS support to worker
2. **HIGH**: Atomic install with fsync, side-by-side naming
3. **HIGH**: Registry atomic write with backup, guaranteed flush
4. **MEDIUM**: Update rollback with verification, payload integrity check
5. **MEDIUM**: Replace silent exceptions with logged errors + user-facing dialogs
6. **MEDIUM**: Add timeout to all subprocess calls
