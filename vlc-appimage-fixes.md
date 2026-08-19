# VLC JuNest AppImage — Problem & Fixes

## Background

The VLC AppImage bundles a **JuNest** environment (a minimal Arch Linux filesystem inside `.junest/`). The AppImage uses `bwrap` (bubblewrap) to namespace-enter this environment and launch VLC. Two categories of bugs were encountered:

1. **Path-with-spaces word-splitting** — The installation directory `VLC media player` contains spaces, which breaks unquoted shell variable expansions in JuNest's scripts.
2. **Pre-existing container issues** — Broken profile scripts and stale cache inside the JuNest image itself.

---

## Problem 1: `bwrap: execvp .../junest: No such file or directory`

### Root Cause

In `lib/core/namespace.sh`, the constant `COMMON_BWRAP_OPTION` was defined as a **string**:

```bash
COMMON_BWRAP_OPTION="--bind $JUNEST_HOME $JUNEST_HOME"
```

Because `JUNEST_HOME` expands to a path containing spaces (`/home/putinservai/Applications/VLC media player/.junest`), this string, when used unquoted, is split into multiple words by the shell:

```
--bind /home/putinservai/Applications/VLC  media  player/.junest /home/putinservai/Applications/VLC  media  player/.junest
```

`bwrap` then tries to `execvp` the path `player/.junest` (the last word before the second `--bind`), which does not exist.

### Fix

Changed `COMMON_BWRAP_OPTION` from a bare string to a **bash array** so each element is preserved regardless of spaces:

```bash
COMMON_BWRAP_OPTION=("--bind" "${JUNEST_HOME}" "${JUNEST_HOME}")
```

All usage sites were updated from `$COMMON_BWRAP_OPTION` (unquoted) to `"${COMMON_BWRAP_OPTION[@]}"` (quoted array expansion).

**Files changed:**
- `lib/core/namespace.sh` — definition + all call sites

---

## Problem 2: `media: command not found` on container exit

### Root Cause

In `bin/junest` line 311, the `trap` command was:

```bash
trap "PATH=$PATH create_wrappers" EXIT QUIT TERM
```

The double quotes cause `$PATH` to be expanded **at trap-definition time**, producing a string like:

```bash
trap "PATH=/usr/local/bin:/home/putinservai/Applications/VLC media player/... create_wrappers" EXIT
```

When the trap actually **fires** (on exit), the shell evaluates this string and word-splits it on the spaces in `VLC media player`, producing:

```bash
PATH=/usr/local/bin:/home/putinservai/Applications/VLC media
```

The word `media` becomes an additional command that the shell tries to execute, causing `media: command not found`.

### Fix

Changed the double quotes to **single quotes**, deferring expansion to trap-execution time where `$PATH` is already set correctly inside the function scope:

```bash
trap 'PATH="$PATH" create_wrappers' EXIT QUIT TERM
```

**Files changed:**
- `bin/junest` line 311

---

## Problem 3: `vapoursynth.sh: command not found`

### Root Cause

The file `.junest/etc/profile.d/vapoursynth.sh` contained:

```bash
export VSSCRIPT_PATH=$(vapoursynth get-vsscript)
```

This runs on **every** shell invocation inside the JuNest container. The `vapoursynth` binary does **not exist** in the AppImage's bundled Arch image (it was never included). The only related file is a **broken symlink** at `usr/bin_wrappers/vapoursynth` pointing to a non-existent `junest_wrapper` binary. As a result, every shell spawn shows:

```
bash: vapoursynth: command not found
```

### Fix

Commented out the offending line so it no longer executes:

```bash
# vapoursynth binary not available in this environment
# export VSSCRIPT_PATH=$(vapoursynth get-vsscript)
```

**Files changed:**
- `.junest/etc/profile.d/vapoursynth.sh`

---

## Problem 4: Stale VLC plugin cache

### Root Cause

When VLC is launched, it checks `.junest/usr/lib/vlc/plugins/plugins.dat` against the actual plugin files. If the cache was built from a different set of plugins (e.g., during image creation on the build machine), VLC reports:

```
stale plugins cache
```

This is a cosmetic warning but can slow down startup and confuse users.

### Fix

Deleted the stale cache and regenerated it inside the JuNest container using VLC's own `vlc-cache-gen`:

```bash
JUNEST_HOME="$HERE/.junest" "$HERE/.local/share/junest/bin/junest" \
  -n -b "$junest_bindings" \
  -- /usr/lib/vlc/vlc-cache-gen /usr/lib/vlc/plugins
```

Equivalent to manually removing the file:

```bash
rm ".junest/usr/lib/vlc/plugins/plugins.dat"
```

VLC will also regenerate it automatically on next launch if absent.

**Files changed:**
- `.junest/usr/lib/vlc/plugins/plugins.dat` — deleted and regenerated

---

## Additional Fixes (supporting infrastructure)

### `lib/core/common.sh` — `ld_exec_cmd` quoting

The `LD_EXEC` variable was a string containing library-path arguments with `$LD_LIB` (which could contain spaces in its expanded value). Replaced with a **function** `ld_exec_cmd()` that uses `"$LD_LIB"` (quoted) throughout.

Also fixed `proot_cmd()` to quote the `"${PROOT}"` binary path and use `eval` with properly quoted arguments so paths with spaces survive.

### `lib/core/chroot.sh` — GRoot command splitting

The `_run_env_as_xroot()` function was combining `$cmd` and `$cmd_opts` into a single string, then splitting them again. Restructured to accept them as **separate parameters**, preserving spaces in the groot binary path.

### `lib/core/proot.sh` — `printf "%q"` escaping

Used `printf "%q"` to shell-escape `JUNEST_HOME` before embedding it in proot argument strings, preventing word-splitting in the `eval` inside `proot_cmd()`.

---

## Summary of Changed Files

| File | Change |
|------|--------|
| `.local/share/junest/bin/junest:311` | Double → single quotes in trap |
| `.local/share/junest/lib/core/common.sh` | `ld_exec_cmd()` function; `proot_cmd()` quoting |
| `.local/share/junest/lib/core/namespace.sh` | `COMMON_BWRAP_OPTION` string → array |
| `.local/share/junest/lib/core/chroot.sh` | Separate `cmd`/`cmd_opts` for groot |
| `.local/share/junest/lib/core/proot.sh` | `printf "%q"` for JUNEST_HOME |
| `.junest/etc/profile.d/vapoursynth.sh` | Commented out broken command |
| `.junest/usr/lib/vlc/plugins/plugins.dat` | Deleted + regenerated |
