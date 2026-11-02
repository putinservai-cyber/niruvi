# STEP 1 Audit Report: JuNest Path Spaces Issue

## Executive Summary

The codebase has several locations where display names with spaces can reach filesystem paths, shell commands, and registry keys, which breaks JuNest containers. The current codebase has partial protections (e.g., `suggest_space_free_path`, `path_has_spaces`, `sanitize_install_name`), but they are not applied consistently across all code paths.

---

## 1. Places Where Display Names Become Paths, Filenames, or Registry Keys

### Direct Path Construction from Display Names

| File | Line | Issue |
|------|------|-------|
| `niruvi/main.py:129` | `dest_dir = dest_override or os.path.join(install_dir, app_name)` | Uses raw `app_name` from `.desktop Name=` field |
| `niruvi/ui/wizard.py:728` | `self.dest_dir = os.path.join(dest_base, self.app_name)` | Uses `self.app_name` directly |
| `niruvi/ui/wizard.py:763` | `self.dest_dir = os.path.join(dest_base, self.app_name)` | Same issue |
| `niruvi/ui/self_installer_wizard.py:130` | `return os.path.join(default_install_dir(config), config["app_name"])` | Uses raw app_name |
| `niruvi/ui/self_installer_wizard.py:134` | `return os.path.join(inst_dir, ".installed")` | Uses inst_dir which contains app_name |
| `niruvi/ui/self_installer_wizard.py:138` | `return os.path.join(inst_dir, ".appimage-manager.json")` | Same |
| `niruvi/ui/manager.py:1009` | `app_dir = os.path.join(install_dir, item)` | Iterates over install_dir contents |
| `niruvi/ui/manager.py:1031` | `apprun = os.path.join(app_dir, "AppRun")` | Uses app_dir which may have spaces |
| `niruvi/ui/wizard.py:1034` | `target = os.path.join(self.dest_dir, f"{self.app_name}.AppImage")` | Uses app_name in path |
| `niruvi/ui/wizard.py:1220` | `seed_target = os.path.join(dest_dir, f"{app_name}.AppImage")` | Same |
| `niruvi/ui/self_installer_wizard.py:1087` | `self._finish_appimage = os.path.join(dest, "AppRun")` | Uses dest which contains app_name |
| `niruvi/ui/self_installer_wizard.py:1241` | `launch = wizard._finish_appimage or os.path.join(installed_dir(config), "AppRun")` | Uses installed_dir which has app_name |
| `niruvi/main.py:129` | `dest_dir = dest_override or os.path.join(install_dir, app_name)` | CLI install uses raw app_name |
| `niruvi/ui/manager.py:1012` | `app_dir = os.path.join(install_dir, item)` | Iterates over install_dir |
| `niruvi/ui/app_info_dialog.py:125` | `os.path.join(self._app_dir, name)` | Uses item names |
| `niruvi/ui/app_info_dialog.py:133` | `sorted(names, key=lambda n: ... os.path.join(path, n))` | Uses raw names |
| `niruvi/ui/self_installer_wizard.py:194` | `target = os.path.join(self.dest, item)` | Uses item names |
| `niruvi/ui/self_installer_wizard.py:203` | `shutil.move(os.path.join(backup, item), os.path.join(self.dest, item))` | Same |
| `niruvi/ui/self_installer_wizard.py:283` | `s = os.path.join(srcdir, item)` | Same |
| `niruvi/ui/self_installer_wizard.py:284` | `d = os.path.join(self.dest, item)` | Same |
| `niruvi/ui/self_installer_wizard.py:371` | `dest_desktop = os.path.join(desktop_dir, f"{app_name}.desktop")` | Uses app_name |
| `niruvi/ui/self_installer_wizard.py:378-379` | `Exec=` line uses `os.path.join(self.dest, "AppRun")` | Path may have spaces |
| `niruvi/ui/self_installer_wizard.py:397` | `shutil.copy2(str(candidates[0]), os.path.join(icon_dir, f"{app_name}{ext}"))` | Uses app_name |
| `niruvi/ui/self_installer_wizard.py:406` | `local_uninstall = os.path.join(self.dest, ".niruvi-uninstall.sh")` | Path may have spaces |
| `niruvi/ui/self_installer_wizard.py:407` | `shipped = os.path.join(self.dest, ".niruvi-install", "uninstall.sh")` | Same |
| `niruvi/ui/self_installer_wizard.py:415` | `Exec="{uninstall_exec}"` - unquoted path | May have spaces |
| `niruvi/ui/self_installer_wizard.py:430` | `install_dir = self.dest` - uses dest with spaces | Path in registry |
| `niruvi/ui/self_installer_wizard.py:445` | `path=install_dir,` - registry path with spaces | Registry key |
| `niruvi/ui/self_installer_wizard.py:665` | `srcdir = os.path.join(inst_dir, "squashfs-root")` | Path may have spaces |
| `niruvi/ui/self_installer_wizard.py:668-669` | `s = os.path.join(srcdir, item)` / `d = os.path.join(inst_dir, item)` | Paths with spaces |

### Registry Keys

| File | Line | Issue |
|------|------|-------|
| `niruvi/desktop/installation_registry.py` | `name` field used as primary key | Registry key = display name with spaces |
| `niruvi/ui/wizard.py:1243` | `record = InstallationRecord(name=app_name, path=dest_dir, ...)` | Name with spaces as registry key |
| `niruvi/ui/self_installer_wizard.py:445` | `path=install_dir` - path with spaces | Registry path |
| `niruvi/ui/wizard.py:1246` | `sandbox_config` stored with path containing spaces | Registry value |

### .desktop File Generation

| File | Line | Issue |
|------|------|-------|
| `niruvi/desktop/desktop_utils.py:267` | `Exec={sandboxed_exec or _format_exec_path(app_dir)}` | Path may have spaces, only partially quoted |
| `niruvi/desktop/desktop_utils.py:270` | `TryExec={_format_tryexec_path(app_dir)}` | Partially quoted |
| `niruvi/desktop/desktop_utils.py:334` | `Exec={exec_value} %F` - may have unquoted spaces | Desktop shortcut |
| `niruvi/desktop/desktop_utils.py:335` | `Icon={icon_value}` - may have spaces | Icon path |
| `niruvi/desktop/desktop_utils.py:359` | `Exec={_format_exec_path(app_dir)}\n` | Partially quoted |
| `niruvi/desktop/desktop_utils.py:361` | `Icon={icon_name or 'application-x-executable'}` | Icon name |
| `niruvi/ui/self_installer_wizard.py:378-379` | `Exec="{os.path.join(self.dest, "AppRun")}" %F` | Quoted but path may have spaces |
| `niruvi/ui/self_installer_wizard.py:395` | `Icon=` with unquoted path | Icon path |
| `niruvi/ui/self_installer_wizard.py:415` | `Exec="{uninstall_exec}"` - quoted but may have spaces | Uninstall shortcut |
| `niruvi/desktop/desktop_utils.py:334` | `Exec={exec_value} %F` - may be unquoted | Shortcut |

### JuNest Detection

| File | Line | Current Behavior |
|------|------|------------------|
| `niruvi/installer/junest.py:26` | `path_has_spaces` checks basename only | Only checks last component |
| `niruvi/installer/junest.py:33` | `suggest_space_free_path` replaces spaces with dashes | Only fixes last component |
| `niruvi/core/worker.py:475-488` | `_check_junest_destination` raises error | Blocks install with spaces |
| `niruvi/ui/wizard.py:1370-1379` | Checks for JuNest and suggests path | Shows warning but doesn't auto-fix |

---

## 2. Subprocess/Shell Calls Running JuNest or AppImages

### Subprocess Calls

| File | Line | Pattern | Issue |
|------|------|---------|-------|
| `niruvi/installer/scripts.py:411` | `Exec="$target/AppRun" %F` | Shell script, unquoted path |
| `niruvi/installer/scripts.py:437` | `Exec="$uninstall_script"` | Unquoted path in shell script |
| `niruvi/ui/self_installer_wizard.py:283` | `s = os.path.join(srcdir, item)` | Paths with spaces in shutil.move |
| `niruvi/ui/self_installer_wizard.py:284` | `d = os.path.join(self.dest, item)` | Paths with spaces |
| `niruvi/core/worker.py:295` | `subprocess.Popen([appimage_path, "--appimage-extract"], ...)` | List form - OK |
| `niruvi/core/worker.py:413` | `subprocess.Popen([appimage_path, "--appimage-extract"], ...)` | List form - OK |
| `niruvi/desktop/appimage_assets.py:32` | `subprocess.run([appimage_path, "--appimage-extract"], ...)` | List form - OK |
| `niruvi/desktop/appimageupdate.py:21` | `subprocess.run([tool, appimage_path], ...)` | List form - OK |
| `niruvi/desktop/appimageupdate.py:48` | `subprocess.run([tool, appimage_path], ...)` | List form - OK |
| `niruvi/app/self_update.py:68` | `subprocess.run([tool, appimage_path], ...)` | List form - OK |
| `niruvi/launcher.py:104` | `subprocess.Popen(cmd, ...)` - list form | OK |
| `niruvi/core/sandbox.py:800+` | `subprocess.Popen(cmd_to_run, ...)` | Uses list form - OK |

### Shell=True Usage
- No direct `shell=True` found in Python code
- Shell scripts in `installer/scripts.py` are executed via bash

### JuNest Invocation
- `niruvi/installer/scripts.py` generates install.sh that calls JuNest
- JuNest's `namespace.sh` uses shell word-splitting on unquoted variables
- The `--appimage-extract` method is used instead of direct JuNest invocation in most cases

---

## 3. Unquoted/Raw Paths in .desktop Files

### Exec= Lines

| File | Line | Current Code | Issue |
|------|------|--------------|-------|
| `desktop_utils.py:267` | `Exec={sandboxed_exec or _format_exec_path(app_dir)}` | Partially quoted |
| `desktop_utils.py:270` | `TryExec={_format_tryexec_path(app_dir)}` | Partially quoted |
| `desktop_utils.py:286` | `Exec={sandboxed_exec or _format_exec_path(app_dir)}` | Same |
| `desktop_utils.py:334` | `Exec={exec_value} %F` | Shortcut - unquoted |
| `desktop_utils.py:359` | `Exec={_format_exec_path(app_dir)}\n` | Partially quoted |
| `self_installer_wizard.py:379` | `Exec="{os.path.join(self.dest, "AppRun")}" %F` | Quoted but path may have spaces |
| `self_installer_wizard.py:395` | `Icon=` unquoted | Icon path |
| `self_installer_wizard.py:415` | `Exec="{uninstall_exec}"` | Quoted but may have spaces |
| `desktop_utils.py:334` | `Exec={exec_value} %F` | May be unquoted |
| `desktop_utils.py:359` | `Exec={_format_exec_path(app_dir)}\n` | Partially quoted |

### _format_exec_path and _format_tryexec_path

```python
def _format_exec_path(app_dir: str) -> str:
    apprun = os.path.join(app_dir, "AppRun")
    if " " in apprun:
        return f'"{apprun}" %F'
    return f"{apprun} %F"

def _format_tryexec_path(app_dir: str) -> str:
    apprun = os.path.join(app_dir, "AppRun")
    return f'"{apprun}"' if " " in apprun else apprun
```

**Issues:**
1. Only quotes if space present, but other special chars (`$`, `` ` ``, `\`, `"`) not handled
2. According to Desktop Entry spec, Exec= should always be quoted if path contains special chars
2. `TryExec=` should also be fully quoted per spec

---

## 4. JuNest Invocation Pattern

### Current Implementation

JuNest is not directly invoked as a subprocess. Instead:
1. AppImages with JuNest are detected via `is_junest_app()`
2. If install path has spaces, `_check_junest_destination` raises `JunestPathError`
3. User is prompted to choose a space-free path
4. Extraction uses `--appimage-extract` (not JuNest directly)
5. The installed app's `AppRun` is executed normally (with sandbox)

### Key Files
- `niruvi/installer/junest.py` - Detection and path suggestion
- `niruvi/installer/scripts.py` - Generates install.sh with JuNest handling
- `niruvi/core/worker.py` - Checks JuNest during extraction
- `niruvi/ui/wizard.py` - Shows warning dialog for JuNest paths

**Gap**: The installer scripts in `niruvi/installer/scripts.py` generate shell scripts that may still have issues with paths containing spaces when JuNest is used at runtime.

---

## 5. Existing Installs with Spaces

### Detection
- `junest.py:path_has_spaces()` checks only the **basename** of the path
- Full path components are not checked (e.g., `/home/user/Applications/My App` would be detected, but `/home/user/My Apps/App` would not)

### Current Behavior
- `junest_destination_error()` blocks installation if path has spaces AND app is JuNest
- User is shown suggested path with spaces replaced by dashes
- No automatic migration of existing spaced installs

### Registry State
- Registry uses display name as primary key (can contain spaces)
- Path stored in registry may contain spaces
- Desktop files created with paths containing spaces

---

## Summary of Required Changes

### High Priority (Breaks JuNest)
1. **All install paths must use safe names** - Create `safe_name()` function and apply everywhere
2. **Fix .desktop Exec= lines** - Always quote paths per Desktop Entry spec
3. **Registry keys must be safe** - Use safe names as registry keys
4. **Side-by-side naming** - Use `safe_name(base) + "-2"` instead of "App 2"
4. **Migration for existing installs** - Auto-rename spaced folders on startup

### Medium Priority
5. **Fix JuNest detection** - Check full path, not just basename
6. **Add path validation** - `assert_no_spaces()` at install boundaries
7. **Migrate existing installs** on startup
8. **Fix portable desktop entries** - Quote paths properly

### Lower Priority
9. **Fix JuNest runtime scripts** - Ensure generated shell scripts handle paths with spaces
10. **Add regression tests** - All the test cases in STEP 8

---

## Files Requiring Changes (Priority Order)

| File | Priority | Changes Needed |
|------|----------|----------------|
| `niruvi/naming.py` (new) | Critical | Create `safe_name()`, `side_by_side_name()`, `assert_no_spaces()` |
| `niruvi/desktop/desktop_utils.py` | Critical | Fix `sanitize_app_name`, `_format_exec_path`, `_format_tryexec_path`, `create_desktop_entry`, `create_desktop_shortcut`, `create_portable_desktop_entry`, `find_unique_install_name` |
| `niruvi/ui/wizard.py` | Critical | Use safe names for install dir, desktop files, registry keys |
| `niruvi/ui/self_installer_wizard.py` | Critical | Use safe names for all paths, quote .desktop Exec= properly |
| `niruvi/main.py` | Critical | CLI install/uninstall with safe names, migration on startup |
| `niruvi/desktop/desktop_utils.py` | Critical | `sanitize_app_name`, `find_unique_install_name`, `_format_exec_path`, `_format_tryexec_path`, `create_desktop_entry`, `create_desktop_shortcut`, `create_portable_desktop_entry` |
| `niruvi/ui/wizard.py` | Critical | `sanitize_install_name` → use `safe_name`, fix dest_dir |
| `niruvi/ui/self_installer_wizard.py` | High | All paths use safe names, fix .desktop generation |
| `niruvi/desktop/installation_registry.py` | High | Use safe names as keys, add migration |
| `niruvi/desktop/desktop_utils.py` | High | `sanitize_app_name` → delegate to `safe_name`, `find_unique_install_name` → use `side_by_side_name` |
| `niruvi/installer/junest.py` | High | Check full path for spaces, not just basename |
| `niruvi/core/worker.py` | Medium | Use safe names for extraction, architecture check |
| `niruvi/installer/scripts.py` | Medium | Shell script generation with proper quoting |
| `niruvi/ui/manager.py` | Medium | Iterate with safe names |
| `niruvi/ui/uninstall_dialog.py` | Medium | Use safe names for paths |
| `niruvi/desktop/icon_utils.py` | Low | Icon naming with safe names |
| `niruvi/desktop/appimage_assets.py` | Low | Extraction paths |
| `niruvi/installer/bootstrap.py` | Low | Build output paths |

---

## Test Cases Needed (Per STEP 8)

| Test | Expected |
|------|----------|
| `safe_name("VLC media player")` | `"VLC-media-player"` |
| `safe_name("Café Télé")` | `"Caf-T-l"` (ASCII, no spaces) |
| `safe_name("!!!")` | `"app"` |
| `side_by_side_name("App", 2)` | `"App-2"` (no spaces) |
| `assert_no_spaces("a b")` | Raises `ValueError` |
| `assert_no_spaces("a-b")` | Passes |
| JuNest command is list | Never `shell=True`, path as single element |
| .desktop Exec= quoted | Always quoted per spec |
| Migration works | Spaced folder renamed, .desktop updated, registry updated |
| Uninstall cleans up | Only app's files removed |

---

## Migration Strategy

1. **On startup** (in `main()` before GUI):
   - Scan registry for entries with spaces in path
   - For each: compute safe name, rename folder, update .desktop, update registry
   - Atomic registry write (temp file → fsync → replace)
   - Rollback on any failure
   - Mark migration done in settings

2. **One-time only** - Store migration version in settings

---

This audit reveals that the JuNest path spaces issue is pervasive throughout the codebase. The fix requires a centralized `safe_name()` function and consistent application across all path construction, .desktop file generation, and registry operations.
