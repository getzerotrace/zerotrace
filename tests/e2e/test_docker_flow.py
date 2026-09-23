"""What the installer tells a person about Docker, in each state a real machine can be in.

The daemon is played by the fake `docker` from the parent conftest, so every one of these
states is reproducible on a laptop, on a CI runner with no Docker, and on one where Docker is
Windows-only. The point of each test is the SENTENCE the person reads, because that sentence
is the whole feature: a guardrail that installs fine and silently has no model is worse than
one that says so.
"""
import json
import os
import sys


from .conftest import flat, posix_only, requires_e2e

pytestmark = [requires_e2e, posix_only]


def _with_fake_docker(machine, tmp_path, **state) -> None:
    """Put a fake `docker` first on this machine's PATH, in the state the test wants."""
    from ..conftest import _FAKE_DOCKER

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    implementation = bin_dir / "fake_docker_impl.py"
    implementation.write_text(_FAKE_DOCKER, encoding="utf-8")
    state_file = tmp_path / "fake-docker-state.json"
    state_file.write_text(json.dumps({"daemon": "running", "image": False,
                                      "container": "gone", **state}), encoding="utf-8")
    launcher = bin_dir / "docker"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{implementation}" "$@"\n',
                        encoding="utf-8")
    launcher.chmod(0o755)
    machine.env["PATH"] = str(bin_dir) + os.pathsep + machine.env["PATH"]
    machine.env["FAKE_DOCKER_STATE"] = str(state_file)
    machine.env["FAKE_DOCKER_LOG"] = str(tmp_path / "fake-docker.log")


def _without_docker(machine, tmp_path) -> None:
    """A PATH with everything on it except docker.

    Dropping the PATH entries that contain a docker binary is the obvious approach and the
    wrong one: on a Linux runner `docker` sits in /usr/bin next to `git`, so "no Docker"
    silently becomes "no git". This links every executable into one directory instead, and
    leaves exactly one of them out.
    """
    farm = tmp_path / "path-without-docker"
    farm.mkdir(exist_ok=True)
    for directory in machine.env["PATH"].split(os.pathsep):
        if not directory or not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name.lower().split(".")[0] == "docker":
                continue
            link = farm / name
            if not link.exists():
                try:
                    link.symlink_to(os.path.join(directory, name))
                except OSError:          # a name we cannot link is a name we can do without
                    continue
    machine.env["PATH"] = str(farm)


def test_no_docker_still_installs_a_working_guardrail(machine, tmp_path):
    _without_docker(machine, tmp_path)
    done = machine.install("--local")
    printed = flat(done.stdout)
    assert "Docker is not installed" in printed
    assert "install Docker" in printed, "say what to do about it"
    assert "a staged credential was refused by the pre-commit hook" in printed
    assert "is protecting every repo on this machine" in printed
    assert machine.git_global("core.hooksPath") != ""


def test_docker_installed_but_stopped_says_start_it_and_how(machine, tmp_path):
    _with_fake_docker(machine, tmp_path, daemon="stopped")
    done = machine.install("--local")
    printed = flat(done.stdout)
    assert "Docker is installed, but the daemon is not running" in printed
    assert "zerotrace model up" in printed
    assert "is protecting every repo on this machine" in printed, \
        "a stopped daemon must never fail an install"


def test_a_user_outside_the_docker_group_is_told_the_command_to_fix_it(machine, tmp_path):
    _with_fake_docker(machine, tmp_path, daemon="denied")
    done = machine.install("--local")
    printed = flat(done.stdout)
    assert "cannot talk to the daemon" in printed
    assert "usermod -aG docker" in printed


def test_a_running_daemon_pulls_the_image_and_reports_the_model(machine, tmp_path):
    """The model server never answers here, so this is also the "it started but the model is
    not there yet" path - which must warn, not fail."""
    _with_fake_docker(machine, tmp_path, daemon="running")
    done = machine.install("--local")
    printed = flat(done.stdout)
    calls = (tmp_path / "fake-docker.log").read_text(encoding="utf-8")
    assert "compose" in calls and "up" in calls, "a running daemon should have been used"
    assert "is protecting every repo on this machine" in printed
    assert "MEDIUM findings will WARN" in printed or "did not answer" in printed


def test_no_model_never_touches_docker(machine, tmp_path):
    _with_fake_docker(machine, tmp_path, daemon="running")
    machine.install("--local", "--no-model")
    calls = (tmp_path / "fake-docker.log").read_text(encoding="utf-8").splitlines()
    assert not [line for line in calls if "up" in line.split()]
    assert not [line for line in calls if "pull" in line]


def test_model_status_after_an_install_explains_the_same_thing(machine, tmp_path):
    _with_fake_docker(machine, tmp_path, daemon="stopped")
    machine.install("--local", "--no-model")
    status = machine.zerotrace("model", "status")
    printed = flat(status.stdout)
    assert status.returncode == 1, "not usable is an exit code a script can act on"
    assert "daemon is not running" in printed
    # How you start a daemon differs per OS (Docker Desktop, the Start menu, systemctl); the
    # command that finishes the job is the same everywhere.
    assert "zerotrace model up" in printed


def test_uninstall_stops_the_container_but_keeps_the_gigabytes(machine, tmp_path):
    _with_fake_docker(machine, tmp_path, daemon="running", image=True, container="up")
    machine.install("--local", "--no-model")
    done = machine.run(str(machine.bin_dir / "zerotrace-uninstall"))
    assert done.returncode == 0
    state = json.loads((tmp_path / "fake-docker-state.json").read_text(encoding="utf-8"))
    assert state["container"] == "gone", "the container was left running after an uninstall"
    assert state["image"] is True, "the image is a cache worth gigabytes; --purge removes it"
    assert "--purge" in flat(done.stdout)


def test_purge_removes_the_image_too(machine, tmp_path):
    _with_fake_docker(machine, tmp_path, daemon="running", image=True, container="up")
    machine.install("--local", "--no-model")
    done = machine.run(str(machine.bin_dir / "zerotrace-uninstall"), "--purge")
    assert done.returncode == 0
    state = json.loads((tmp_path / "fake-docker-state.json").read_text(encoding="utf-8"))
    assert state["image"] is False
