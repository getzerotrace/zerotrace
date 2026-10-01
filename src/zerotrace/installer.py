"""Install ZeroTrace for every repo on the machine via a global/system core.hooksPath.

`core.hooksPath` replaces ALL hooks, so the managed directory contains a pass-through shim
for every hook name. Shims run the repo's own `.git/hooks/<name>` and any hooks dir that was
configured before us, so git-lfs, husky-style scripts and commit-msg linters keep working.
"""
import contextlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from collections.abc import Callable

from . import gitutil, platform_env
from .config import zerotrace_home

MARKER = ".zerotrace-managed"
HOOKS_PATH_KEY = "core.hooksPath"      # the git config key a global install owns
TEMPLATE_DIR_KEY = "init.templateDir"  # repo-local fallback: applied by git init/clone only
# One label per real unit of work in install(), in order -- the CLI progress bar's total.
INSTALL_STEPS = (
    "Checking install target",
    "Writing hook shims",
    "Registering core.hooksPath",
    "Writing template-dir shims",
    "Registering init.templateDir",
    "Saving install state",
)
HOOK_NAMES = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
    "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout",
    "post-merge", "pre-push", "post-rewrite", "pre-auto-gc", "push-to-checkout",
    "sendemail-validate",
)

# The common git dir, usually without starting git: git runs a hook from the top of the work
# tree, and a plain repository keeps its git dir in .git. A process costs ~0.4 s on a Windows
# machine with a virus scanner, and every hook of every commit pays for each one.
_GIT_DIR = """if [ -z "${{GIT_DIR:-}}" ] && [ -d .git ]; then
  git_dir=.git
else
  git_dir=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0
fi
"""

_HEADER = """#!/bin/sh
# Installed by ZeroTrace (`zerotrace install`). Do not edit: re-run install to regenerate.
ZT_PY={python}
PREV_HOOKS={prev}
hook_name=${{0##*/}}
""" + _GIT_DIR + """
run_chained() {{
  # 1) the repo's own hook  2) a hooks dir configured before ZeroTrace. A repo made by `git init`
  # after the install holds ZeroTrace's own template shim as its hook: ZEROTRACE_CHAINED tells it
  # that this shim runs ZeroTrace, so it must not scan a second time.
  if [ -x "$git_dir/hooks/$hook_name" ]; then
    ZEROTRACE_CHAINED=1 "$git_dir/hooks/$hook_name" "$@" || return $?
  fi
  if [ -n "$PREV_HOOKS" ] && [ -x "$PREV_HOOKS/$hook_name" ]; then "$PREV_HOOKS/$hook_name" "$@" || return $?; fi
  return 0
}}

zerotrace() {{
  if [ ! -x "$ZT_PY" ]; then
    echo "zerotrace: $ZT_PY not found. Blocking (fail closed). Reinstall ZeroTrace or run 'git config --global --unset core.hooksPath'." >&2
    return 1
  fi
  "$ZT_PY" -m zerotrace "$@"
}}
"""

_CHAIN = '\nrun_chained "$@"\n'

_PASSTHROUGH = _HEADER + _CHAIN

_PRE_COMMIT = _HEADER + """
run_chained "$@" || exit $?
# Repos that use the pre-commit framework can't `pre-commit install` while core.hooksPath is
# set, so run their config for them.
if [ ! -x "$git_dir/hooks/pre-commit" ] && [ -f .pre-commit-config.yaml ] \\
   && [ "${{ZEROTRACE_CHAIN_PRECOMMIT:-1}}" = "1" ] && command -v pre-commit >/dev/null 2>&1; then
  pre-commit run --hook-stage pre-commit || exit $?
fi
# Reattach the terminal so the [V/R/U/E/A] menu works inside `git commit`.
# IDEs and GUI clients have no terminal: they get the headless report instead.
if [ -t 1 ] && {{ : </dev/tty; }} 2>/dev/null; then
  zerotrace run --hook </dev/tty
else
  zerotrace run --hook
fi
"""

_PRE_PUSH = _HEADER + """
input=$(cat)
if [ -x "$git_dir/hooks/pre-push" ]; then
  printf '%s\\n' "$input" | ZEROTRACE_CHAINED=1 "$git_dir/hooks/pre-push" "$@" || exit $?
fi
if [ -n "$PREV_HOOKS" ] && [ -x "$PREV_HOOKS/pre-push" ]; then
  printf '%s\\n' "$input" | "$PREV_HOOKS/pre-push" "$@" || exit $?
fi
printf '%s\\n' "$input" | zerotrace pre-push "$@"
"""

# `git commit --no-verify` skips the pre-commit hook but git still runs post-commit. The pre-commit
# hook stamps the tree it scanned (bypass.py), so a commit with no stamp skipped the scan and
# `zerotrace post-commit` looks at it. Every ordinary commit, and every commit a rebase replays,
# leaves here in a few milliseconds without starting Python.
_POST_COMMIT_CHECK = """
tree=$(git rev-parse -q --verify 'HEAD^{{tree}}' 2>/dev/null) || exit 0
[ -e "$git_dir/zerotrace/verified/$tree" ] && exit 0
work_git_dir=$(git rev-parse --git-dir 2>/dev/null)
if [ -d "$work_git_dir/rebase-merge" ] || [ -d "$work_git_dir/rebase-apply" ]; then exit 0; fi
zerotrace post-commit
"""

_POST_COMMIT = _HEADER + _CHAIN + _POST_COMMIT_CHECK

# init.templateDir fallback: copied by `git init`/`git clone` into a brand-new .git/hooks, so it
# only ever runs there directly (never self-referential, unlike the hooksPath shim above), and
# only matters for repos created while core.hooksPath is locally cleared/overridden.
_TPL_HEADER = """#!/bin/sh
# Installed by ZeroTrace (`zerotrace install`) as an init.templateDir fallback. Do not edit:
# re-run install to regenerate. core.hooksPath is the primary protection; this only seeds new
# repos created while it is locally cleared/overridden.
ZT_PY={python}
PREV_TEMPLATE_HOOKS={prev}
hook_name=${{0##*/}}

run_chained() {{
  # a hook that was seeded by a template configured before ZeroTrace
  if [ -n "$PREV_TEMPLATE_HOOKS" ] && [ -x "$PREV_TEMPLATE_HOOKS/$hook_name" ]; then
    "$PREV_TEMPLATE_HOOKS/$hook_name" "$@" || return $?
  fi
  return 0
}}

zerotrace() {{
  if [ ! -x "$ZT_PY" ]; then
    echo "zerotrace: $ZT_PY not found. Blocking (fail closed). Reinstall ZeroTrace." >&2
    return 1
  fi
  "$ZT_PY" -m zerotrace "$@"
}}
"""

# The global shim chains the copy of these that `git init` seeds into .git/hooks, and runs
# ZeroTrace itself: the copy must not do it a second time (and still runs when git calls it
# directly, which is the case the fallback exists for).
_TPL_ONCE = '[ -n "$ZEROTRACE_CHAINED" ] && exit 0\n'

_TPL_PASSTHROUGH = _TPL_HEADER + _CHAIN

_TPL_PRE_COMMIT = _TPL_HEADER + """
run_chained "$@" || exit $?
""" + _TPL_ONCE + """if [ -t 1 ] && {{ : </dev/tty; }} 2>/dev/null; then
  zerotrace run --hook </dev/tty
else
  zerotrace run --hook
fi
"""

_TPL_PRE_PUSH = _TPL_HEADER + """
input=$(cat)
if [ -n "$PREV_TEMPLATE_HOOKS" ] && [ -x "$PREV_TEMPLATE_HOOKS/pre-push" ]; then
  printf '%s\\n' "$input" | "$PREV_TEMPLATE_HOOKS/pre-push" "$@" || exit $?
fi
""" + _TPL_ONCE + """printf '%s\\n' "$input" | zerotrace pre-push "$@"
"""

_TPL_POST_COMMIT = _TPL_HEADER + _GIT_DIR + _CHAIN + _TPL_ONCE + _POST_COMMIT_CHECK

_REPO_BLOCK = """# >>> zerotrace >>>
if [ -t 1 ] && {{ : </dev/tty; }} 2>/dev/null; then
  {python} -m zerotrace run --hook </dev/tty || exit $?
else
  {python} -m zerotrace run --hook || exit $?
fi
# <<< zerotrace <<<
"""


# What may be written into a hook script as a path: absolute, one line, no NUL. POSIX `/…`,
# Windows `C:\…` or `C:/…`, UNC `\\host\…`. Absolute means it can never be read as an option
# (`-x`) by `[ -x … ]` or `exec`, which quoting alone does not prevent.
_SCRIPT_PATH_RE = re.compile(r"(?:/|[A-Za-z]:[\\/]|\\\\)[^\x00\n\r]{0,4095}")


def _checked_script_path(value: str) -> str:
    """The path as it may appear in a generated script, rebuilt from the match so that
    nothing unvalidated (a hand-edited state file, a git config value) reaches the script."""
    match = _SCRIPT_PATH_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"refusing to write a non-absolute path into a hook script: {value!r}")
    return match.group(0)


def _sh_path(path: str) -> str:
    return path.replace("\\", "/")


def _sh_quote(value: str) -> str:
    """An absolute path for a generated hook script, quoted for sh. Empty means "none"."""
    return shlex.quote(_sh_path(_checked_script_path(value))) if value else "''"


def python_path() -> str:
    return os.path.abspath(sys.executable)


def default_hooks_dir(scope: str) -> str:
    if scope == "system":
        if os.name == "nt":
            return os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "zerotrace", "hooks")
        return "/usr/local/share/zerotrace/hooks"
    return os.path.join(zerotrace_home(), "hooks")


def default_template_dir(scope: str) -> str:
    """A git "template directory": its hooks/ subfolder is copied into .git/hooks by every
    `git init` and `git clone`. This is a fallback for the (rare) case a repo is created while
    core.hooksPath has been locally cleared/overridden; core.hooksPath itself already covers
    every git invocation, so it is the primary mechanism and this only matters at creation time.
    """
    if scope == "system":
        if os.name == "nt":
            return os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "zerotrace", "template")
        return "/usr/local/share/zerotrace/template"
    return os.path.join(zerotrace_home(), "template")


def _state_path() -> str:
    return os.path.join(zerotrace_home(), "install-state.json")


def _load_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(zerotrace_home(), exist_ok=True)
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


_USER_MODE = 0o700          # a per-user install: only the owner needs to run the hook
_SYSTEM_MODE = 0o755        # a machine-wide install: every user's git must read+execute it


def _write_script(path: str, content: str, mode: int = _USER_MODE) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    os.chmod(path, mode)
    if os.name != "nt" and not os.access(path, os.X_OK):
        # A Windows drive mounted into WSL without the `metadata` option silently drops the
        # exec bit: fail loudly here rather than let git skip a hook it can't execute.
        raise PermissionError(
            f"{path}: chmod +x did not take effect (mount without exec-bit support?). "
            "Install into a directory on the Linux filesystem, e.g. $HOME/.zerotrace, "
            "not a path under /mnt/<drive>.")


def is_managed(hooks_dir: str | None) -> bool:
    if not hooks_dir:
        return False
    return os.path.exists(os.path.join(os.path.expanduser(hooks_dir), MARKER))


def write_hooks(hooks_dir: str, prev_hooks: str = "", scope: str = "global") -> None:
    mode = _SYSTEM_MODE if scope == "system" else _USER_MODE
    os.makedirs(hooks_dir, mode=0o755 if scope == "system" else 0o700, exist_ok=True)
    fmt = {"python": _sh_quote(python_path()), "prev": _sh_quote(prev_hooks)}
    for name in HOOK_NAMES:
        template = {"pre-commit": _PRE_COMMIT, "pre-push": _PRE_PUSH,
                    "post-commit": _POST_COMMIT}.get(name, _PASSTHROUGH)
        _write_script(os.path.join(hooks_dir, name), template.format(**fmt), mode)
    with open(os.path.join(hooks_dir, MARKER), "w", encoding="utf-8") as f:
        f.write("This directory is generated by `zerotrace install`.\n")


def write_template_hooks(hooks_dir: str, prev_template_hooks: str = "", scope: str = "global") -> None:
    mode = _SYSTEM_MODE if scope == "system" else _USER_MODE
    os.makedirs(hooks_dir, mode=0o755 if scope == "system" else 0o700, exist_ok=True)
    fmt = {"python": _sh_quote(python_path()), "prev": _sh_quote(prev_template_hooks)}
    for name in HOOK_NAMES:
        template = {"pre-commit": _TPL_PRE_COMMIT, "pre-push": _TPL_PRE_PUSH,
                    "post-commit": _TPL_POST_COMMIT}.get(name, _TPL_PASSTHROUGH)
        _write_script(os.path.join(hooks_dir, name), template.format(**fmt), mode)
    with open(os.path.join(hooks_dir, MARKER), "w", encoding="utf-8") as f:
        f.write("This directory is generated by `zerotrace install` (init.templateDir).\n")


def _git_config(scope: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "config", f"--{scope}", *args], capture_output=True, text=True)


def _checked_hooks_dir(hooks_dir: str) -> str:
    """Resolve the target and refuse to write into a directory holding unrelated files."""
    resolved = os.path.realpath(os.path.expanduser(hooks_dir))
    if platform_env.classify_path(resolved) == "drvfs":
        raise PermissionError(
            f"{resolved} is on a Windows drive mounted into WSL (/mnt/<drive>); the exec bit "
            "and line endings it needs won't survive there. Use a path under $HOME instead, "
            "e.g. --hooks-dir ~/.zerotrace/hooks.")
    if os.path.exists(resolved) and not os.path.isdir(resolved):
        raise PermissionError(f"{resolved} exists and is not a directory")
    if os.path.isdir(resolved) and not is_managed(resolved):
        unexpected = sorted(set(os.listdir(resolved)) - set(HOOK_NAMES) - {MARKER})
        if unexpected:
            raise PermissionError(
                f"{resolved} already contains {', '.join(unexpected[:3])}"
                f"{'…' if len(unexpected) > 3 else ''}; refusing to write hook scripts there. "
                "Point --hooks-dir at a dedicated directory.")
    return resolved


def _set_config(scope: str, key: str, value: str) -> None:
    result = _git_config(scope, key, value)
    if result.returncode != 0:
        raise PermissionError(result.stderr.strip() or f"could not set {scope} {key}")


def _template_managed(template_dir: str | None) -> bool:
    return is_managed(os.path.join(template_dir, "hooks")) if template_dir else False


def _chained_hooks_path(scope: str, state: dict, log: list[str]) -> str:
    """The core.hooksPath that was set before ours, which the new shims keep running."""
    current = gitutil.config_get(HOOKS_PATH_KEY, scope)
    if not current:
        return ""
    if not is_managed(current):
        state[f"{scope}_previous_hooks_path"] = current
        log.append(f"existing {scope} core.hooksPath {current} will still run (chained)")
        return os.path.abspath(os.path.expanduser(current))
    prev = state.get(f"{scope}_previous_hooks_path", "")
    return os.path.abspath(os.path.expanduser(prev)) if prev else ""


def _chained_template_hooks(scope: str, state: dict, log: list[str]) -> str:
    """The hooks directory of the init.templateDir that was set before ours."""
    current = gitutil.config_get(TEMPLATE_DIR_KEY, scope)
    if not current:
        return ""
    if not _template_managed(current):
        state[f"{scope}_previous_template_dir"] = current
        log.append(f"existing {scope} init.templateDir {current} will still seed new "
                   "repos (chained)")
        return os.path.join(os.path.abspath(os.path.expanduser(current)), "hooks")
    prev = state.get(f"{scope}_previous_template_dir", "")
    return os.path.join(os.path.abspath(os.path.expanduser(prev)), "hooks") if prev else ""


def _restore_setting(scope: str, key: str, managed: bool, previous: str, log: list[str]) -> None:
    """Put back the value an install displaced, or drop the setting if it displaced none."""
    if not managed:
        log.append(f"{scope} {key} is not managed by ZeroTrace; left unchanged")
    elif previous:
        _git_config(scope, key, previous)
        log.append(f"restored {scope} {key} -> {previous}")
    else:
        _git_config(scope, "--unset", key)
        log.append(f"unset {scope} {key}")


def _remove_hook_files(hooks_dir: str) -> None:
    for name in (*HOOK_NAMES, MARKER):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(hooks_dir, name))
    with contextlib.suppress(OSError):
        os.rmdir(hooks_dir)


def install(scope: str = "global", hooks_dir: str | None = None,
            template_dir: str | None = None,
            on_step: Callable[[str], None] | None = None) -> list[str]:
    """scope: global | system. Returns human-readable log lines.

    on_step, if given, is called once per INSTALL_STEPS label, in order, purely
    so a caller (the CLI) can drive a progress bar -- it never changes behavior.
    """
    def _step(label: str) -> None:
        if on_step:
            on_step(label)

    _step(INSTALL_STEPS[0])
    hooks_dir = _checked_hooks_dir(hooks_dir or default_hooks_dir(scope))
    template_dir = template_dir or default_template_dir(scope)
    template_hooks_dir = _checked_hooks_dir(os.path.join(template_dir, "hooks"))
    log: list[str] = []
    state = _load_state()
    prev = _chained_hooks_path(scope, state, log)

    _step(INSTALL_STEPS[1])
    write_hooks(hooks_dir, prev, scope)
    log.append(f"wrote {len(HOOK_NAMES)} hook shims to {hooks_dir}")

    _step(INSTALL_STEPS[2])
    _set_config(scope, HOOKS_PATH_KEY, _sh_path(hooks_dir))
    state[f"{scope}_hooks_dir"] = hooks_dir
    log.append(f"git config --{scope} {HOOKS_PATH_KEY} {_sh_path(hooks_dir)}")

    # init.templateDir: a repo-local fallback so `git init`/`clone` still seed .git/hooks if
    # core.hooksPath is ever locally cleared or overridden; core.hooksPath (above) is the
    # mechanism that actually protects every invocation, so this only matters at creation time.
    prev_template_hooks = _chained_template_hooks(scope, state, log)

    _step(INSTALL_STEPS[3])
    write_template_hooks(template_hooks_dir, prev_template_hooks, scope)

    _step(INSTALL_STEPS[4])
    _set_config(scope, TEMPLATE_DIR_KEY, _sh_path(template_dir))
    state[f"{scope}_template_dir"] = template_dir
    log.append(f"git config --{scope} {TEMPLATE_DIR_KEY} {_sh_path(template_dir)}")

    _step(INSTALL_STEPS[5])
    _save_state(state)
    return log


def uninstall(scope: str = "global") -> list[str]:
    log: list[str] = []
    state = _load_state()
    current = gitutil.config_get(HOOKS_PATH_KEY, scope)
    prev = state.pop(f"{scope}_previous_hooks_path", "")
    hooks_dir = state.pop(f"{scope}_hooks_dir", current or "")
    _restore_setting(scope, HOOKS_PATH_KEY, is_managed(current), prev, log)
    if hooks_dir and is_managed(hooks_dir):
        _remove_hook_files(hooks_dir)
        log.append(f"removed {hooks_dir}")

    current_template = gitutil.config_get(TEMPLATE_DIR_KEY, scope)
    prev_template = state.pop(f"{scope}_previous_template_dir", "")
    template_dir = state.pop(f"{scope}_template_dir", current_template or "")
    _restore_setting(scope, TEMPLATE_DIR_KEY, _template_managed(current_template), prev_template, log)
    if _template_managed(template_dir):
        _remove_hook_files(os.path.join(template_dir, "hooks"))
        with contextlib.suppress(OSError):
            os.rmdir(template_dir)
        log.append(f"removed {template_dir}")

    _save_state(state)
    return log


def install_repo() -> list[str]:
    """Remediation for one repo whose local core.hooksPath (husky etc.) overrides the global
    install - invoked via `zerotrace doctor --fix`. Not a standalone install path: it patches
    a pre-existing override, it never substitutes for `zerotrace install --global`."""
    root = gitutil.repo_root()
    local = gitutil.config_get(HOOKS_PATH_KEY, "local")
    if not local and is_managed(gitutil.config_get(HOOKS_PATH_KEY)):
        return ["this repo is already covered by the global/system ZeroTrace install"]
    if local and os.path.basename(local.rstrip("/\\")) == "_" and \
            os.path.basename(os.path.dirname(local.rstrip("/\\"))) == ".husky":
        target = os.path.join(root, ".husky", "pre-commit")      # husky v9 user hook file
    else:
        target = os.path.join(effective_hooks_dir(), "pre-commit")
    block = _REPO_BLOCK.format(python=_sh_quote(python_path()))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target):
        with open(target, encoding="utf-8") as f:
            content = f.read()
        if "# >>> zerotrace >>>" in content:
            return [f"{target} already runs ZeroTrace"]
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            f.write(("\n" if not content.endswith("\n") else "") + block)
        os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR)  # keep the repo's own bits
        return [f"appended ZeroTrace to existing hook {target}"]
    _write_script(target, "#!/bin/sh\n" + block)
    return [f"wrote {target}"]


def effective_hooks_dir() -> str:
    path = gitutil.git("rev-parse", "--git-path", "hooks").strip()
    return os.path.abspath(path)
