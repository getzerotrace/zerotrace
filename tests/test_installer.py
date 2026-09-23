"""Global install: every repo protected, existing hooks keep working, uninstall restores."""
import os
import subprocess

import pytest

from zerotrace import installer

from .conftest import git, write

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX hook scripts")


def _new_repo(tmp_path, name):
    path = tmp_path / name
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _commit(path, *args):
    return subprocess.run(["git", "-C", str(path), "commit", "-qm", "c", *args],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def test_global_install_protects_every_repo(git_env, tmp_path, fake):
    installer.install("global")
    assert installer.is_managed(git("config", "--global", "core.hooksPath").stdout.strip())
    for name in ("api", "web"):
        repo = _new_repo(tmp_path, name)
        write(repo / "app.py", f'KEY = "{fake.stripe_live()}"\n')
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        result = _commit(repo)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "stripe-live-key" in result.stdout + result.stderr


def test_clean_commit_passes(git_env, tmp_path):
    installer.install("global")
    repo = _new_repo(tmp_path, "clean")
    write(repo / "ok.py", "def f():\n    return 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert _commit(repo).returncode == 0


def test_repo_local_hooks_still_run(git_env, tmp_path):
    installer.install("global")
    repo = _new_repo(tmp_path, "lfs")
    marker = tmp_path / "local-hook-ran"
    for name in ("pre-commit", "post-commit"):
        hook = repo / ".git" / "hooks" / name
        write(hook, f"#!/bin/sh\necho {name} >> '{marker}'\n")
        os.chmod(hook, 0o755)
    write(repo / "ok.py", "x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert _commit(repo).returncode == 0
    assert marker.read_text().split() == ["pre-commit", "post-commit"]


def test_failing_local_hook_still_blocks(git_env, tmp_path):
    installer.install("global")
    repo = _new_repo(tmp_path, "lint")
    hook = repo / ".git" / "hooks" / "pre-commit"
    write(hook, "#!/bin/sh\necho lint failed; exit 3\n")
    os.chmod(hook, 0o755)
    write(repo / "ok.py", "x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert _commit(repo).returncode != 0


def test_previous_global_hooks_are_chained_and_restored(git_env, tmp_path):
    prev = tmp_path / "company-hooks"
    marker = tmp_path / "company-hook-ran"
    write(prev / "commit-msg", f"#!/bin/sh\necho yes > '{marker}'\n")
    os.chmod(prev / "commit-msg", 0o755)
    git("config", "--global", "core.hooksPath", str(prev))

    installer.install("global")
    repo = _new_repo(tmp_path, "chained")
    write(repo / "ok.py", "x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert _commit(repo).returncode == 0
    assert marker.exists()

    installer.uninstall("global")
    assert git("config", "--global", "core.hooksPath").stdout.strip() == str(prev)


def test_uninstall_unsets_when_nothing_before(git_env):
    installer.install("global")
    installer.uninstall("global")
    assert git("config", "--global", "core.hooksPath").returncode != 0


def test_install_twice_is_idempotent(git_env):
    installer.install("global")
    lines = installer.install("global")  # re-running against an already-managed dir must not error
    assert any("wrote" in line for line in lines)


def test_husky_style_local_hookspath_gets_repo_install(git_env, tmp_path, monkeypatch, fake):
    installer.install("global")
    repo = _new_repo(tmp_path, "husky")
    husky = repo / ".husky"
    write(husky / "pre-commit", "#!/bin/sh\necho husky lint ok\n")
    os.chmod(husky / "pre-commit", 0o755)
    write(husky / "_" / "pre-commit", '#!/bin/sh\nsh "$(dirname "$0")/../pre-commit"\n')
    os.chmod(husky / "_" / "pre-commit", 0o755)
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", ".husky/_"], check=True)
    monkeypatch.chdir(repo)
    from zerotrace import gitutil
    gitutil._toplevel.cache_clear()

    write(repo / "app.py", f'KEY = "{fake.stripe_live()}"\n')
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert _commit(repo).returncode == 0  # husky overrides the global hook: unprotected

    subprocess.run(["git", "-C", str(repo), "update-ref", "-d", "HEAD"], check=True)
    (line,) = installer.install_repo()
    assert ".husky" in line
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert _commit(repo).returncode != 0


def test_install_sets_template_dir_as_a_fallback(git_env, tmp_path, fake):
    installer.install("global")
    template = git("config", "--global", "init.templateDir").stdout.strip()
    assert installer.is_managed(os.path.join(template, "hooks"))

    # Simulate the scenario the template fallback exists for: core.hooksPath locally cleared.
    repo_path = tmp_path / "cleared"
    subprocess.run(["git", "init", "-q", str(repo_path)], check=True)
    subprocess.run(["git", "-C", str(repo_path), "config", "--unset", "core.hooksPath"],
                    capture_output=True)  # no-op if repo has none locally; hooksPath is global
    write(repo_path / "app.py", f'KEY = "{fake.stripe_live()}"\n')
    subprocess.run(["git", "-C", str(repo_path), "add", "-A"], check=True)
    assert _commit(repo_path).returncode != 0  # still protected via core.hooksPath


def test_uninstall_restores_prior_template_dir(git_env, tmp_path):
    prev_template = tmp_path / "company-template"
    prev_hook = prev_template / "hooks" / "commit-msg"
    marker = tmp_path / "company-template-hook-ran"
    write(prev_hook, f"#!/bin/sh\necho yes > '{marker}'\n")
    os.chmod(prev_hook, 0o755)
    git("config", "--global", "init.templateDir", str(prev_template))

    installer.install("global")
    assert git("config", "--global", "init.templateDir").stdout.strip() != str(prev_template)

    repo = _new_repo(tmp_path, "templated")
    write(repo / "ok.py", "x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert _commit(repo).returncode == 0
    assert marker.exists()  # the previously-configured template hook still ran (chained)

    installer.uninstall("global")
    assert git("config", "--global", "init.templateDir").stdout.strip() == str(prev_template)


def test_install_uninstall_round_trip_is_config_identical(git_env, tmp_path):
    before = git("config", "--global", "--list").stdout
    installer.install("global")
    installer.uninstall("global")
    after = git("config", "--global", "--list").stdout
    assert after == before


def test_hook_fires_from_a_linked_worktree(git_env, tmp_path, fake):
    installer.install("global")
    repo = _new_repo(tmp_path, "main")
    write(repo / "ok.py", "x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert _commit(repo).returncode == 0

    worktree = tmp_path / "linked-worktree"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "wt", str(worktree)],
                   check=True, capture_output=True)
    write(worktree / "app.py", f'KEY = "{fake.stripe_live()}"\n')
    subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True)
    assert _commit(worktree).returncode != 0


def test_hook_fires_with_explicit_git_dir_override(git_env, tmp_path, fake):
    installer.install("global")
    repo = _new_repo(tmp_path, "explicit-gitdir")
    write(repo / "app.py", f'KEY = "{fake.stripe_live()}"\n')
    env = {**os.environ, "GIT_DIR": str(repo / ".git"), "GIT_WORK_TREE": str(repo)}
    subprocess.run(["git", "add", "-A"], check=True, cwd=str(repo), env=env)
    result = subprocess.run(["git", "commit", "-qm", "c"], cwd=str(repo), env=env,
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode != 0
