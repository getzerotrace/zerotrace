"""`zerotrace setup`: the guided half of an install that both bootstrap installers hand over to.

These run against a real git installation in a sandboxed HOME, so the self-test at the end of
`setup` is the real thing: a real repository, a real staged credential, a real `git commit`
through the shims that were just written.
"""
import io

import pytest

from zerotrace import installer, modelhost, setup
from zerotrace.config import load_config


@pytest.fixture
def sandbox(git_env, tmp_path, monkeypatch):
    # A container that never answers is a real case (a wedged daemon), and waiting the real
    # three minutes for it in a unit test is not.
    monkeypatch.setenv(modelhost._WAIT_ENV, "2")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(**kwargs) -> setup.Outcome:
    return setup.run(file=io.StringIO(), **kwargs)


def _rows(outcome: setup.Outcome) -> dict[str, tuple[str, str]]:
    return {check: (state, result) for state, check, result in outcome.rows}


def test_a_machine_with_no_docker_still_gets_a_working_guardrail(sandbox, no_docker):
    outcome = _run(pull_model=False)
    assert outcome.guardrail_ok
    assert outcome.code == 0, "the model is optional; the deterministic rules are the product"
    rows = _rows(outcome)
    assert rows["docker"][0] == "warn"
    assert "not installed" in rows["docker"][1]


def test_the_install_is_proven_by_a_commit_that_is_actually_blocked(sandbox, no_docker):
    outcome = _run(pull_model=False)
    state, result = _rows(outcome)["a secret really is blocked"]
    assert state == "ok"
    assert "refused by the pre-commit hook" in result


def test_a_hooks_path_that_is_not_ours_fails_the_validation(sandbox, no_docker, monkeypatch):
    """The shims were written but something else owns core.hooksPath - the case where an
    install reports success and no commit is ever scanned."""
    monkeypatch.setattr(installer, "is_managed", lambda path: False)
    outcome = _run(pull_model=False)
    assert outcome.guardrail_ok is False
    assert outcome.code == 1
    assert _rows(outcome)["hooks registered"][0] == "fail"


def test_no_model_skips_docker_entirely_and_says_so(sandbox, fake_docker):
    outcome = _run(pull_model=False)
    assert outcome.guardrail_ok
    assert "--no-model" in _rows(outcome)["model"][1]
    # Docker is still *inspected* (the report says where the tie-break stands); nothing is
    # started and nothing is downloaded.
    assert not [call for call in fake_docker.calls if "up" in call.split()]


def test_a_stopped_daemon_is_reported_once_with_the_fix(sandbox, fake_docker):
    fake_docker.set(daemon="stopped")
    outcome = _run()
    assert outcome.guardrail_ok
    assert "daemon is not running" in _rows(outcome)["docker"][1]
    # The instruction itself is per-OS (`open -a Docker`, the Start menu, systemctl); what
    # every one of them ends with is the command that finishes the job.
    assert any("zerotrace model up" in hint for hint in outcome.next_steps)
    # The Docker row already carried the sentence; the model step must not repeat it.
    assert "model" not in _rows(outcome) or "daemon" not in _rows(outcome).get("model", ("", ""))[1]


def test_a_running_daemon_brings_the_model_up(sandbox, fake_docker, fake_ollama):
    outcome = _run()
    assert outcome.guardrail_ok
    state, result = _rows(outcome)["model"]
    assert state == "ok"
    assert load_config().model_name in result
    assert outcome.model_status is not None and outcome.model_status.usable


def test_the_summary_names_the_uninstall_command(sandbox, no_docker):
    out = io.StringIO()
    setup.run(pull_model=False, file=out)
    printed = out.getvalue()
    assert "zerotrace-uninstall" in printed
    assert "zerotrace doctor" in printed


def test_the_step_numbers_continue_the_installers_own(sandbox, no_docker):
    """`install.sh` has already done two of six steps by the time it calls this."""
    out = io.StringIO()
    setup.run(pull_model=False, step_offset=2, total_steps=6, file=out)
    assert "[3/6]" in out.getvalue()
    assert "[6/6]" in out.getvalue()


def test_running_it_twice_is_not_a_second_install(sandbox, no_docker):
    first = _run(pull_model=False)
    second = _run(pull_model=False)
    assert first.guardrail_ok and second.guardrail_ok
    hooks = installer.default_hooks_dir("global")
    assert _rows(second)["git hooks"][1].startswith(f"{len(installer.HOOK_NAMES)} shims in {hooks}")


def test_an_unreachable_model_is_a_warning_not_a_failed_install(sandbox, fake_docker):
    """Docker runs, the container starts, and nothing ever answers on the endpoint."""
    outcome = _run()
    assert outcome.guardrail_ok
    assert _rows(outcome)["model"][0] == "warn"
    assert outcome.model_status is not None
    assert outcome.model_status.docker == modelhost.RUNNING
