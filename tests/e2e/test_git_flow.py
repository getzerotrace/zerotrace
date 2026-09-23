"""Real git repos: diff collection, index-safe remediation, unstage + gitignore, pre-push.

End to end through git itself - every assertion is about what ends up in the index or in the
commit, not about what a function returned."""
import os
import subprocess
import sys

from zerotrace import pipeline
from zerotrace.collectors.staged_diff import collect_commit, collect_staged
from zerotrace.config import load_config
from zerotrace.remediation import applier

from ..conftest import git, write


def test_collect_staged_only_added_lines(repo):
    write("app.py", "a = 1\nb = 2\n")
    git("add", "app.py")
    git("commit", "-qm", "base", "--no-verify")
    write("app.py", "a = 1\nnew = 3\nb = 2\n")
    git("add", "app.py")
    cs = collect_staged()
    assert [(u.path, u.line_no, u.text) for u in cs.units] == [("app.py", 2, "new = 3")]
    assert "a = 1" in cs.units[0].window


def test_paths_with_spaces_and_binary(repo):
    write("my dir/cfg file.py", "x = 1\n")
    with open("blob.bin", "wb") as f:
        f.write(b"\x00\x01\x02")
    git("add", "-A")
    cs = collect_staged()
    assert "my dir/cfg file.py" in cs.paths and "blob.bin" in cs.paths


def test_pipeline_blocks_hardcoded_secret(repo, fake):
    write("src/pay.py", f'import stripe\nstripe.api_key = "{fake.stripe_live()}"\n')
    git("add", "-A")
    decisions = pipeline.scan(collect_staged(), load_config())
    assert [(d.finding.rule_id, d.action) for d in decisions] == [("stripe-live-key", "block")]


def test_apply_edits_index_and_keeps_unstaged_work(repo, fake):
    key = fake.stripe_live()
    write("pay.py", f'KEY = "{key}"\n')
    git("add", "pay.py")
    write("pay.py", f'KEY = "{key}"\nprint("unstaged work")\n')  # not staged

    where = applier.apply("pay.py", 1, 'KEY = os.environ["KEY"]', f'KEY = "{key}"')
    assert where == "index+worktree"
    staged = git("show", ":pay.py").stdout
    assert staged == 'KEY = os.environ["KEY"]\n'
    with open("pay.py", encoding="utf-8") as f:
        assert f.read() == 'KEY = os.environ["KEY"]\nprint("unstaged work")\n'
    assert "print" not in staged  # the unrelated edit stayed unstaged


def test_apply_preserves_crlf(repo):
    with open("win.ini", "w", newline="") as f:
        f.write("a=1\r\npassword=hunter\r\n")
    git("add", "win.ini")
    applier.apply("win.ini", 2, "password=${PASSWORD}", "password=hunter")
    blob = subprocess.run(["git", "cat-file", "blob", ":win.ini"], capture_output=True).stdout
    assert blob == b"a=1\r\npassword=${PASSWORD}\r\n"


def test_unstage_env_creates_example_and_gitignore(repo):
    write(".env", "API_KEY=abc\nexport DB_PASSWORD=xyz\n")
    git("add", ".env")
    actions = applier.unstage_and_ignore(".env")
    assert len(actions) == 3
    staged = git("diff", "--cached", "--name-only").stdout.split()
    assert ".env" not in staged and ".gitignore" in staged and ".env.example" in staged
    assert git("show", ":.env.example").stdout.endswith("API_KEY=\nDB_PASSWORD=\n")
    assert os.path.exists(".env")  # the developer's file is untouched


def test_scan_commit_catches_no_verify(repo, fake):
    write("gh.py", f'TOKEN = "{fake.github()}"\n')
    git("add", "-A")
    git("commit", "-qm", "sneaky", "--no-verify")
    sha = git("rev-parse", "HEAD").stdout.strip()
    decisions = pipeline.scan(collect_commit(sha), load_config(), use_model=False)
    assert decisions and decisions[0].action == "block"


def test_cli_run_headless_blocks_and_exits_1(repo, fake):
    write("app.js", f'const key = "{fake.openai()}";\n')
    git("add", "-A")
    result = subprocess.run([sys.executable, "-m", "zerotrace", "run"],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 1
    assert "openai-api-key" in result.stdout
    assert "sk-proj-" not in result.stdout  # the value is never printed


def test_state_lives_inside_dot_git(repo, fake):
    write("app.js", f'const key = "{fake.openai()}";\n')
    git("add", "-A")
    subprocess.run([sys.executable, "-m", "zerotrace", "run"], capture_output=True,
                   stdin=subprocess.DEVNULL)
    assert os.path.exists(".git/zerotrace/audit.log.jsonl")
    assert "zerotrace" not in git("status", "--porcelain", "--untracked-files=all").stdout
