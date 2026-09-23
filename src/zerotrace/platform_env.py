"""Detect which OS/WSL environment a hook is running in, and the filesystem class of the
repo it is protecting. One function, used by installer.py, doctor.py and the hook shim's
`zerotrace doctor`, so the three never disagree about where they are.
"""
import os
import re
import sys
from dataclasses import dataclass

_MICROSOFT_RE = re.compile(r"microsoft", re.IGNORECASE)


@dataclass(frozen=True)
class Environment:
    kind: str                    # "windows" | "wsl" | "linux" | "macos"
    wsl_version: "int | None"    # 1 | 2 | None (None off WSL)
    distro_name: "str | None"    # $WSL_DISTRO_NAME, or None
    interop_available: bool      # a WSL process can currently exec into Windows


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    if os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
        return True
    return bool(_MICROSOFT_RE.search(_read("/proc/version")))


def _wsl_version() -> "int | None":
    if not _is_wsl():
        return None
    release = _read("/proc/sys/kernel/osrelease")
    if "WSL2" in release or "microsoft-standard" in release.lower():
        return 2
    return 1


def detect() -> Environment:
    if sys.platform == "win32":
        return Environment("windows", None, None, False)
    if sys.platform == "darwin":
        return Environment("macos", None, None, False)
    if _is_wsl():
        return Environment(
            "wsl",
            _wsl_version(),
            os.environ.get("WSL_DISTRO_NAME"),
            bool(os.environ.get("WSL_INTEROP")),
        )
    return Environment("linux", None, None, False)


def classify_path(path: str) -> str:
    """"native" | "drvfs" (a Windows drive mounted into WSL, e.g. /mnt/c/...) |
    "wsl_unc" (a WSL filesystem reached from Windows via \\\\wsl$\\ or \\\\wsl.localhost\\).

    Pure string classification (no os.path.abspath): that function's platform-specific
    handling of the "other" OS's path syntax would misclassify these on the wrong host.
    """
    normalized = str(path).replace("\\", "/")
    lowered = normalized.lower()
    if lowered.startswith("//wsl$/") or lowered.startswith("//wsl.localhost/"):
        return "wsl_unc"
    if re.match(r"^/mnt/[a-z]/", normalized, re.IGNORECASE):
        return "drvfs"
    return "native"
