"""Environment detection: pure unit tests, no real WSL needed (monkeypatched env/files)."""
import sys

from zerotrace import platform_env


def test_detects_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    env = platform_env.detect()
    assert env.kind == "windows"
    assert env.wsl_version is None


def test_detects_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    env = platform_env.detect()
    assert env.kind == "macos"


def test_detects_plain_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr(platform_env.os.path, "exists", lambda p: False)
    monkeypatch.setattr(platform_env, "_read", lambda p: "")
    env = platform_env.detect()
    assert env.kind == "linux"
    assert env.wsl_version is None


def test_detects_wsl2_via_env_var(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/1_interop")
    monkeypatch.setattr(platform_env, "_read",
                        lambda p: "6.6.0-microsoft-standard-WSL2\n" if "osrelease" in p else "")
    env = platform_env.detect()
    assert env.kind == "wsl"
    assert env.wsl_version == 2
    assert env.distro_name == "Ubuntu"
    assert env.interop_available is True


def test_detects_wsl1_without_interop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Debian")
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr(platform_env, "_read", lambda p: "4.4.0-19041-Microsoft\n")
    env = platform_env.detect()
    assert env.kind == "wsl"
    assert env.wsl_version == 1
    assert env.interop_available is False


def test_detects_wsl_via_binfmt_when_no_env_var(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr(platform_env.os.path, "exists",
                        lambda p: p == "/proc/sys/fs/binfmt_misc/WSLInterop")
    monkeypatch.setattr(platform_env, "_read", lambda p: "")
    env = platform_env.detect()
    assert env.kind == "wsl"
    assert env.distro_name is None


def test_classify_path_drvfs():
    assert platform_env.classify_path("/mnt/c/Users/me/repo") == "drvfs"


def test_classify_path_wsl_unc():
    assert platform_env.classify_path(r"\\wsl$\Ubuntu\home\me\repo") == "wsl_unc"
    assert platform_env.classify_path(r"\\wsl.localhost\Ubuntu\home\me\repo") == "wsl_unc"


def test_classify_path_native():
    assert platform_env.classify_path("/home/me/repo") == "native"
