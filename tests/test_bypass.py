"""`git commit --no-verify` skips the pre-commit hook but not post-commit: a commit whose tree
was never scanned is scanned then, and the developer hears about it before the push refuses it."""
import json
import os
import time

import pytest

from zerotrace import bypass, cli

from .conftest import Fake, git, write


def run(*argv) -> int:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(list(argv))
    return exit_info.value.code


def _commit_without_the_hook(path: str, text: str, message: str = "c") -> str:
    write(path, text)
    git("add", "-A")
    git("commit", "-qm", message, "--no-verify")
    return git("rev-parse", "HEAD").stdout.strip()


def _stamp(tree: str) -> str:
    return os.path.join(".git", "zerotrace", "verified", tree)


def _boom(*_args, **_kwargs):
    raise AssertionError("scanned when it should not have been")


# --- the stamp ----------------------------------------------------------------------------

def test_the_pre_commit_scan_stamps_the_tree_it_cleared(repo):
    write("ok.py", "x = 1\n")
    git("add", "-A")
    tree = git("write-tree").stdout.strip()
    assert run("run") == 0
    assert os.path.exists(_stamp(tree))


def test_a_blocked_scan_leaves_no_stamp(repo, fake):
    write("pay.py", f'KEY = "{fake.stripe_live()}"\n')
    git("add", "-A")
    tree = git("write-tree").stdout.strip()
    assert run("run") == 1
    assert not os.path.exists(_stamp(tree))


def test_a_disabled_zerotrace_stamps_instead_of_scanning(repo):
    write(".zerotrace.yml", "version: 1\nenabled: false\n")
    write("ok.py", "x = 1\n")
    git("add", "-A")
    tree = git("write-tree").stdout.strip()
    assert run("run") == 0
    assert os.path.exists(_stamp(tree))


def test_old_stamps_expire(repo):
    folder = os.path.join(".git", "zerotrace", "verified")
    os.makedirs(folder)
    old = os.path.join(folder, "a" * 40)
    open(old, "w").close()
    long_ago = time.time() - 8 * 24 * 3600
    os.utime(old, (long_ago, long_ago))
    write("ok.py", "x = 1\n")
    git("add", "-A")
    bypass.mark_verified()
    assert not os.path.exists(old)


def test_a_failing_stamp_never_breaks_the_hook(repo, monkeypatch):
    def unwritable(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("zerotrace.gitutil.git", unwritable)
    bypass.mark_verified()                      # a missing stamp only costs one extra scan


# --- which commit gets a second look --------------------------------------------------------

def test_a_commit_that_skipped_the_scan_is_picked_out(repo):
    sha = _commit_without_the_hook("ok.py", "x = 1\n")
    assert bypass.commit_to_check() == sha


def test_a_commit_made_from_a_scanned_tree_needs_no_second_look(repo):
    write("ok.py", "x = 1\n")
    git("add", "-A")
    assert run("run") == 0
    git("commit", "-qm", "c", "--no-verify")    # this fixture has no hooks: the stamp is what counts
    assert bypass.commit_to_check() is None


def test_a_merge_commit_is_left_alone(repo):
    _commit_without_the_hook("a.py", "a = 1\n", "base")
    git("checkout", "-qb", "feature")
    _commit_without_the_hook("b.py", "b = 1\n", "feature work")
    git("checkout", "-q", "main")
    _commit_without_the_hook("c.py", "c = 1\n", "main work")
    assert git("merge", "--no-ff", "-qm", "merge", "feature").returncode == 0
    assert bypass.commit_to_check() is None


def test_commits_replayed_by_a_rebase_are_left_alone(repo):
    _commit_without_the_hook("a.py", "a = 1\n")
    os.makedirs(os.path.join(".git", "rebase-merge"))
    assert bypass.commit_to_check() is None


def test_the_undo_command_matches_the_history(repo):
    _commit_without_the_hook("a.py", "a = 1\n")
    assert bypass.undo_command() == "git update-ref -d HEAD"    # a root commit has no parent
    _commit_without_the_hook("b.py", "b = 1\n")
    assert bypass.undo_command() == "git reset --soft HEAD~1"


# --- `zerotrace post-commit` -----------------------------------------------------------------

def test_post_commit_warns_about_a_secret_that_skipped_the_hook(repo, capsys):
    value = Fake.github()
    _commit_without_the_hook("base.py", "x = 1\n", "base")
    sha = _commit_without_the_hook("gh.py", f'TOKEN = "{value}"\n', "sneaky")
    assert run("post-commit") == 1              # git ignores the status: it only says "found something"
    out = capsys.readouterr().out
    assert "skipped the pre-commit scan" in out
    assert "gh.py:1" in out
    assert "github-token" in out
    assert "git reset --soft HEAD~1" in out
    assert value not in out
    with open(os.path.join(".git", "zerotrace", "audit.log.jsonl"), encoding="utf-8") as log:
        events = [json.loads(line)["event"] for line in log]
    assert events[-1]["stage"] == "post-commit"
    assert events[-1]["commit"] == sha


def test_post_commit_says_nothing_about_a_clean_commit(repo, capsys):
    _commit_without_the_hook("ok.py", "x = 1\n")
    assert run("post-commit") == 0
    assert capsys.readouterr().out == ""


def test_post_commit_does_not_scan_a_commit_the_hook_already_cleared(repo, monkeypatch):
    write("ok.py", "x = 1\n")
    git("add", "-A")
    assert run("run") == 0
    git("commit", "-qm", "c")
    monkeypatch.setattr("zerotrace.pipeline.scan", _boom)
    assert run("post-commit") == 0


def test_post_commit_does_nothing_when_zerotrace_is_disabled(repo, monkeypatch, capsys):
    write(".zerotrace.yml", "version: 1\nenabled: false\n")
    _commit_without_the_hook("gh.py", f'TOKEN = "{Fake.github()}"\n')
    monkeypatch.setattr("zerotrace.pipeline.scan", _boom)
    assert run("post-commit") == 0
    assert capsys.readouterr().out == ""


def test_a_failure_in_post_commit_says_it_could_not_check(repo, monkeypatch, capsys):
    _commit_without_the_hook("ok.py", "x = 1\n")
    monkeypatch.setattr("zerotrace.pipeline.scan", _boom)
    assert run("post-commit") == 1
    err = capsys.readouterr().err
    assert "could not check this commit" in err
    assert "blocking" not in err


def test_post_commit_outside_a_repository_is_a_no_op(git_env, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert run("post-commit") == 0
    assert "not inside a git repository" in capsys.readouterr().err
