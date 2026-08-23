"""Niruvi Shield — sandbox backend for AppImages.

Supports multiple backends:
  - SHIELD: native process hardening (rlimits, mlockall, ptrace disable)
           + portable .home/.config
           + user namespace unsharing
           + Landlock LSM (Linux 5.13+) file system sandboxing
           + seccomp-bpf syscall filtering
  - FIREJAIL: external sandbox via firejail(1)
  - BUBBLEWRAP: external sandbox via bubblewrap(1)

Usage:
    from niruvi.core.sandbox import Shield, ShieldConfig, SandboxBackend
    config = ShieldConfig(portable_home=True, portable_config=True)
    sb = Shield(config)
    sb.run(["/path/to/AppRun"])
"""

import ctypes
import ctypes.util
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any

logger = logging.getLogger(__name__)


class SandboxBackend:
    SHIELD = "shield"
    FIREJAIL = "firejail"
    BUBBLEWRAP = "bwrap"


PR_SET_NO_NEW_PRIVS = 38
PR_SET_DUMPABLE = 4
PR_SET_PTRACER = 0x59616D61
PR_SET_SECCOMP = 22

PR_SET_PTRACER_DISABLE = 0

MCL_CURRENT = 1

RLIMIT_NPROC = 6
RLIMIT_NOFILE = 7
RLIMIT_FSIZE = 1
RLIMIT_CORE = 4
RLIMIT_STACK = 3
RLIMIT_CPU = 0
RLIMIT_AS = 9
RLIMIT_DATA = 2

SECCOMP_SET_MODE_FILTER = 1

XDG_SHORTCUTS = {
    "xdg-download": "~/Downloads",
    "xdg-documents": "~/Documents",
    "xdg-pictures": "~/Pictures",
    "xdg-music": "~/Music",
    "xdg-videos": "~/Videos",
    "xdg-cache": "~/.cache",
    "xdg-config": "~/.config",
    "xdg-data": "~/.local/share",
    "xdg-bin": "~/.local/bin",
    "xdg-statedir": "~/.local/state",
}


def resolve_xdg(path: str) -> str:
    raw = path.rsplit(":", 1)[0] if (path.endswith(":rw") or path.endswith(":ro")) else path
    for shortcut, real in XDG_SHORTCUTS.items():
        if raw == shortcut:
            return real
        if raw.startswith(shortcut + "/"):
            return real + raw[len(shortcut) :]
    return path


def _is_under(child: str, parent: str) -> bool:
    child = os.path.realpath(os.path.expanduser(child))
    parent = os.path.realpath(os.path.expanduser(parent))
    if not parent.endswith("/"):
        parent += "/"
    return child == parent.rstrip("/") or child.startswith(parent)


class _RLimit(ctypes.Structure):
    _fields_ = [("rlim_cur", ctypes.c_ulong), ("rlim_max", ctypes.c_ulong)]


def _apply_rlimits_ctypes():
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    libc.getrlimit.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.setrlimit.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.getrlimit.restype = ctypes.c_int
    libc.setrlimit.restype = ctypes.c_int

    def _set_if_lower(rlimit, wanted_cur, wanted_max):
        current = _RLimit()
        if libc.getrlimit(rlimit, ctypes.byref(current)) != 0:
            return
        new_cur = min(wanted_cur, current.rlim_cur)
        new_max = min(wanted_max, current.rlim_max)
        if new_cur < current.rlim_cur or new_max < current.rlim_max:
            rl = _RLimit(new_cur, new_max)
            libc.setrlimit(rlimit, ctypes.byref(rl))

    _set_if_lower(RLIMIT_CORE, 0, 0)
    _set_if_lower(RLIMIT_FSIZE, 268435456, 268435456)
    _set_if_lower(RLIMIT_NOFILE, 8192, 65536)
    _set_if_lower(RLIMIT_NPROC, 65536, 65536)
    _set_if_lower(RLIMIT_STACK, 8388608, 33554432)


def _apply_memory_hardening():
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.mlockall.argtypes = [ctypes.c_int]
        libc.mlockall.restype = ctypes.c_int
        if libc.mlockall(MCL_CURRENT) != 0:
            logger.debug("mlockall failed: errno=%d", ctypes.get_errno())
    except Exception as e:
        logger.debug("mlockall: %s", e)


def _apply_ptrace_scope():
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int
        libc.prctl(PR_SET_PTRACER, PR_SET_PTRACER_DISABLE, 0, 0, 0)
    except Exception as e:
        logger.debug("PR_SET_PTRACER: %s", e)


_SECCOMP_SYSCALLS: dict[str, dict[str, int]] = {
    "x86_64": {
        "kexec_load": 246,
        "process_vm_readv": 310,
        "process_vm_writev": 311,
        "kexec_file_load": 320,
        # "bpf": 321,  # Chrome/Chromium needs bpf() for its own internal sandbox
        "userfaultfd": 323,
        "iopl": 172,
        "ioperm": 173,
        "swapon": 87,
        "swapoff": 88,
        "pivot_root": 155,
        "syslog": 103,
    },
    "aarch64": {
        "kexec_load": 104,
        "process_vm_readv": 270,
        "process_vm_writev": 271,
        "kexec_file_load": 294,
        # "bpf": 280,  # Chrome/Chromium needs bpf() for its own internal sandbox
        "userfaultfd": 282,
        "pivot_root": 41,
        "swapon": 95,
        "swapoff": 96,
        "syslog": 116,
    },
}

_AUDIT_ARCH: dict[str, int] = {
    "x86_64": 0xC000003E,
    "i386": 0x40000003,
    "aarch64": 0xC00000B7,
    "arm": 0x40000028,
    "riscv64": 0xC00000F3,
}


def _seccomp_syscalls_for_arch() -> dict[str, int] | None:
    """Return the blocked syscall table for the current architecture, if known."""
    import platform

    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return _SECCOMP_SYSCALLS["x86_64"]
    if machine in ("aarch64", "arm64"):
        return _SECCOMP_SYSCALLS["aarch64"]
    if machine in ("i386", "i686", "arm", "armv7l", "riscv64"):
        # No reliable syscall table — skip seccomp rather than risk killing apps.
        return None
    return None


def _bpf(code, jt, jf, k) -> bytes:
    import struct

    return struct.pack("<HBBI", code, jt, jf, k)


def _build_seccomp_filter() -> bytes:
    """Build a BPF seccomp filter using a denylist approach.

    Allows all syscalls by default, only blocking clearly dangerous ones
    (kexec, bpf, userfaultfd, etc.). This avoids killing Electron/Chromium
    apps that use many legitimate syscalls not in an allowlist.

    BPF instruction format:
      struct sock_filter {
          __u16 code;
          __u8  jt;    // jump-if-true
          __u8  jf;    // jump-if-false
          __u32 k;     // generic multiuse field
      };
    """
    import platform

    BPF_LD = 0x00
    BPF_W = 0x00
    BPF_ABS = 0x20
    BPF_JMP = 0x05
    BPF_JEQ = 0x10
    BPF_JSET = 0x40
    BPF_RET = 0x06
    SECCOMP_RET_KILL = 0x00000000
    SECCOMP_RET_ALLOW = 0x7FFF0000

    # x32 ABI syscalls carry bit 30 set; without this check the x32-numbered
    # aliases of blocked syscalls would evade the denylist entirely.
    X32_SYSCALL_BIT = 0x40000000

    syscalls = _seccomp_syscalls_for_arch()
    machine = platform.machine().lower()
    audit_arch = _AUDIT_ARCH.get(machine)
    if syscalls is None or audit_arch is None:
        # Allow-everything filter — never kills the app, but the caller
        # should skip seccomp entirely in that case.
        return _bpf(BPF_RET, 0, 0, SECCOMP_RET_ALLOW)

    arch_offset = 4
    nr_offset = 0

    instructions = [_bpf(BPF_LD | BPF_W | BPF_ABS, 0, 0, arch_offset)]
    instructions.append(_bpf(BPF_JMP | BPF_JEQ, 0, 1, audit_arch))
    instructions.append(_bpf(BPF_RET, 0, 0, SECCOMP_RET_KILL))

    instructions.append(_bpf(BPF_LD | BPF_W | BPF_ABS, 0, 0, nr_offset))

    # Kill every x32-ABI invocation regardless of syscall number
    instructions.append(_bpf(BPF_JMP | BPF_JSET, 0, 1, X32_SYSCALL_BIT))
    instructions.append(_bpf(BPF_RET, 0, 0, SECCOMP_RET_KILL))

    for nr in sorted(set(syscalls.values())):
        instructions.append(_bpf(BPF_JMP | BPF_JEQ, 0, 1, nr))
        instructions.append(_bpf(BPF_RET, 0, 0, SECCOMP_RET_KILL))

    instructions.append(_bpf(BPF_RET, 0, 0, SECCOMP_RET_ALLOW))

    return b"".join(instructions)


def _apply_seccomp():
    """Apply seccomp-bpf filter to block dangerous syscalls.

    Skipped on architectures without a known syscall table so that
    non-x86_64/aarch64 apps are never killed by an incorrect filter.
    """
    try:
        import platform

        syscalls = _seccomp_syscalls_for_arch()
        machine = platform.machine().lower()
        if syscalls is None or machine not in _AUDIT_ARCH:
            logger.debug("seccomp: skipping, no syscall table for arch %r", machine)
            return
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int

        filter_data = _build_seccomp_filter()

        class SockFprog(ctypes.Structure):
            _fields_ = [
                ("len", ctypes.c_ushort),
                ("filter", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        prog = SockFprog()
        prog.len = len(filter_data) // 8
        filter_array = (ctypes.c_ubyte * len(filter_data)).from_buffer_copy(filter_data)
        prog.filter = filter_array

        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            logger.debug("PR_SET_NO_NEW_PRIVS for seccomp failed")
            return

        result = libc.prctl(PR_SET_SECCOMP, SECCOMP_SET_MODE_FILTER, ctypes.byref(prog), 0, 0)
        if result != 0:
            logger.debug("seccomp filter apply failed: errno=%d", ctypes.get_errno())
    except Exception as e:
        logger.debug("seccomp: %s", e)


# ── Landlock LSM (direct syscall implementation) ─────────────────────────
# CPython does not expose os.landlock_restrict_self(), so the landlock
# syscalls are issued via libc.syscall() — same approach as the seccomp
# filter above. The syscall numbers are identical on every architecture
# that uses the generic table (x86_64, aarch64, riscv64) since Linux 5.13.
_LANDLOCK_NR = {
    # machine -> (create_ruleset, add_rule, restrict_self)
    "x86_64": (444, 445, 446),
    "amd64": (444, 445, 446),
    "aarch64": (444, 445, 446),
    "arm64": (444, 445, 446),
    "riscv64": (444, 445, 446),
}

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_RULE_PATH_BENEATH = 1

# LANDLOCK_ACCESS_FS_* bits (ABI v1 base set, Linux 5.13+)
_LL_EXECUTE = 1 << 0
_LL_WRITE_FILE = 1 << 1
_LL_READ_FILE = 1 << 2
_LL_READ_DIR = 1 << 3
_LL_REMOVE_DIR = 1 << 4
_LL_REMOVE_FILE = 1 << 5
_LL_MAKE_CHAR = 1 << 6
_LL_MAKE_DIR = 1 << 7
_LL_MAKE_REG = 1 << 8
_LL_MAKE_SOCK = 1 << 9
_LL_MAKE_FIFO = 1 << 10
_LL_MAKE_BLOCK = 1 << 11
_LL_MAKE_SYM = 1 << 12
# Higher-ABI rights (REFER bit 13, TRUNCATE bit 14, IOCTL_DEV bit 15) are
# deliberately NOT handled: handling them would deny truncation/cross-dir
# renames everywhere they are not granted, breaking ordinary apps. Writes
# remain gated by WRITE_FILE on open(2), so unhandled TRUNCATE does not
# allow modifying files that could not be opened for writing anyway.
_LANDLOCK_HANDLED_FS = (
    _LL_EXECUTE
    | _LL_WRITE_FILE
    | _LL_READ_FILE
    | _LL_READ_DIR
    | _LL_REMOVE_DIR
    | _LL_REMOVE_FILE
    | _LL_MAKE_CHAR
    | _LL_MAKE_DIR
    | _LL_MAKE_REG
    | _LL_MAKE_SOCK
    | _LL_MAKE_FIFO
    | _LL_MAKE_BLOCK
    | _LL_MAKE_SYM
)

_LANDLOCK_RO_ACCESS = _LL_EXECUTE | _LL_READ_FILE | _LL_READ_DIR
_LANDLOCK_RW_ACCESS = _LANDLOCK_HANDLED_FS


class _LandlockRulesetAttr(ctypes.Structure):
    """struct landlock_ruleset_attr { __u64 handled_access_fs; }"""

    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    """struct landlock_path_beneath_attr (packed): __u64 allowed_access;
    __s32 parent_fd."""

    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
        ("reserved", ctypes.c_uint32),
    ]


def _landlock_syscall_numbers() -> tuple[int, int, int] | None:
    import platform

    return _LANDLOCK_NR.get(platform.machine().lower())


def landlock_supported() -> bool:
    """True when this kernel supports Landlock (ABI >= 1) on this architecture."""
    numbers = _landlock_syscall_numbers()
    if numbers is None:
        return False
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.syscall.restype = ctypes.c_long
        abi = libc.syscall(numbers[0], None, 0, LANDLOCK_CREATE_RULESET_VERSION)
        return int(abi) >= 1
    except Exception as e:
        logger.debug("Landlock probe failed: %s", e)
        return False


def _apply_landlock(paths_readonly: list[str] | None, paths_rw: list[str] | None) -> bool:
    """Apply Landlock LSM restrictions for file system sandboxing.

    Access beneath ``paths_readonly`` is limited to read/execute; access
    beneath ``paths_rw`` is granted fully (for the handled right set).
    Everything outside those trees loses write/create rights for the
    duration of the process. Requires Linux 5.13+; returns True when the
    restrictions were actually enforced.

    If any rule cannot be added, restriction is aborted rather than applied
    partially (a partial ruleset would break the sandboxed app outright).
    """
    numbers = _landlock_syscall_numbers()
    if numbers is None:
        logger.debug("Landlock not available: unsupported architecture")
        return False
    nr_create, nr_add, nr_restrict = numbers

    libc = None
    ruleset_fd = -1
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.syscall.restype = ctypes.c_long
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int

        abi = int(libc.syscall(nr_create, None, 0, LANDLOCK_CREATE_RULESET_VERSION))
        if abi < 1:
            logger.debug("Landlock not available (kernel returned %d)", abi)
            return False

        ruleset_attr = _LandlockRulesetAttr(_LANDLOCK_HANDLED_FS)
        ruleset_fd = int(libc.syscall(nr_create, ctypes.byref(ruleset_attr), ctypes.sizeof(ruleset_attr), 0))
        if ruleset_fd < 0:
            logger.warning("landlock_create_ruleset failed: errno=%d", ctypes.get_errno())
            return False

        def _add_rule(path: str, access: int) -> bool:
            try:
                parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                # Path missing (e.g. optional XDG dir) — nothing to grant
                return True
            try:
                rule = _LandlockPathBeneathAttr(access & _LANDLOCK_HANDLED_FS, parent_fd, 0)
                if libc.syscall(nr_add, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH, ctypes.byref(rule), 0) != 0:
                    logger.warning("landlock_add_rule failed for %s: errno=%d", path, ctypes.get_errno())
                    return False
                return True
            finally:
                os.close(parent_fd)

        ro_paths = list(dict.fromkeys(paths_readonly or []))
        rw_paths = list(dict.fromkeys(paths_rw or []))
        ok = all(_add_rule(p, _LANDLOCK_RO_ACCESS) for p in ro_paths)
        ok = all(_add_rule(p, _LANDLOCK_RW_ACCESS) for p in rw_paths) and ok
        if not ok:
            logger.warning("Landlock rules incomplete — NOT restricting (partial ruleset would break the app)")
            os.close(ruleset_fd)
            return False

        # landlock_restrict_self requires PR_SET_NO_NEW_PRIVS (already set by
        # the preexec hook; re-assert defensively).
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            logger.warning("Landlock: PR_SET_NO_NEW_PRIVS failed")
            os.close(ruleset_fd)
            return False

        if libc.syscall(nr_restrict, ruleset_fd, 0) != 0:
            logger.warning("landlock_restrict_self failed: errno=%d", ctypes.get_errno())
            os.close(ruleset_fd)
            return False
        os.close(ruleset_fd)
        logger.info("Landlock enforced: ro=%d paths, rw=%d paths", len(ro_paths), len(rw_paths))
        return True
    except Exception as e:
        logger.warning("Landlock enforcement failed: %s", e, exc_info=True)
        if ruleset_fd >= 0:
            try:
                os.close(ruleset_fd)
            except OSError:
                pass
        return False


def _parse_size_limit(value: str) -> int | None:
    """Parse a size string like '512M', '2G', '1073741824' into bytes."""
    if not value:
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([kmg]?)\s*$", value.strip().lower())
    if not m:
        logger.debug("Unparseable size limit: %r", value)
        return None
    number, suffix = m.group(1), m.group(2)
    mult = {"": 1, "k": 1024, "m": 1024 * 1024, "g": 1024 * 1024 * 1024}[suffix]
    return int(float(number) * mult)


def _apply_resource_limits(memory_limit: str = "", cpu_limit: str = ""):
    """Apply RLIMIT_AS/DATA (memory) and RLIMIT_CPU from ShieldConfig."""
    mem = _parse_size_limit(memory_limit)
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.getrlimit.argtypes = [ctypes.c_int, ctypes.c_void_p]
        libc.setrlimit.argtypes = [ctypes.c_int, ctypes.c_void_p]
        libc.getrlimit.restype = ctypes.c_int
        libc.setrlimit.restype = ctypes.c_int

        if mem:
            for resource in (RLIMIT_AS, RLIMIT_DATA):
                current = _RLimit()
                if libc.getrlimit(resource, ctypes.byref(current)) == 0 and mem < current.rlim_cur:
                    rl = _RLimit(mem, mem)
                    libc.setrlimit(resource, ctypes.byref(rl))
        if cpu_limit:
            try:
                seconds = int(float(cpu_limit))
            except ValueError:
                seconds = 0
            if seconds > 0:
                current = _RLimit()
                if libc.getrlimit(RLIMIT_CPU, ctypes.byref(current)) == 0 and seconds < current.rlim_cur:
                    rl = _RLimit(seconds, min(seconds + 5, current.rlim_max))
                    libc.setrlimit(RLIMIT_CPU, ctypes.byref(rl))
    except Exception as e:
        logger.debug("Resource limits failed: %s", e)


def _make_preexec(
    ro_paths: list[str] | None = None, rw_paths: list[str] | None = None, memory_limit: str = "", cpu_limit: str = ""
):
    """Return a preexec_fn closure that applies hardening + optional Landlock in the child."""

    def _preexec():
        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
            libc.prctl.restype = ctypes.c_int
            if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
                logger.debug("PR_SET_NO_NEW_PRIVS failed: errno=%d", ctypes.get_errno())
            if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
                logger.debug("PR_SET_DUMPABLE failed: errno=%d", ctypes.get_errno())
        except Exception as e:
            logger.debug("prctl hardening failed: %s", e)

        try:
            _apply_rlimits_ctypes()
        except Exception as e:
            logger.debug("rlimit hardening failed: %s", e)

        try:
            _apply_resource_limits(memory_limit, cpu_limit)
        except Exception as e:
            logger.debug("resource limit hardening failed: %s", e)

        try:
            with open("/proc/self/oom_score_adj", "w") as f:
                f.write("-500\n")
        except OSError:
            pass

        _apply_memory_hardening()
        _apply_ptrace_scope()
        _apply_seccomp()

        if ro_paths is not None:
            _apply_landlock(ro_paths, rw_paths or [])

    return _preexec


class XdgOpenDaemon:
    """Forwards xdg-open calls from sandboxed app to host via FIFO."""

    def __init__(self):
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self._fifo_path: str | None = None
        self._wrapper_path: str | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> str | None:
        try:
            self._tmpdir = tempfile.TemporaryDirectory(prefix="niruvi-xdg-open-")
            wrapper_dir = self._tmpdir.name
            os.chmod(wrapper_dir, 0o700)
            self._fifo_path = os.path.join(wrapper_dir, "fifo")
            self._wrapper_path = os.path.join(wrapper_dir, "xdg-open")
            os.mkfifo(self._fifo_path, 0o600)
            with open(self._wrapper_path, "w") as f:
                f.write(f"""#!/bin/sh
for arg; do
    printf '%s\\n' "$arg" > "{self._fifo_path}"
done
exit 0
""")
            os.chmod(self._wrapper_path, 0o755)
            self._running = True
            self._thread = threading.Thread(target=self._listener, daemon=True)
            self._thread.start()
            return wrapper_dir
        except Exception as e:
            logger.warning("Failed to start xdg-open daemon: %s", e)
            self._cleanup()
            return None

    # Loopback hosts stay reachable so OAuth callbacks (http://localhost:PORT)
    # work from sandboxed apps; every other IP literal in private, link-local
    # or reserved space is rejected to prevent intranet probing / cloud
    # metadata access (169.254.169.254) through the host's browser.
    _ALLOWED_SCHEMES = {"http", "https", "ftp", "mailto", "x-scheme-handler"}
    _BLOCKED_HOSTS: set[str] = set()
    _NET_SCHEMES = {"http", "https", "ftp"}

    @staticmethod
    def _addr_allowed(addr_text: str) -> bool:
        """True for a single resolved address (or IP literal) that is safe to
        hand to the host browser: loopback or global unicast. Everything in
        private/link-local/reserved space (cloud metadata endpoints,
        intranets, CGNAT) is rejected."""
        import ipaddress

        try:
            ip = ipaddress.ip_address(addr_text.strip())
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped  # ::ffff:10.0.0.1 style bypasses
        if ip.is_loopback:
            return True
        if ip.is_multicast or ip.is_unspecified:
            return False
        return bool(ip.is_global)

    @staticmethod
    def _resolve_host(host: str) -> bool:
        """Resolve ``host`` via DNS and allow only when EVERY resolved
        address passes :meth:`_addr_allowed`. This closes the hole where an
        attacker-controlled name (e.g. ``169.254.169.254.nip.io``) resolves
        into link-local/metadata space while looking like a public domain."""
        import socket

        try:
            infos = socket.getaddrinfo(host, None)
        except (OSError, UnicodeError):
            return False
        addrs = {info[4][0] for info in infos if isinstance(info[4][0], str)}
        if not addrs:
            return False
        return all(XdgOpenDaemon._addr_allowed(addr) for addr in addrs)

    @classmethod
    def _host_allowed(cls, hostname: str | None) -> bool:
        """True for loopback names/IPs and public hostnames; False for any
        address in private/link-local/reserved space. Hostnames are actually
        resolved so a DNS name pointing at e.g. 169.254.169.254 cannot slip
        through as a 'regular domain'."""
        import ipaddress

        if not hostname:
            return False
        host = hostname.lower().strip("[]").rstrip(".")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return cls._addr_allowed(host)
        if host == "localhost" or host.endswith(".localhost"):
            return True
        if host.endswith(".local"):
            return False
        return cls._resolve_host(host)

    @classmethod
    def _is_safe_url(cls, url: str) -> bool:
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            scheme = parsed.scheme.lower()
            if not scheme:
                logger.warning("Blocked URL without a scheme: %s", url)
                return False
            if scheme not in cls._ALLOWED_SCHEMES:
                logger.warning("Blocked URL with scheme '%s': %s", parsed.scheme, url)
                return False
            if scheme in cls._NET_SCHEMES and not cls._host_allowed(parsed.hostname):
                logger.warning("Blocked URL to non-loopback/private host '%s': %s", parsed.hostname, url)
                return False
            if parsed.hostname and parsed.hostname.lower() in cls._BLOCKED_HOSTS:
                logger.warning("Blocked URL: %s", url)
                return False
            return True
        except Exception as e:
            logger.debug("URL parse failed in _is_safe_url: %s", e, exc_info=True)
            return False

    def _listener(self):
        while self._running:
            try:
                if self._fifo_path is None:
                    break
                with open(self._fifo_path) as fifo:
                    for line in fifo:
                        url = line.strip()
                        if url and self._is_safe_url(url):
                            try:
                                subprocess.Popen(
                                    ["xdg-open", url],
                                    start_new_session=True,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                            except Exception as e:
                                logger.debug("xdg-open call failed: %s", e)
                        elif url:
                            logger.warning("Blocked unsafe URL from sandbox: %s", url)
            except OSError:
                break
            except Exception as e:
                logger.debug("xdg-open listener error: %s", e)

    def stop(self):
        self._running = False
        self._cleanup()

    def _cleanup(self):
        if self._tmpdir:
            try:
                self._tmpdir.cleanup()
            except OSError:
                pass
            self._tmpdir = None


_xdg_open_daemon: XdgOpenDaemon | None = None
_xdg_open_lock = threading.Lock()


def _get_xdg_open_daemon() -> XdgOpenDaemon | None:
    global _xdg_open_daemon
    if _xdg_open_daemon is None:
        with _xdg_open_lock:
            if _xdg_open_daemon is None:
                d = XdgOpenDaemon()
                if d.start():
                    _xdg_open_daemon = d
                else:
                    return None
    return _xdg_open_daemon


def _which(name: str) -> str | None:
    return shutil.which(name)


def check_firejail_available() -> dict:
    result: dict[str, Any] = {
        "available": False,
        "version": None,
        "error": None,
    }
    try:
        r = subprocess.run(
            ["firejail", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            result["available"] = True
            result["version"] = r.stdout.splitlines()[0] if r.stdout else "unknown"
    except FileNotFoundError:
        result["error"] = "firejail not found in PATH"
    except subprocess.TimeoutExpired:
        result["error"] = "firejail --version timed out"
    except Exception as e:
        result["error"] = str(e)
    return result


def check_bwrap_available() -> dict:
    result: dict[str, Any] = {
        "available": False,
        "version": None,
        "error": None,
    }
    try:
        r = subprocess.run(
            ["bwrap", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            result["available"] = True
            result["version"] = r.stdout.splitlines()[0] if r.stdout else "unknown"
    except FileNotFoundError:
        result["error"] = "bwrap not found in PATH"
    except subprocess.TimeoutExpired:
        result["error"] = "bwrap --version timed out"
    except Exception as e:
        result["error"] = str(e)
    return result


class ShieldConfig:
    def __init__(
        self,
        enabled: bool = False,
        hardening: bool = True,
        portable_home: bool = False,
        portable_config: bool = False,
        backend: str = SandboxBackend.SHIELD,
        enable_network: bool = True,
        enable_dbus: bool = True,
        enable_x11: bool = True,
        enable_pulse: bool = True,
        memory_limit: str = "",
        cpu_limit: str = "",
        timeout: int = 0,
        private_tmp: bool = False,
        seccomp: bool = True,
        landlock: bool = True,
        use_namespace: bool = True,
    ):
        self.enabled = enabled
        self.hardening = hardening
        self.portable_home = portable_home
        self.portable_config = portable_config
        self.backend = backend
        self.enable_network = enable_network
        self.enable_dbus = enable_dbus
        self.enable_x11 = enable_x11
        self.enable_pulse = enable_pulse
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.timeout = timeout
        self.private_tmp = private_tmp
        self.seccomp = seccomp
        self.landlock = landlock
        self.use_namespace = use_namespace

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            enabled=d.get("enabled", False),
            hardening=d.get("hardening", True),
            portable_home=d.get("portable_home", False),
            portable_config=d.get("portable_config", False),
            backend=d.get("backend", SandboxBackend.SHIELD),
            enable_network=d.get("enable_network", True),
            enable_dbus=d.get("enable_dbus", True),
            enable_x11=d.get("enable_x11", True),
            enable_pulse=d.get("enable_pulse", True),
            memory_limit=d.get("memory_limit", ""),
            cpu_limit=d.get("cpu_limit", ""),
            timeout=d.get("timeout", 0),
            private_tmp=d.get("private_tmp", False),
            seccomp=d.get("seccomp", True),
            landlock=d.get("landlock", True),
            use_namespace=d.get("use_namespace", True),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "hardening": self.hardening,
            "portable_home": self.portable_home,
            "portable_config": self.portable_config,
            "backend": self.backend,
            "enable_network": self.enable_network,
            "enable_dbus": self.enable_dbus,
            "enable_x11": self.enable_x11,
            "enable_pulse": self.enable_pulse,
            "memory_limit": self.memory_limit,
            "cpu_limit": self.cpu_limit,
            "timeout": self.timeout,
            "private_tmp": self.private_tmp,
            "seccomp": self.seccomp,
            "landlock": self.landlock,
            "use_namespace": self.use_namespace,
        }


# Minimal set of env vars needed for display/audio/IPC to work.
# Includes session/desktop identity vars so portals, theming and
# browser-based OAuth flows (xdg-open, portals) work inside the sandbox.
ALWAYS_KEEP_ENV = {
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "DBUS_SYSTEM_BUS_ADDRESS",
    "PULSE_SERVER",
    "PIPEWIRE_RUNTIME_DIR",
    "PULSE_COOKIE",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_MESSAGES",
    "QT_QPA_PLATFORM",
    "QT_QPA_PLATFORMTHEME",
    "GDK_BACKEND",
    "SDL_VIDEO_DRIVER",
    "SDL_AUDIODRIVER",
    "TZ",
    "SHELL",
    "TERM",
    # Session / desktop environment identification (portals + theming)
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_DESKTOP",
    "DESKTOP_SESSION",
    "GNOME_DESKTOP_SESSION_ID",
    "KDE_FULL_SESSION",
    "XDG_MENU_PREFIX",
    "GTK_MODULES",
    # Config/data search paths so sandboxed apps find themes and mime info
    "XDG_CONFIG_DIRS",
    "XDG_DATA_DIRS",
    # Browser/OAuth: let apps open external login URLs via xdg-open/BROWSER
    "BROWSER",
    # GPU / hardware acceleration driver selection
    "LIBGL_DRIVERS_PATH",
    "MESA_LOADER_DRIVER_OVERRIDE",
    "__GLX_VENDOR_LIBRARY_NAME",
    "VK_ICD_FILENAMES",
    # AppImage runtime vars
    "APPIMAGE",
    "APPDIR",
    "OWD",
}

# Environment variables that are always inherited from the host even though
# they are not part of the session-identity set above.
_REQUIRED_ENV = ("PATH", "TMPDIR", "TMP", "TEMP")

HARDENED_ENV = {
    "GLIBC_TUNABLES": "glibc.malloc.perturb=0x42:glibc.malloc.tcache_count=0:glibc.malloc.mxfast=0",
    "MALLOC_PERTURB_": "66",
    "MALLOC_CHECK_": "3",
    "LD_BIND_NOW": "1",
}


def _bridge_audio_config(portable_home: str):
    """Bridge PipeWire/D-Bus config into portable home.

    Uses copies instead of symlinks to avoid leaking host paths.
    PulseAudio cookie is NOT copied — sandboxed apps use PULSE_SERVER
    socket instead, preventing microphone eavesdropping.
    """
    real_config = os.path.expanduser("~/.config")
    pw_config = os.path.join(portable_home, ".config", "pipewire")
    try:
        os.makedirs(pw_config, exist_ok=True)
        real_pw = os.path.join(real_config, "pipewire", "client.conf")
        portable_pw = os.path.join(pw_config, "client.conf")
        if os.path.isfile(real_pw) and not os.path.isfile(portable_pw):
            shutil.copy2(real_pw, portable_pw)
    except OSError:
        pass
    dbus_config = os.path.join(portable_home, ".config", "dbus")
    try:
        os.makedirs(dbus_config, exist_ok=True)
        machine_id = "/etc/machine-id"
        portable_mid = os.path.join(dbus_config, "machine-id")
        if os.path.isfile(machine_id) and not os.path.isfile(portable_mid):
            shutil.copy2(machine_id, portable_mid)
    except OSError:
        pass


class _ProcessWatchdog:
    """Kills a sandboxed process group after a wall-clock timeout."""

    def __init__(self, proc: subprocess.Popen, timeout_seconds: int):
        self._proc = proc
        self._timeout = timeout_seconds
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self):
        try:
            self._proc.wait(timeout=self._timeout)
            return
        except subprocess.TimeoutExpired:
            pass
        if self._proc.poll() is None:
            logger.warning("Sandbox watchdog: killing process after %d s timeout", self._timeout)
            try:
                os.killpg(os.getpgid(self._proc.pid), 9)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self._proc.kill()
            except Exception:
                pass


class Shield:
    def __init__(self, config: ShieldConfig):
        self.config = config
        self._watchdog: _ProcessWatchdog | None = None

    def run(self, cmd: list[str], cwd: str | None = None, env: dict | None = None) -> subprocess.Popen | None:
        if not self.config.enabled:
            return self._run_direct(cmd, cwd, env)

        backend = self.config.backend or SandboxBackend.SHIELD

        env = self._build_env(env)
        app_dir = cwd or ""

        if self.config.portable_home and app_dir:
            env["HOME"] = os.path.join(app_dir, ".home")
        if self.config.portable_config and app_dir:
            env["XDG_CONFIG_HOME"] = os.path.join(app_dir, ".config")

        if self.config.portable_home and app_dir:
            portable_home = os.path.join(app_dir, ".home")
            _bridge_audio_config(portable_home)

        if backend == SandboxBackend.FIREJAIL:
            return self._run_firejail(cmd, cwd, env)
        elif backend == SandboxBackend.BUBBLEWRAP:
            return self._run_bwrap(cmd, cwd, env)
        else:
            return self._run_shield(cmd, cwd, env)

    def _run_shield(self, cmd: list[str], cwd: str | None, env: dict | None) -> subprocess.Popen | None:
        env = env or {}
        app_dir = cwd or ""

        xd = _get_xdg_open_daemon()
        if xd and xd._tmpdir:
            wdir = xd._tmpdir.name
            env["PATH"] = f"{wdir}:" + env.get("PATH", os.environ.get("PATH", ""))
            env["BROWSER"] = os.path.join(wdir, "xdg-open")

        try:
            from niruvi.core.broker import get_daemon as _get_perm_daemon

            perm_daemon = _get_perm_daemon()
            if perm_daemon and perm_daemon.fifo_dir:
                env["NIRUVI_PERM_BROKER"] = perm_daemon.fifo_dir
        except Exception as e:
            logger.debug("Failed to inject permission broker into sandbox env: %s", e, exc_info=True)

        if self.config.hardening:
            ro_paths = None
            rw_paths = None
            if self.config.landlock:
                ro_paths = ["/usr", "/etc", "/lib", "/lib64", "/bin", "/sbin"]
                rw_paths = []
                if app_dir and os.path.isdir(app_dir):
                    ro_paths.append(app_dir)
                runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
                if runtime_dir and os.path.isdir(runtime_dir):
                    rw_paths.append(runtime_dir)
                home = os.path.expanduser("~")
                if self.config.portable_home and app_dir:
                    rw_paths.append(os.path.join(app_dir, ".home"))
                elif not self.config.portable_home:
                    rw_paths.append(home)
                if self.config.portable_config and app_dir:
                    rw_paths.append(os.path.join(app_dir, ".config"))
                elif not self.config.portable_config:
                    rw_paths.append(os.path.join(home, ".config"))
                # Standard user data directories so sandboxed apps can open,
                # save, and use file dialogs against Downloads/Documents/etc.
                xdg_dirs = {
                    "XDG_DOWNLOAD_DIR": "~/Downloads",
                    "XDG_DOCUMENTS_DIR": "~/Documents",
                    "XDG_PICTURES_DIR": "~/Pictures",
                    "XDG_MUSIC_DIR": "~/Music",
                    "XDG_VIDEOS_DIR": "~/Videos",
                    "XDG_DESKTOP_DIR": "~/Desktop",
                }
                for env_var, default in xdg_dirs.items():
                    path = os.environ.get(env_var) or os.path.expanduser(default)
                    path = os.path.expanduser(path)
                    if os.path.isdir(path) and path not in rw_paths:
                        rw_paths.append(path)
                # Scratch space: without these grants Landlock would break
                # virtually every app that writes temp files or POSIX shm.
                for scratch in ("/tmp", "/var/tmp", "/dev/shm"):
                    if os.path.isdir(scratch):
                        rw_paths.append(scratch)
                if self.config.private_tmp and app_dir:
                    # TMPDIR lives inside the read-only app_dir tree; grant it
                    # explicitly so the app can actually use it.
                    rw_paths.append(os.path.join(app_dir, ".tmp"))
            preexec_fn = _make_preexec(
                ro_paths,
                rw_paths,
                memory_limit=self.config.memory_limit,
                cpu_limit=self.config.cpu_limit,
            )
        elif self.config.memory_limit or self.config.cpu_limit:
            # Resource limits apply even when hardening is disabled
            preexec_fn = _make_preexec(
                None,
                None,
                memory_limit=self.config.memory_limit,
                cpu_limit=self.config.cpu_limit,
            )
        else:
            preexec_fn = None

        if self.config.portable_home and app_dir:
            os.makedirs(os.path.join(app_dir, ".home"), exist_ok=True)
        if self.config.portable_config and app_dir:
            os.makedirs(os.path.join(app_dir, ".config"), exist_ok=True)

        if self.config.private_tmp and app_dir:
            app_tmp = os.path.join(app_dir, ".tmp")
            os.makedirs(app_tmp, exist_ok=True)
            env["TMPDIR"] = app_tmp
            env["TMP"] = app_tmp
            env["TEMP"] = app_tmp

        cmd_to_run = cmd
        if self.config.use_namespace and not self.config.enable_network:
            runner = ["unshare", "--map-root-user", "--mount", "--net"]
            cmd_to_run = runner + cmd

        p = subprocess.Popen(
            cmd_to_run,
            cwd=cwd or os.getcwd(),
            env=env,
            start_new_session=True,
            preexec_fn=preexec_fn,
        )

        if self.config.timeout and self.config.timeout > 0:
            self._watchdog = _ProcessWatchdog(p, self.config.timeout)
            self._watchdog.start()

        return p

    def _run_firejail(self, cmd: list[str], cwd: str | None, env: dict | None) -> subprocess.Popen | None:
        app_dir = cwd or ""
        fj_cmd = ["firejail"]

        if self.config.portable_home and app_dir:
            home_dir = os.path.join(app_dir, ".home")
            os.makedirs(home_dir, exist_ok=True)
            fj_cmd.extend(["--home", home_dir])

        if self.config.portable_config and app_dir:
            cfg_dir = os.path.join(app_dir, ".config")
            os.makedirs(cfg_dir, exist_ok=True)
            fj_cmd.extend(["--private-home", cfg_dir])

        fj_cmd.append("--x11")

        if not self.config.enable_network:
            fj_cmd.append("--net=none")

        if self.config.private_tmp:
            fj_cmd.append("--private-tmp")

        if not self.config.hardening:
            fj_cmd.append("--noroot")
        else:
            fj_cmd.append("--seccomp")

        if self.config.enable_dbus:
            # Filter the session bus but keep xdg-desktop-portal reachable so
            # file dialogs, screenshots and OAuth helpers work inside the jail.
            fj_cmd.append("--dbus-user=filter")
            fj_cmd.append("--dbus-user.talk=org.freedesktop.portal.Desktop")
            fj_cmd.append("--dbus-user.talk=org.freedesktop.portal.*")

        # Keep standard user folders and GPU device access usable
        fj_cmd.append("--whitelist=~/Downloads")
        fj_cmd.append("--whitelist=~/Documents")
        fj_cmd.append("--whitelist=~/Pictures")
        fj_cmd.append("--noblacklist=/dev/dri")

        fj_cmd.append("--")
        fj_cmd.extend(cmd)

        return subprocess.Popen(
            fj_cmd,
            cwd=cwd or os.getcwd(),
            env=env,
            start_new_session=True,
        )

    def _run_bwrap(self, cmd: list[str], cwd: str | None, env: dict | None) -> subprocess.Popen | None:
        app_dir = cwd or ""
        env = env or {}
        home = env.get("HOME") or os.path.expanduser("~")
        bwrap_cmd = [
            "bwrap",
            "--unshare-all",
            "--new-session",
            "--die-with-parent",
        ]

        bwrap_cmd.extend(["--proc", "/proc"])
        bwrap_cmd.extend(["--dev", "/dev"])
        bwrap_cmd.extend(["--ro-bind", "/usr", "/usr"])

        # Compatibility paths (/bin, /sbin, /lib, /lib64): on merged-/usr
        # systems these are symlinks into /usr, on older layouts they are real
        # directories. Binding a symlinked source materializes it as a plain
        # directory inside the sandbox, after which the --symlink fallback
        # aborts with "destination exists and is not a symlink" — so decide
        # per-path based on what the host provides.
        _COMPAT_PATHS = (
            ("/bin", "/usr/bin"),
            ("/sbin", "/usr/sbin"),
            ("/lib", "/usr/lib"),
            ("/lib64", "/usr/lib64"),
        )
        for compat_path, usr_target in _COMPAT_PATHS:
            if os.path.islink(compat_path):
                bwrap_cmd.extend(["--symlink", usr_target, compat_path])
            elif os.path.isdir(compat_path):
                bwrap_cmd.extend(["--ro-bind", compat_path, compat_path])

        bwrap_cmd.extend(["--ro-bind", "/etc", "/etc"])
        bwrap_cmd.extend(["--tmpfs", "/tmp"])
        bwrap_cmd.extend(["--tmpfs", "/var/tmp"])
        bwrap_cmd.extend(["--tmpfs", "/dev/shm"])
        # /run hosts system services' sockets. Bind ONLY what sandboxed apps
        # legitimately need (D-Bus system/session broker dirs) instead of all
        # of /run — a read-write /run would expose privileged sockets such as
        # docker.sock or snapd.socket to the app. The per-user runtime dir
        # (PipeWire, portal, Wayland sockets) is bound separately below.
        if os.path.isdir("/run/dbus"):
            bwrap_cmd.extend(["--ro-bind", "/run/dbus", "/run/dbus"])
        # GPU render nodes + hardware/sysfs introspection for driver detection
        if os.path.isdir("/dev/dri"):
            bwrap_cmd.extend(["--dev-bind", "/dev/dri", "/dev/dri"])
        for sys_dir in ("/sys/class", "/sys/dev", "/sys/devices"):
            if os.path.isdir(sys_dir):
                bwrap_cmd.extend(["--ro-bind", sys_dir, sys_dir])

        if app_dir and os.path.isdir(app_dir):
            bwrap_cmd.extend(["--ro-bind", app_dir, app_dir])

        if self.config.portable_home and app_dir:
            home_dir = os.path.join(app_dir, ".home")
            os.makedirs(home_dir, exist_ok=True)
            bwrap_cmd.extend(["--bind", home_dir, home])
        elif os.path.isdir(os.path.expanduser("~")):
            bwrap_cmd.extend(["--bind", os.path.expanduser("~"), home])

        if self.config.portable_config and app_dir:
            cfg_dir = os.path.join(app_dir, ".config")
            os.makedirs(cfg_dir, exist_ok=True)
            bwrap_cmd.extend(["--bind", cfg_dir, os.path.join(home, ".config")])

        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
        if runtime_dir and os.path.isdir(runtime_dir):
            bwrap_cmd.extend(["--bind", runtime_dir, runtime_dir])

        if self.config.private_tmp:
            app_tmp = os.path.join(app_dir or os.getcwd(), ".tmp")
            os.makedirs(app_tmp, exist_ok=True)
            bwrap_cmd.extend(["--bind", app_tmp, "/tmp"])

        if self.config.enable_network:
            bwrap_cmd.append("--share-net")
        else:
            bwrap_cmd.append("--unshare-net")

        bwrap_cmd.append("--")
        bwrap_cmd.extend(cmd)

        return subprocess.Popen(
            bwrap_cmd,
            cwd=cwd or os.getcwd(),
            env=env,
            start_new_session=True,
        )

    def _build_env(self, base_env: dict | None) -> dict:
        """Build a sandboxed environment.

        Security: the host environment is filtered through a strict
        allowlist (ALWAYS_KEEP_ENV + required vars). Everything else —
        agent sockets, cloud CLI tokens, proxy credentials, arbitrary app
        state — is NOT passed to sandboxed apps. ``base_env`` carries
        trusted caller overrides (per-app env vars from the registry,
        portable-home HOME redirects) and is applied last.
        """
        env: dict[str, str] = {}
        for k in ALWAYS_KEEP_ENV:
            if k in os.environ:
                env[k] = os.environ[k]
        for k in _REQUIRED_ENV:
            if k in os.environ:
                env[k] = os.environ[k]
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env.setdefault("HOME", os.path.expanduser("~"))
        if "USER" not in env and "LOGNAME" not in env:
            import getpass

            try:
                env["USER"] = getpass.getuser()
            except Exception:
                pass
        # Trusted caller-explicit overrides win over inherited values
        if base_env:
            env.update(base_env)
        if self.config.hardening:
            for k, v in HARDENED_ENV.items():
                if k not in env:
                    env[k] = v
        # Prefer xdg-desktop-portal for file dialogs / OAuth helpers inside the
        # sandbox; fall back to a GTK theme so Qt apps are not unstyled.
        if "GTK_USE_PORTAL" not in env:
            env["GTK_USE_PORTAL"] = "1"
        if "QT_QPA_PLATFORMTHEME" not in env:
            env["QT_QPA_PLATFORMTHEME"] = "gtk2"
        return env

    def _run_direct(self, cmd: list[str], cwd: str | None, env: dict | None) -> subprocess.Popen:
        # Unsandboxed launch: inherit the full host environment, then apply
        # caller overrides (per-app env vars) on top.
        result_env = os.environ.copy()
        if env:
            result_env.update(env)
        return subprocess.Popen(
            cmd,
            cwd=cwd or os.getcwd(),
            env=result_env,
            start_new_session=True,
        )


def check_shield_available() -> dict:
    info: dict[str, Any] = {
        "hardening": True,
        "portable_mode": True,
        "xdg_open_daemon": True,
        "memory_locking": True,
        "ptrace_scope": True,
        "malloc_hardening": True,
        "seccomp": True,
        "landlock": landlock_supported(),
        "namespace_unshare": True,
        "backends": {"shield": True},
    }
    fj = check_firejail_available()
    info["backends"]["firejail"] = fj["available"]
    info["firejail_version"] = fj["version"]
    bw = check_bwrap_available()
    info["backends"]["bwrap"] = bw["available"]
    info["bwrap_version"] = bw["version"]
    return info
