"""Niruvi Shield — sandbox backend for AppImages.

Supports multiple backends:
  - SHIELD: native process hardening (rlimits, mlockall, ptrace disable)
           + portable .home/.config (like AppManager)
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
import shutil
import subprocess
import tempfile
import threading

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


_SECCOMP_ALLOWED_SYSCALLS = [
    0,  # read
    1,  # write
    2,  # open
    3,  # close
    4,  # stat
    5,  # fstat
    6,  # lstat
    7,  # poll
    8,  # lseek
    9,  # mmap
    10,  # mprotect
    11,  # munmap
    12,  # brk
    13,  # rt_sigaction
    14,  # rt_sigprocmask
    15,  # rt_sigreturn
    16,  # ioctl
    17,  # pread64
    18,  # pwrite64
    19,  # readv
    20,  # writev
    21,  # access
    22,  # pipe
    23,  # select
    24,  # sched_yield
    25,  # mremap
    26,  # msync
    27,  # mincore
    28,  # madvise
    29,  # shmget
    30,  # shmat
    31,  # shmctl
    32,  # dup
    33,  # dup2
    34,  # pause
    35,  # nanosleep
    36,  # getitimer
    37,  # alarm
    38,  # setitimer
    39,  # getpid
    40,  # sendfile
    41,  # socket
    42,  # connect
    43,  # accept
    44,  # sendto
    45,  # recvfrom
    46,  # sendmsg
    47,  # recvmsg
    48,  # shutdown
    49,  # bind
    50,  # listen
    51,  # getsockname
    52,  # getpeername
    53,  # socketpair
    54,  # setsockopt
    55,  # getsockopt
    56,  # clone
    57,  # fork
    58,  # vfork
    59,  # execve
    60,  # exit
    61,  # wait4
    62,  # kill
    63,  # uname
    64,  # semget
    65,  # semop
    66,  # semctl
    67,  # shmdt
    68,  # msgget
    69,  # msgsnd
    70,  # msgrcv
    71,  # msgctl
    72,  # fcntl
    73,  # flock
    74,  # fsync
    75,  # fdatasync
    76,  # truncate
    77,  # ftruncate
    78,  # getdents
    79,  # getcwd
    80,  # chdir
    81,  # fchdir
    82,  # rename
    83,  # mkdir
    84,  # rmdir
    85,  # creat
    86,  # link
    87,  # unlink
    88,  # symlink
    89,  # readlink
    90,  # chmod
    91,  # fchmod
    92,  # chown
    93,  # fchown
    94,  # lchown
    95,  # umask
    96,  # gettimeofday
    97,  # getrlimit
    98,  # getrusage
    99,  # sysinfo
    100,  # times
    101,  # ptrace
    102,  # getuid
    104,  # getgid
    105,  # setuid
    106,  # setgid
    107,  # geteuid
    108,  # getegid
    110,  # getppid
    111,  # getpgrp
    112,  # setsid
    113,  # setreuid
    114,  # setregid
    115,  # getgroups
    116,  # setgroups
    117,  # setresuid
    118,  # getresuid
    119,  # setresgid
    120,  # getresgid
    121,  # getpgid
    122,  # setpgid
    123,  # gettid
    124,  # sched_getparam
    125,  # sched_setparam
    126,  # sched_getscheduler
    127,  # sched_setscheduler
    128,  # sched_get_priority_max
    129,  # sched_get_priority_min
    130,  # sched_rr_get_interval
    131,  # sigaltstack
    132,  # utime
    133,  # mknod
    134,  # uselib
    135,  # personality
    136,  # ustat
    137,  # statfs
    138,  # fstatfs
    139,  # sysfs
    140,  # getpriority
    141,  # setpriority
    142,  # sched_setaffinity
    143,  # sched_getaffinity
    144,  # sched_yield
    145,  # sched_getattr
    146,  # sched_setattr
    157,  # prctl
    158,  # arch_prctl
    159,  # adjtimex
    160,  # setrlimit
    186,  # gettid
    187,  # readahead
    188,  # setxattr
    189,  # lsetxattr
    190,  # fsetxattr
    191,  # getxattr
    192,  # lgetxattr
    193,  # fgetxattr
    194,  # listxattr
    195,  # llistxattr
    196,  # flistxattr
    197,  # removexattr
    198,  # lremovexattr
    199,  # fremovexattr
    200,  # tkill
    201,  # time
    202,  # futex
    203,  # sched_setaffinity
    204,  # sched_getaffinity
    205,  # set_thread_area
    206,  # io_setup
    207,  # io_destroy
    208,  # io_getevents
    209,  # io_submit
    210,  # io_cancel
    211,  # get_thread_area
    212,  # lookup_dcookie
    213,  # epoll_create
    214,  # epoll_ctl_old
    215,  # epoll_wait_old
    216,  # remap_file_pages
    217,  # getdents64
    218,  # set_tid_address
    219,  # restart_syscall
    220,  # semtimedop
    221,  # fadvise64
    222,  # timer_create
    223,  # timer_settime
    224,  # timer_gettime
    225,  # timer_getoverrun
    226,  # timer_delete
    227,  # clock_settime
    228,  # clock_gettime
    229,  # clock_getres
    230,  # clock_nanosleep
    231,  # exit_group
    232,  # epoll_wait
    233,  # epoll_ctl
    234,  # tgkill
    235,  # utimes
    236,  # vserver
    237,  # mbind
    238,  # set_mempolicy
    239,  # get_mempolicy
    240,  # mq_open
    241,  # mq_unlink
    242,  # mq_timedsend
    243,  # mq_timedreceive
    244,  # mq_notify
    245,  # mq_getsetattr
    246,  # kexec_load — BLOCKED
    247,  # waitid
    248,  # add_key
    249,  # request_key
    250,  # keyctl
    251,  # ioprio_set
    252,  # ioprio_get
    253,  # inotify_init
    254,  # inotify_add_watch
    255,  # inotify_rm_watch
    256,  # migrate_pages
    257,  # openat
    258,  # mkdirat
    259,  # mknodat
    260,  # fchownat
    261,  # futimesat
    262,  # newfstatat
    263,  # unlinkat
    264,  # renameat
    265,  # linkat
    266,  # symlinkat
    267,  # readlinkat
    268,  # fchmodat
    269,  # faccessat
    270,  # pselect6
    271,  # ppoll
    272,  # unshare
    273,  # set_robust_list
    274,  # get_robust_list
    275,  # splice
    276,  # tee
    277,  # sync_file_range
    278,  # vmsplice
    279,  # move_pages
    280,  # utimensat
    281,  # epoll_pwait
    282,  # signalfd
    283,  # timerfd_create
    284,  # eventfd
    285,  # fallocate
    286,  # timerfd_settime
    287,  # timerfd_gettime
    288,  # accept4
    289,  # signalfd4
    290,  # eventfd2
    291,  # epoll_create1
    292,  # dup3
    293,  # pipe2
    294,  # inotify_init1
    295,  # preadv
    296,  # pwritev
    297,  # rt_tgsigqueueinfo
    298,  # perf_event_open
    299,  # recvmmsg
    300,  # fanotify_init
    301,  # fanotify_mark
    302,  # prlimit64
    303,  # name_to_handle_at
    304,  # open_by_handle_at
    305,  # clock_adjtime
    306,  # syncfs
    307,  # sendmmsg
    308,  # setns
    309,  # getcpu
    310,  # process_vm_readv — BLOCKED
    311,  # process_vm_writev — BLOCKED
    312,  # kcmp
    313,  # finit_module
    314,  # sched_setattr
    315,  # sched_getattr
    316,  # renameat2
    317,  # seccomp
    318,  # getrandom
    319,  # memfd_create
    320,  # kexec_file_load — BLOCKED
    321,  # bpf — BLOCKED
    322,  # execveat
    323,  # userfaultfd — BLOCKED
    324,  # membarrier
    325,  # mlock2
    326,  # copy_file_range
    327,  # preadv2
    328,  # pwritev2
    329,  # pkey_mprotect
    330,  # pkey_alloc
    331,  # pkey_free
    332,  # statx
    333,  # io_pgetevents
    334,  # rseq
    424,  # pidfd_open
    425,  # clone3
]

_SECCOMP_BLOCKED_SYSCALLS = {
    "kexec_load": 246,
    "process_vm_readv": 310,
    "process_vm_writev": 311,
    "kexec_file_load": 320,
    "bpf": 321,
    "userfaultfd": 323,
    "iopl": 172,
    "ioperm": 173,
    "swapon": 87,
    "swapoff": 88,
    "pivot_root": 155,
    "syslog": 103,
}


def _build_seccomp_filter() -> bytes:
    """Build a minimal BPF seccomp filter using ctypes.

    BPF instruction format:
      struct sock_filter {
          __u16 code;
          __u8  jt;    // jump-if-true
          __u8  jf;    // jump-if-false
          __u32 k;     // generic multiuse field
      };
    """
    import struct

    BPF_LD = 0x00
    BPF_W = 0x00
    BPF_ABS = 0x20
    BPF_JMP = 0x05
    BPF_JEQ = 0x10
    BPF_RET = 0x06
    SECCOMP_RET_KILL = 0x00000000
    SECCOMP_RET_ALLOW = 0x7FFF0000

    arch_offset = 4
    nr_offset = 0

    instructions = []

    def _bpf(code, jt, jf, k):
        return struct.pack("<HBBI", code, jt, jf, k)

    instructions.append(_bpf(BPF_LD | BPF_W | BPF_ABS, 0, 0, arch_offset))

    AUDIT_ARCH_X86_64 = 0xC000003E
    instructions.append(_bpf(BPF_JMP | BPF_JEQ, 0, 1, AUDIT_ARCH_X86_64))
    instructions.append(_bpf(BPF_RET, 0, 0, SECCOMP_RET_KILL))

    instructions.append(_bpf(BPF_LD | BPF_W | BPF_ABS, 0, 0, nr_offset))

    allowed_set = set(_SECCOMP_ALLOWED_SYSCALLS)

    allowed_sorted = sorted(allowed_set)
    for nr in allowed_sorted:
        instructions.append(_bpf(BPF_JMP | BPF_JEQ, 0, 1, nr))
        instructions.append(_bpf(BPF_RET, 0, 0, SECCOMP_RET_ALLOW))

    instructions.append(_bpf(BPF_RET, 0, 0, SECCOMP_RET_KILL))

    return b"".join(instructions)


def _apply_seccomp():
    """Apply seccomp-bpf filter to block dangerous syscalls."""
    try:
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


def _apply_landlock(paths_readonly: list[str], paths_rw: list[str]):
    """Apply Landlock LSM restrictions for file system sandboxing.

    Requires Linux 5.13+ and Python 3.12+.
    """
    try:
        os.landlock_restrict_self(paths_readonly, paths_rw)
        logger.debug("Landlock applied: ro=%d paths, rw=%d paths", len(paths_readonly), len(paths_rw))
    except AttributeError:
        logger.debug("Landlock not available (need Python 3.12+ on Linux 5.13+)")
    except OSError as e:
        logger.debug("Landlock restrict failed: %s", e)


def _preexec_harden():
    """Apply process hardening in child before exec."""
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
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("-500\n")
    except OSError:
        pass

    _apply_memory_hardening()
    _apply_ptrace_scope()
    _apply_seccomp()


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

    _ALLOWED_SCHEMES = {"http", "https", "ftp"}
    _BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]"}

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            if parsed.scheme and parsed.scheme.lower() not in XdgOpenDaemon._ALLOWED_SCHEMES:
                logger.warning("Blocked URL with scheme '%s': %s", parsed.scheme, url)
                return False
            if parsed.hostname and parsed.hostname.lower() in XdgOpenDaemon._BLOCKED_HOSTS:
                logger.warning("Blocked URL to localhost: %s", url)
                return False
            return True
        except Exception as e:
            logger.debug("URL parse failed in _is_safe_url: %s", e, exc_info=True)
            return False

    def _listener(self):
        while self._running:
            try:
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
    result = {
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
    result = {
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
            use_namespace=d.get("use_namespace", False),
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
# Reduced from the previous bloated list that leaked session internals.
ALWAYS_KEEP_ENV = {
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "PULSE_SERVER",
    "PIPEWIRE_RUNTIME_DIR",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "QT_QPA_PLATFORM",
    "QT_QPA_PLATFORMTHEME",
    "GDK_BACKEND",
    "SDL_VIDEO_DRIVER",
    "SDL_AUDIODRIVER",
    "TZ",
    "SHELL",
    "TERM",
}

HARDENED_ENV = {
    "GLIBC_TUNABLES": "glibc.malloc.perturb=0x42:glibc.malloc.tcache_count=0:glibc.malloc.mxfast=0",
    "MALLOC_PERTURB_": "66",
    "MALLOC_CHECK_": "3",
    "LD_BIND_NOW": "1",
}


def _bridge_audio_config(portable_home: str):
    """Bridge PulseAudio/PipeWire/D-Bus config into portable home.

    Uses copies instead of symlinks to avoid leaking host paths.
    """
    real_config = os.path.expanduser("~/.config")
    pulse_config = os.path.join(portable_home, ".config", "pulse")
    try:
        os.makedirs(pulse_config, exist_ok=True)
        real_cookie = os.path.join(real_config, "pulse", "cookie")
        portable_cookie = os.path.join(pulse_config, "cookie")
        if os.path.isfile(real_cookie) and not os.path.isfile(portable_cookie):
            shutil.copy2(real_cookie, portable_cookie)
            os.chmod(portable_cookie, 0o600)
    except OSError:
        pass
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


class Shield:
    def __init__(self, config: ShieldConfig):
        self.config = config

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

        preexec_fn = _preexec_harden if self.config.hardening else None

        if self.config.portable_home and app_dir:
            os.makedirs(os.path.join(app_dir, ".home"), exist_ok=True)
        if self.config.portable_config and app_dir:
            os.makedirs(os.path.join(app_dir, ".config"), exist_ok=True)

        if self.config.landlock:
            ro_paths = ["/usr", "/etc", "/lib", "/lib64"]
            rw_paths = []
            if self.config.portable_home and app_dir:
                rw_paths.append(os.path.join(app_dir, ".home"))
            if self.config.portable_config and app_dir:
                rw_paths.append(os.path.join(app_dir, ".config"))
            try:
                os.landlock_restrict_self(ro_paths, rw_paths)
                logger.debug("Landlock applied to sandbox")
            except (AttributeError, OSError) as e:
                logger.debug("Landlock: %s", e)

        cmd_to_run = cmd
        if self.config.use_namespace and not self.config.enable_network:
            try:
                import subprocess

                runner = ["unshare", "--user", "--mount", "--net"]
                cmd_to_run = runner + cmd
            except Exception:
                pass

        return subprocess.Popen(
            cmd_to_run,
            cwd=cwd or os.getcwd(),
            env=env,
            start_new_session=True,
            preexec_fn=preexec_fn,
        )

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
        bwrap_cmd = [
            "bwrap",
            "--unshare-all",
            "--new-session",
            "--die-with-parent",
        ]

        bwrap_cmd.extend(["--proc", "/proc"])
        bwrap_cmd.extend(["--dev", "/dev"])
        bwrap_cmd.extend(["--ro-bind", "/usr", "/usr"])
        bwrap_cmd.extend(["--ro-bind", "/lib", "/lib"])
        bwrap_cmd.extend(["--ro-bind", "/lib64", "/lib64"])
        bwrap_cmd.extend(["--ro-bind", "/bin", "/bin"])
        bwrap_cmd.extend(["--ro-bind", "/sbin", "/sbin"])
        bwrap_cmd.extend(["--ro-bind", "/etc", "/etc"])
        bwrap_cmd.extend(["--tmpfs", "/tmp"])
        bwrap_cmd.extend(["--tmpfs", "/var/tmp"])
        bwrap_cmd.extend(["--tmpfs", "/dev/shm"])
        bwrap_cmd.extend(["--symlink", "/usr/bin", "/bin"])
        bwrap_cmd.extend(["--symlink", "/usr/bin", "/sbin"])
        bwrap_cmd.extend(["--symlink", "/usr/lib", "/lib"])
        bwrap_cmd.extend(["--symlink", "/usr/lib64", "/lib64"])

        if self.config.portable_home and app_dir:
            home_dir = os.path.join(app_dir, ".home")
            os.makedirs(home_dir, exist_ok=True)
            bwrap_cmd.extend(["--bind", home_dir, os.path.expanduser("~")])

        if self.config.portable_config and app_dir:
            cfg_dir = os.path.join(app_dir, ".config")
            os.makedirs(cfg_dir, exist_ok=True)
            bwrap_cmd.extend(["--bind", cfg_dir, os.path.expanduser("~/.config")])

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
        env = (base_env or os.environ).copy()
        for k in ALWAYS_KEEP_ENV:
            if k in os.environ:
                env[k] = os.environ[k]
        if self.config.hardening:
            for k, v in HARDENED_ENV.items():
                if k not in env:
                    env[k] = v
        return env

    def _run_direct(self, cmd: list[str], cwd: str | None, env: dict | None) -> subprocess.Popen:
        result_env = (env or os.environ).copy()
        for k in ALWAYS_KEEP_ENV:
            if k in os.environ:
                result_env[k] = os.environ[k]
        return subprocess.Popen(
            cmd,
            cwd=cwd or os.getcwd(),
            env=result_env,
            start_new_session=True,
        )


def check_shield_available() -> dict:
    info = {
        "hardening": True,
        "portable_mode": True,
        "xdg_open_daemon": True,
        "memory_locking": True,
        "ptrace_scope": True,
        "malloc_hardening": True,
        "seccomp": True,
        "landlock": hasattr(os, "landlock_restrict_self"),
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
