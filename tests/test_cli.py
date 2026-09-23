"""CLI and doctor, driven in-process so behaviour (and coverage) is measured directly."""
import io
import json
import os

import pytest

from zerotrace import cli

from .conftest import Fake, git, write


def run(*argv) -> int:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(list(argv))
    return exit_info.value.code


def test_version(capsys):
    assert run("version") == 0
    assert "zerotrace" in capsys.readouterr().out


def test_run_blocks_a_staged_secret_and_hides_the_value(repo, capsys):
    value = Fake.stripe_live()
    write("pay.py", f'KEY = "{value}"\n')
    git("add", "-A")
    assert run("run") == 1
    out = capsys.readouterr().out
    assert "stripe-live-key" in out and value not in out


def test_run_allows_a_clean_diff(repo, capsys):
    write("ok.py", "def f():\n    return 1\n")
    git("add", "-A")
    assert run("run") == 0


def test_pre_commit_framework_style_filename_arguments_are_accepted(repo):
    write("ok.py", "x = 1\n")
    git("add", "-A")
    assert run("ok.py") == 0          # argv[0] is not a command: treated as `run <files>`


def test_scan_json_reports_fingerprints_not_values(repo, capsys):
    value = Fake.github()
    write("gh.py", f'TOKEN = "{value}"\n')
    git("add", "-A")
    assert run("scan", "--staged", "--format", "json", "--no-model") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["rule"] == "github-token"
    assert payload[0]["action"] == "block"
    assert len(payload[0]["fingerprint"]) == 64
    assert value not in json.dumps(payload)


def test_scan_all_and_range(repo, capsys):
    write("ok.py", "x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base", "--no-verify")
    value = Fake.openai()
    write("bad.py", f'KEY = "{value}"\n')
    git("add", "-A")
    git("commit", "-qm", "bad", "--no-verify")
    assert run("scan", "--all", "--no-model") == 1
    assert run("scan", "--range", "HEAD~1..HEAD", "--no-model") == 1
    capsys.readouterr()


def test_pre_push_blocks_commits_that_bypassed_the_hook(repo, monkeypatch, capsys):
    write("ok.py", "x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base", "--no-verify")
    base = git("rev-parse", "HEAD").stdout.strip()
    value = Fake.github()
    write("gh.py", f'TOKEN = "{value}"\n')
    git("add", "-A")
    git("commit", "-qm", "sneaky", "--no-verify")
    head = git("rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr("sys.stdin", io.StringIO(f"refs/heads/main {head} refs/heads/main {base}\n"))
    assert run("pre-push", "origin") == 1
    out = capsys.readouterr().out
    assert "github-token" in out and value not in out


def test_init_writes_config_and_hashed_baseline(repo, capsys):
    write("app.py", "x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "c", "--no-verify")
    assert run("init") == 0
    assert (repo / ".zerotrace.yml").exists()
    baseline = json.loads((repo / ".secrets.baseline").read_text())
    assert baseline["version"] and "results" in baseline
    assert run("init") == 0                       # idempotent: does not clobber the config
    assert "exists" in capsys.readouterr().out


def test_the_baseline_records_repo_relative_paths_only(repo, fake):
    """An absolute path in a committed file carries the username and folder layout of whoever
    ran `init` - and matches nothing on anyone else's machine, which silently turns the
    baseline off."""
    write("app.py", f'KEY = "{fake.aws_key_id()}"\n')
    git("add", "-A")
    git("commit", "-qm", "c", "--no-verify")
    assert run("init") == 0
    results = json.loads((repo / ".secrets.baseline").read_text())["results"]
    assert results, "the seeded secret should be in the baseline"
    for filename in results:
        assert not os.path.isabs(filename), filename
        assert str(repo) not in filename
        assert not filename.startswith("./"), filename


def test_regenerating_the_baseline_does_not_grow_it(repo, fake):
    """The baseline is full of high-entropy hashes, so a scan that includes it records its own
    fingerprints - and does it again on the next run, and the next."""
    write("app.py", f'KEY = "{fake.aws_key_id()}"\n')
    git("add", "-A")
    git("commit", "-qm", "c", "--no-verify")
    assert run("init") == 0
    first = json.loads((repo / ".secrets.baseline").read_text())["results"]
    assert ".secrets.baseline" not in first
    assert run("init") == 0
    second = json.loads((repo / ".secrets.baseline").read_text())["results"]
    assert {name: len(hits) for name, hits in second.items()} == \
           {name: len(hits) for name, hits in first.items()}


def test_install_and_uninstall_through_the_cli(git_env, repo, capsys):
    assert run("install", "--global") == 0
    assert "core.hooksPath" in capsys.readouterr().out
    assert run("doctor") == 0
    assert "this repo protected" in capsys.readouterr().out
    assert run("uninstall", "--global") == 0
    assert "core.hooksPath" in capsys.readouterr().out


def test_doctor_reports_an_unreachable_model_without_failing(repo, capsys):
    assert run("doctor") in (0, 1)
    out = capsys.readouterr().out
    assert "model" in out and "rule pack" in out


def test_doctor_refuses_a_remote_endpoint_that_is_not_opted_in(repo, monkeypatch, capsys):
    monkeypatch.setenv("ZEROTRACE_MODEL_ENDPOINT", "https://inference.corp.example")
    assert run("doctor") == 1
    assert "allow_remote" in capsys.readouterr().out


def test_internal_errors_fail_closed(repo, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr("zerotrace.pipeline.scan", boom)
    write("app.py", "x = 1\n")
    git("add", "-A")
    assert run("run") == 1                        # never 0 on an unexpected error
    assert "blocking to stay safe" in capsys.readouterr().err


def test_outside_a_repo_the_hook_is_a_no_op(tmp_path, monkeypatch, git_env):
    monkeypatch.chdir(tmp_path)
    from zerotrace import gitutil
    gitutil._toplevel.cache_clear()
    assert run("run") == 0
    assert run("scan") == 2


@pytest.mark.parametrize("command", [("doctor", "-i"), ("exceptions", "-i")])
def test_full_screen_flags_fall_back_to_plain_output_without_a_terminal(repo, capsys, command):
    """`-i` in a pipe or CI log: say why, then print what the plain command prints."""
    assert run(*command) in (0, 1)
    captured = capsys.readouterr()
    assert "-i needs an interactive terminal" in captured.err
    assert captured.out.strip(), "the plain report is printed instead"


def test_the_exception_listing_shows_rule_and_file(repo, capsys, monkeypatch):
    from zerotrace.audit import exceptions
    # Wide enough that no cell wraps: this is about escaping, not the runner's console width
    # (the Windows runner's is a column narrower and folded the reason across two lines).
    monkeypatch.setenv("COLUMNS", "200")
    exceptions.add("f" * 64, "vendor [sample] key", 7, rule_id="stripe-live-key", path="pay.py")
    assert run("exceptions") == 0
    out = capsys.readouterr().out
    assert "stripe-live-key" in out and "pay.py" in out
    assert "vendor [sample] key" in out, "a reason is printed as typed, not read as markup"


def test_a_closed_stdin_at_the_prompt_is_an_abort_not_an_internal_error(repo, capsys, monkeypatch):
    write("pay.py", f'KEY = "{Fake.stripe_live()}"\n')
    git("add", "-A")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))       # EOF as soon as it asks
    assert run("review", "--classic") == 1
    err = capsys.readouterr().err
    assert "Nothing was committed" in err and "internal error" not in err


_BANNER = "secret & PII guardrail"      # the name panel printed with the logo


def test_a_blocked_commit_shows_the_findings_without_the_logo(repo, capsys):
    write("pay.py", f'KEY = "{Fake.stripe_live()}"\n')
    git("add", "-A")
    assert run("run") == 1
    out = capsys.readouterr().out
    assert "stripe-live-key" in out
    assert _BANNER not in out and "ZEROTRACE" not in out, "no logo or name on an alert"


def test_scan_reports_have_no_banner_either(repo, capsys):
    write("pay.py", f'KEY = "{Fake.stripe_live()}"\n')
    git("add", "-A")
    assert run("scan", "--staged", "--no-model") == 1
    assert _BANNER not in capsys.readouterr().out


def test_the_logo_appears_on_the_first_install_only(git_env, repo, capsys):
    assert run("install", "--global") == 0
    assert _BANNER in capsys.readouterr().out, "first install: logo and name"
    assert run("install", "--global") == 0
    assert _BANNER not in capsys.readouterr().out, "re-install: just the result"
    assert run("uninstall", "--global") == 0
    capsys.readouterr()
    assert run("install", "--global") == 0
    assert _BANNER in capsys.readouterr().out, "after an uninstall it is a first install again"
