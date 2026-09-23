"""The same install and uninstall flows, through install.ps1 on Windows.

There is no PowerShell on the machine this was written on, so these are the tests that say
whether the Windows installer works: they run on the windows-latest job in ci.yml. They assert
the same outcomes as the POSIX flows - a working guardrail, a proven block, a clean removal -
because "it installed" means the same thing on both.
"""
from .conftest import flat, requires_e2e, windows_only

pytestmark = [requires_e2e, windows_only]


def test_a_fresh_install_protects_the_machine_and_proves_it(machine):
    done = machine.install("-Local", "-NoModel")
    printed = flat(done.stdout)

    assert machine.launcher.exists(), "cmd.exe and PowerShell resolve zerotrace.cmd, not a shim"
    assert machine.uninstaller.exists()
    assert (machine.zerotrace_home / "venv").is_dir()
    assert "a staged credential was refused by the pre-commit hook" in printed
    assert "is protecting every repo on this machine" in printed
    assert machine.hooks_path() == machine.zerotrace_home / "hooks"


def test_the_launcher_runs_from_cmd(machine):
    machine.install("-Local", "-NoModel")
    version = machine.run("cmd.exe", "/c", str(machine.launcher), "version")
    assert version.returncode == 0
    assert version.stdout.strip().startswith("zerotrace ")


def test_a_second_install_replaces_the_first(machine):
    machine.install("-Local", "-NoModel")
    again = machine.install("-Local", "-NoModel")
    assert "replacing the existing environment" in flat(again.stdout)
    assert machine.hooks_path() == machine.zerotrace_home / "hooks"


def test_uninstall_removes_the_install_and_the_path_entry(machine):
    machine.install("-Local", "-NoModel")
    done = machine.run("cmd.exe", "/c", str(machine.uninstaller))
    assert done.returncode == 0, done.stdout + done.stderr
    assert not machine.zerotrace_home.exists()
    assert machine.hooks_path() is None


def test_uninstalling_a_machine_that_never_had_it_says_so(machine):
    done = machine.install("-Uninstall")
    assert "nothing to remove" in flat(done.stdout)


def test_installing_from_release_assets_verifies_them_first(machine, release_assets):
    done = machine.install("-From", str(release_assets), "-NoModel")
    assert "against SHA256SUMS" in flat(done.stdout)
    assert machine.run("cmd.exe", "/c", str(machine.launcher), "version").returncode == 0


def test_a_tampered_wheel_is_refused(machine, release_assets, tmp_path):
    import shutil
    bad = tmp_path / "tampered"
    shutil.copytree(release_assets, bad)
    wheel = next(bad.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")

    done = machine.install("-From", str(bad), "-NoModel", expect=1)
    assert "does not match its SHA256SUMS entry" in flat(done.stdout + done.stderr)
    assert not (machine.zerotrace_home / "venv").exists()
