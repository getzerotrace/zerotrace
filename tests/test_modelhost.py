"""Docker detection and the model container, through a fake `docker` on PATH.

Every state a real machine can be in is a line in a JSON file here (see `fake_docker` in
conftest), because the states that matter most - a stopped daemon, a user who is not in the
docker group - are the ones nobody remembers to try by hand.
"""
import json

import pytest

from zerotrace import modelhost
from zerotrace.classifier import llm
from zerotrace.config import load_config


@pytest.fixture
def cfg(git_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return load_config()


def _status(cfg, **_kw):
    llm.forget_digest()
    return modelhost.probe(cfg)


# --- is Docker there, and may we use it? --------------------------------------------------

def test_no_docker_at_all_is_reported_with_the_way_to_get_it(cfg, no_docker):
    status = _status(cfg)
    assert status.docker == modelhost.ABSENT
    assert status.headline == "Docker is not installed"
    assert "install Docker" in status.hint
    assert not status.usable


def test_docker_installed_but_the_daemon_is_down(cfg, fake_docker):
    fake_docker.set(daemon="stopped")
    status = _status(cfg)
    assert status.docker == modelhost.STOPPED
    assert status.headline == "Docker is installed, but the daemon is not running"
    assert "zerotrace model up" in status.hint
    # Docker's own sentence says the same thing; repeating it under ours is noise.
    assert status.detail == ""


def test_a_user_who_may_not_talk_to_the_daemon_is_told_how_to_fix_it(cfg, fake_docker):
    fake_docker.set(daemon="denied")
    status = _status(cfg)
    assert status.docker == modelhost.DENIED
    assert "usermod -aG docker" in status.hint
    assert "root-equivalent" in status.hint, "the group is worth a word of warning"


def test_a_stopped_desktop_socket_is_not_read_as_a_permission_problem():
    """macOS says "dial unix …docker.sock: connect: no such file or directory" when Docker
    Desktop is merely closed. That was reported as a permissions problem, which sent people
    off to edit groups on a machine that only needed Docker started."""
    state, _ = modelhost._classify(
        "failed to connect to the docker API at unix:///Users/x/.docker/run/docker.sock; "
        "dial unix /Users/x/.docker/run/docker.sock: connect: no such file or directory")
    assert state == modelhost.STOPPED


def test_an_unrecognised_failure_keeps_the_daemons_own_words(cfg, fake_docker):
    fake_docker.set(daemon="weird")
    status = _status(cfg)
    assert status.docker == modelhost.STOPPED
    assert "something nobody has seen before" in status.detail


def test_a_daemon_that_never_answers_times_out_instead_of_hanging(cfg, fake_docker, monkeypatch):
    monkeypatch.setattr(modelhost, "_INFO_TIMEOUT", 0.5)
    fake_docker.set(daemon="slow", sleep=30)
    status = _status(cfg)
    assert status.docker == modelhost.UNRESPONSIVE
    assert "start" in status.hint


# --- the image and the container ----------------------------------------------------------

def test_a_running_daemon_without_the_image_says_what_will_be_downloaded(cfg, fake_docker):
    status = _status(cfg)
    assert status.docker == modelhost.RUNNING
    assert status.image is False
    assert status.container == modelhost.GONE
    assert "zerotrace model up" in status.hint
    assert "ollama/ollama" in status.hint


def test_the_container_states_are_told_apart(cfg, fake_docker):
    for state, expected in (("up", modelhost.UP), ("starting", modelhost.STARTING),
                            ("unhealthy", modelhost.UNHEALTHY), ("created", modelhost.CREATED),
                            ("gone", modelhost.GONE)):
        fake_docker.set(container=state, image=True)
        assert _status(cfg).container == expected


def test_an_unhealthy_container_is_a_warning_with_the_command_that_shows_why(cfg, fake_docker):
    fake_docker.set(container="unhealthy", image=True)
    rows = dict((check, result) for _state, check, result in modelhost.rows(_status(cfg)))
    assert "docker logs zerotrace-ollama" in rows["model container"]


# --- the pin lives in one place -------------------------------------------------------------

def test_the_image_is_pinned_to_a_version_and_a_digest():
    """`latest` silently changes what classifies your secrets."""
    reference = modelhost.image_ref()
    assert reference.startswith("ollama/ollama:")
    assert "@sha256:" in reference, "pin the digest, not just the tag"
    assert ":latest@" not in reference


def test_the_compose_file_and_its_entrypoint_travel_together():
    import os
    directory = modelhost.compose_dir()
    assert os.path.exists(os.path.join(directory, "docker-compose.yml"))
    assert os.path.exists(os.path.join(directory, "ollama-entrypoint.sh")), \
        "the compose file mounts the entrypoint by a relative path"


# --- starting, stopping, pulling ------------------------------------------------------------

def test_up_refuses_to_start_anything_when_the_daemon_is_down(cfg, fake_docker):
    fake_docker.set(daemon="stopped")
    result = modelhost.up(cfg, pull_model=False)
    assert result.ok is False
    assert "daemon is not running" in result.lines[0]
    assert not [call for call in fake_docker.calls if call.startswith("compose -f")]


def test_up_starts_the_container_and_pulls_the_model(cfg, fake_docker, fake_ollama):
    cfg = load_config()                       # picks up the fake endpoint
    seen: list[str] = []
    result = modelhost.up(cfg, on_event=seen.append, wait=10)
    assert result.ok, result.lines
    assert fake_ollama.state["pulls"] == [cfg.model_name]
    assert any("compose" in call and "up" in call for call in fake_docker.calls)
    assert fake_docker.state["container"] == "up"
    assert result.status is not None and result.status.usable


def test_a_model_that_is_already_served_needs_no_pull(cfg, fake_docker, fake_ollama):
    cfg = load_config()
    fake_ollama.state["models"].append(cfg.model_name)
    fake_docker.set(image=True, container="up")
    status = _status(cfg)
    assert status.usable
    assert status.hint == ""


def test_a_failed_pull_is_reported_rather_than_claimed(cfg, fake_docker, fake_ollama):
    cfg = load_config()
    fake_ollama.state["fail"] = "no space left on device"
    result = modelhost.up(cfg, wait=10)
    assert result.ok is False
    assert "could not pull" in result.lines[-1]


def test_pull_reports_progress_so_a_slow_download_does_not_look_stuck(cfg, fake_ollama):
    cfg = load_config()
    seen: list[tuple[int, int]] = []
    assert modelhost.pull(cfg, on_progress=lambda done, total: seen.append((done, total)))
    assert seen and seen[-1] == (1000, 1000)


def test_down_keeps_the_image_and_the_model_unless_asked(cfg, fake_docker):
    fake_docker.set(image=True, container="up")
    result = modelhost.down()
    assert result.ok
    assert fake_docker.state["image"] is True, "gigabytes are not thrown away by default"
    assert "zerotrace model down --purge" in " ".join(result.lines)

    fake_docker.set(container="up")
    purge = modelhost.down(purge=True)
    assert purge.ok
    assert fake_docker.state["image"] is False
    assert any("--rmi" in call for call in fake_docker.calls)


def test_down_on_a_machine_with_no_docker_is_not_an_error(cfg, no_docker):
    result = modelhost.down()
    assert result.ok
    assert "nothing to stop" in result.lines[0]


# --- endpoints we do not manage ---------------------------------------------------------------

def test_a_remote_endpoint_is_never_started_locally(cfg, fake_docker, monkeypatch, tmp_path):
    monkeypatch.setenv("ZEROTRACE_MODEL_ENDPOINT", "https://models.example.com")
    (tmp_path / ".zerotrace.yml").write_text("model:\n  allow_remote: true\n", encoding="utf-8")
    # Asking a remote endpoint whether it answers is a real network call, and this suite makes
    # none: the whole thing also runs under ci/no_egress.py, where any non-loopback connection
    # is an error. What is under test here is that nothing local is started, not the socket.
    monkeypatch.setattr(modelhost, "_endpoint_answers", lambda *args, **kwargs: False)
    remote = load_config()
    status = modelhost.probe(remote)
    assert status.local is False
    assert "served by https://models.example.com" in status.hint
    result = modelhost.up(remote)
    assert not [call for call in fake_docker.calls if "compose" in call]
    assert "nothing to start locally" in result.lines[0]


def test_the_status_rows_read_the_same_as_the_doctors(cfg, fake_docker):
    """`zerotrace model status` and `zerotrace doctor` must not describe one machine in two
    different vocabularies."""
    rows = modelhost.rows(_status(cfg))
    assert [state for state, _, _ in rows] == ["ok", "warn", "warn", "warn"]
    assert [check for _, check, _ in rows] == ["docker", "model image", "model container",
                                               "model"]
    assert json.dumps(rows)      # plain data: no rich markup, no objects
