"""One command installs it, one command removes it - proven on a machine in a box.

Each test runs the real `install.sh` into a sandboxed HOME and then asks the questions a
person would: is it there, does it stop a secret, is it gone afterwards, and did it leave
anything of mine behind?
"""
import pytest

from .conftest import ROOT, flat, posix_only, requires_e2e

pytestmark = [requires_e2e, posix_only]


def test_a_fresh_install_protects_the_machine_and_proves_it(machine):
    done = machine.install("--local", "--no-model")
    printed = flat(done.stdout)

    assert machine.launcher.exists()
    assert (machine.bin_dir / "zerotrace-uninstall").exists()
    assert (machine.zerotrace_home / "venv").is_dir()
    # The install is only finished when a commit carrying a secret is actually refused.
    assert "a staged credential was refused by the pre-commit hook" in printed
    assert "is protecting every repo on this machine" in printed
    assert machine.hooks_path() == machine.zerotrace_home / "hooks"


def test_the_installed_launcher_runs_without_anything_else_on_path(machine):
    machine.install("--local", "--no-model")
    version = machine.zerotrace("version")
    assert version.returncode == 0
    assert version.stdout.startswith("zerotrace ")
    doctor = machine.zerotrace("doctor")
    assert "this repo protected" in flat(doctor.stdout) or "repo" in doctor.stdout


def test_a_second_install_replaces_the_first_without_stacking_path_lines(machine):
    machine.install("--local", "--no-model")
    first = machine.marker_lines()
    again = machine.install("--local", "--no-model")
    assert "replacing the existing environment" in flat(again.stdout)
    assert machine.marker_lines() == first, "re-installing must not append PATH lines again"
    assert machine.hooks_path() == machine.zerotrace_home / "hooks"


def test_uninstall_removes_what_it_installed_and_nothing_else(machine):
    machine.install("--local", "--no-model")
    # ~/.profile, not ~/.zshrc: the installer writes a zsh line only where zsh exists, and the
    # Linux runner has none - a test must not depend on which shells a machine happens to have.
    mine = machine.home / ".profile"
    mine.write_text(mine.read_text(encoding="utf-8") +
                    '\n# added by some other tool\nexport PATH="/opt/other:$PATH"\n',
                    encoding="utf-8")

    done = machine.run(str(machine.bin_dir / "zerotrace-uninstall"))
    assert done.returncode == 0, done.stdout + done.stderr

    assert not machine.zerotrace_home.exists()
    assert not machine.launcher.exists()
    assert machine.hooks_path() is None
    assert machine.marker_lines() == 0
    left = mine.read_text(encoding="utf-8")
    assert "# added by some other tool" in left, "another tool's PATH line was removed"
    assert "/opt/other" in left


def test_uninstalling_twice_is_not_an_error(machine):
    machine.install("--local", "--no-model")
    machine.run(str(machine.bin_dir / "zerotrace-uninstall"))
    done = machine.run("bash", str(ROOT / "install.sh"), "--uninstall")
    assert done.returncode == 0
    assert "nothing to remove" in flat(done.stdout)


def test_uninstalling_a_machine_that_never_had_it_says_so(machine):
    done = machine.run("bash", str(ROOT / "install.sh"), "--uninstall")
    assert done.returncode == 0
    assert "nothing to remove" in flat(done.stdout)
    assert machine.marker_lines() == 0


def test_installing_from_release_assets_verifies_them_first(machine, release_assets):
    done = machine.install("--from", str(release_assets), "--no-model")
    assert "against SHA256SUMS" in flat(done.stdout)
    assert machine.zerotrace("version").returncode == 0


def test_a_tampered_wheel_is_refused_and_nothing_is_installed(machine, release_assets, tmp_path):
    import shutil
    bad = tmp_path / "tampered"
    shutil.copytree(release_assets, bad)
    wheel = next(bad.glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")

    done = machine.install("--from", str(bad), "--no-model", expect=1)
    assert "does not match its SHA256SUMS entry" in flat(done.stdout + done.stderr)
    assert not (machine.zerotrace_home / "venv").exists()
    assert machine.hooks_path() is None


def test_a_failed_install_puts_the_previous_one_back(machine, release_assets, tmp_path):
    """The worst outcome is hooks pointing at an interpreter that no longer exists: every
    commit on the machine then fails closed, and the person has no tool left to fix it with."""
    import shutil
    machine.install("--local", "--no-model")
    before = (machine.zerotrace_home / "venv" / "bin" / "python").resolve()

    bad = tmp_path / "broken"
    shutil.copytree(release_assets, bad)
    requirements = bad / "requirements-install.txt"
    requirements.write_text("this-package-does-not-exist==9.9.9 --hash=sha256:" + "0" * 64,
                            encoding="utf-8")
    digest = __import__("hashlib").sha256(requirements.read_bytes()).hexdigest()
    sums = [line for line in (bad / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
            if "requirements-install.txt" not in line]
    sums.append(f"{digest}  requirements-install.txt")
    (bad / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")

    done = machine.install("--from", str(bad), "--no-model", expect=1)
    assert "could not install the dependencies" in flat(done.stdout + done.stderr)
    assert "put the previous install back" in flat(done.stdout)
    assert before.exists(), "the working environment was destroyed by a failed re-install"
    assert machine.zerotrace("version").returncode == 0


def test_the_help_works_when_the_script_is_not_on_disk(machine):
    """`curl … | bash -s -- --help` has no file to read its own usage out of."""
    import shlex
    done = machine.run("bash", "-c",
                       f"cat {shlex.quote(str(ROOT / 'install.sh'))} | bash -s -- --help")
    assert done.returncode == 0
    assert "--uninstall" in done.stdout
    assert "--no-model" in done.stdout


@pytest.mark.parametrize("flag", ["--nope", "--verison"])
def test_an_unknown_option_is_refused_before_anything_is_touched(machine, flag):
    done = machine.install(flag, expect=2)
    assert "unknown option" in done.stderr
    assert not machine.zerotrace_home.exists()
