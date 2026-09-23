"""Input that reaches a git command line, file permissions, and regex-anchor correctness."""
import os
import subprocess
import sys

import pytest

from zerotrace import gitutil, installer
from zerotrace.detectors import sensitive_files

from .conftest import Fake, rand, write


@pytest.mark.parametrize("rev", ["HEAD", "origin/main", "a1b2c3..d4e5f6", "HEAD~3", "v1.0.0",
                                 "refs/heads/main", "@{u}"])
def test_valid_revisions_pass(rev):
    assert gitutil.checked_rev(rev) == rev


@pytest.mark.parametrize("rev", ["--upload-pack=touch /tmp/x", "-n", "a;rm -rf /", "a b",
                                 "$(id)", "`id`", "a|b", "", "a" * 300, "a\nb"])
def test_hostile_revisions_are_refused(rev):
    with pytest.raises(gitutil.GitError):
        gitutil.checked_rev(rev)


@pytest.mark.parametrize("path", ["../../etc/passwd", "/etc/passwd", "-rf", "a/../../b", ""])
def test_hostile_paths_are_refused(path):
    with pytest.raises(gitutil.GitError):
        gitutil.checked_path(path)


def test_scan_rejects_a_hostile_range_instead_of_running_git(repo):
    write("a.py", "x = 1\n")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "c", "--no-verify"], check=True)
    result = subprocess.run(
        [sys.executable, "-m", "zerotrace", "scan", "--range=--output=/tmp/pwned"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 1
    assert "unexpected revision" in result.stderr
    assert not os.path.exists("/tmp/pwned")


def test_pre_push_ignores_lines_that_are_not_object_ids(repo):
    result = subprocess.run(
        [sys.executable, "-m", "zerotrace", "pre-push", "origin"],
        input="refs/heads/main --upload-pack=id refs/heads/main 0000000000000000000000000000000000000000\n",
        capture_output=True, text=True,
    )
    assert result.returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX permission bits")
def test_hooks_are_not_group_or_world_writable(git_env):
    installer.install("global")
    hooks = installer.default_hooks_dir("global")
    for name in ("pre-commit", "pre-push", "commit-msg"):
        mode = os.stat(os.path.join(hooks, name)).st_mode & 0o777
        assert mode == 0o700, (name, oct(mode))


def test_path_like_values_are_still_skipped_and_secrets_still_caught():
    from .test_detectors import _scan
    assert _scan("app.py", 'key_file = "/etc/ssl/private/server.key"') == []
    assert _scan("app.py", 'cert = "certs/server.pem"') == []
    findings = _scan("app.py", f'api_key = "{rand(20)}9aZ"')
    assert findings and findings[0].severity == "high"


def test_private_key_file_detection_still_anchors_correctly():
    assert sensitive_files.match("keys/app.key", "-----BEGIN " + "PRIVATE KEY-----\nabc") is not None
    assert sensitive_files.match("keys/app.key", "A" * 250) is not None      # bare base64 body
    assert sensitive_files.match("keys/app.key", "not a key") is None
    assert sensitive_files.match("src/hotkey.py", "PRIVATE KEY") is None     # not a .key file


def test_eval_values_use_the_csprng():
    """Generated values come from `secrets` alone: the `random` module is never imported."""
    import ast
    import inspect

    from zerotrace import evals
    tree = ast.parse(inspect.getsource(evals))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert "random" not in imported

    assert sorted(evals._shuffled(list("abcdef"))) == list("abcdef"), "a permutation, no loss"
    password = evals._gen("pw:16")
    assert len(password) == 16
    assert any(c.islower() for c in password) and any(c.isupper() for c in password)
    assert any(c.isdigit() for c in password) and any(c in "!@#%^*-_" for c in password)
    assert Fake.github().startswith("ghp_")


def test_writes_cannot_escape_the_repository(repo):
    from zerotrace import gitutil
    from zerotrace.remediation import applier

    outside = repo.parent / "outside.txt"
    outside.write_text("untouched\n")
    try:
        os.symlink(outside, repo / "link.txt")
    except OSError as exc:
        # Creating a symlink needs an elevated token or Developer Mode on Windows
        # (WinError 1314) - a real environment limitation, not a code issue. Skip
        # honestly rather than pretend the escape-guard was exercised.
        pytest.skip(f"cannot create symlinks in this environment: {exc}")
    write(repo / "real.txt", "x = 1\n")
    subprocess.run(["git", "add", "-A"], check=True)

    # A symlink in the index resolves outside the work tree: refuse to touch it. (git also
    # stores the link target as the blob, so the apply would fail the staleness check too.)
    with pytest.raises(gitutil.GitError):
        gitutil.resolved_in_repo("link.txt")
    with pytest.raises((gitutil.GitError, applier.StaleIndexError)):
        applier.apply("link.txt", 1, "y = 2", "x = 1")
    for escape in ("../outside.txt", "/etc/passwd", "-rf"):
        with pytest.raises(gitutil.GitError):
            gitutil.resolved_in_repo(escape)
    assert outside.read_text() == "untouched\n"


def test_install_refuses_a_directory_that_holds_other_files(git_env, tmp_path):
    target = tmp_path / "not-a-hooks-dir"
    target.mkdir()
    (target / "id_rsa").write_text("important\n")
    with pytest.raises(PermissionError, match="id_rsa"):
        installer.install("global", str(target))
    assert (target / "id_rsa").read_text() == "important\n"
    assert not (target / "pre-commit").exists()


def test_eval_rejects_a_missing_cases_file():
    from zerotrace import evals
    with pytest.raises(SystemExit):
        evals.load_cases("/nonexistent/cases.jsonl")


@pytest.mark.parametrize("value", ["-x", "--upload-pack=touch pwned", "relative/hooks",
                                   "/hooks\nrm -rf ~", "/hooks\x00"])
def test_hook_scripts_refuse_anything_but_a_plain_absolute_path(value):
    """Quoting stops shell injection but not an option-like value (`exec -x`); a path written
    into a hook script must be absolute and on one line."""
    with pytest.raises(ValueError):
        installer._sh_quote(value)


@pytest.mark.parametrize("value,quoted", [
    ("/usr/bin/python3", "/usr/bin/python3"),
    ("/Users/a b/.venv/bin/python", "'/Users/a b/.venv/bin/python'"),
    ("C:\\Program Files\\Python312\\python.exe", "'C:/Program Files/Python312/python.exe'"),
    ("\\\\fileserver\\share\\hooks", "//fileserver/share/hooks"),
    ("", "''"),
])
def test_hook_script_paths_are_quoted_for_sh(value, quoted):
    assert installer._sh_quote(value) == quoted
