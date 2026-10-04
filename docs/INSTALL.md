# Installing ZeroTrace

```bash
curl -fsSL https://getzerotrace.github.io/zerotrace/install.sh | bash     # macOS, Linux, WSL, Git Bash
```

```powershell
irm https://getzerotrace.github.io/zerotrace/install.ps1 | iex            # Windows PowerShell
```

That is the whole install: no pip, pipx or uv first, because the installer builds the
environment it needs. `zerotrace-uninstall` removes it again.

Nothing else on this page is required reading — it is here for the cases that are not that
one line: which platforms are supported, what the six steps do, the options, air-gapped
machines, Docker, and uninstalling.

### The install URL

`getzerotrace.github.io/zerotrace/` is this repository's own GitHub Pages site (**Settings →
Pages → Deploy from a branch: `main`, `/`**). It serves the repository root, so `index.html`
is the landing page and `install.sh` / `install.ps1` sit right beside it — nothing is copied
by hand. Being a *project* page it carries the repository name in the path. It is still
reachable from corporate networks that allow `github.io` but block unknown domains.

The installers served from `main` **resolve and install the newest release at run time**, so
`curl … | bash` installs a released version rather than the exact bytes of the script on
`main`. To pin the exact bytes, or to verify a download, go straight to the release assets —
`SHA256SUMS` sits beside them there:

```bash
curl -fsSL https://github.com/getzerotrace/zerotrace/releases/latest/download/install.sh | bash
```

### Other ways in

```bash
# straight from the release, no site in between
curl -fsSL https://github.com/getzerotrace/zerotrace/releases/latest/download/install.sh | bash

# pinning the redirect as well as the scheme, for the cautious
curl --proto '=https' --tlsv1.2 -fsSL https://getzerotrace.github.io/zerotrace/install.sh | bash

# you already have Python tooling and would rather manage it yourself
pipx install zerotrace && zerotrace install --global && zerotrace doctor
```

Reading the script before running it is always reasonable: it is the same file the one-liner
fetches, served at [https://getzerotrace.github.io/zerotrace/install.sh](https://getzerotrace.github.io/zerotrace/install.sh). The release page carries
`SHA256SUMS` beside the stamped installer if you want to verify a pinned copy.

Fleet rollouts do not use the short URL. `deploy/jamf-postinstall.sh` and
`deploy/intune-install.ps1` install from a **release** asset
(`releases/latest/download/install.sh`, or a pinned `ZEROTRACE_VERSION`) so that a rollout
installs the version that was tested and does not change under the fleet because a release
happened this morning.

### Updating

```bash
zerotrace update            # install the latest release
zerotrace update --check    # only say whether there is one
```

`zerotrace update` is the installer run again, not a second way of installing. It fetches
`install.sh` (`install.ps1` on Windows) from the latest release together with that release's
`SHA256SUMS`, refuses to run it unless the two agree, and runs it with `--version vX.Y.Z` so
that it installs exactly the release it just verified. The installer then does what it always
does: it checks the wheel and every dependency against the same file, builds the new
environment beside the old one and puts the old one back if anything fails, re-registers the
hooks and runs the self-test that proves a staged secret is refused. The model and its weights
are not touched (`--no-model`), the NER extra is kept if it was installed, and your config,
exceptions and audit log live outside the environment, so they stay.

The logo and the progress bar mark ZeroTrace arriving on a machine, so an update does not draw
them: when the installer finds ZeroTrace already installed (running it again does too, not only
`update`) it prints one line, plain step lines instead of the bar, and one line of result
instead of the welcome. A table appears only if something did not work.

- **Windows.** Windows will not replace an environment while a program inside it is running, and
  `zerotrace update` is such a program. The command starts the installer in a PowerShell of its
  own - in a new window when you ran it from a terminal - which waits for the command to exit
  and then runs. The log is `%USERPROFILE%\.zerotrace\install.log`.
- **A copy the installer did not make** (pipx, pip, the single-file binary, a source checkout)
  is not replaced from here, and `update` says what to do instead: `pipx upgrade zerotrace` for
  a pipx copy, the release page for the binary, `git pull` and the checkout's installer for a
  clone, or the one-liner above, which builds an environment of its own.
- **Ahead of you, not behind.** A copy newer than the latest release (a build from `main`) is
  reported as up to date and is never "downgraded".

**The notice.** After a command that succeeded, in a terminal, a newer release is mentioned in
one line (`zerotrace: update available 0.4.0 → 0.5.0 · run zerotrace update`). The lookup is a
single `HEAD` request to `<repository>/releases/latest`, which GitHub answers with a redirect that
names the newest tag - no token, no API rate limit - made at most once a day, beside the command
rather than in front of it, with the answer kept in `~/.zerotrace/update-check.json`. It carries
a user agent of `zerotrace/<version>` and nothing else: no repository, path, user name or finding.
Detection itself still never touches the network. Only `https` is accepted (plain `http` only to
this machine, for a local mirror), and `ZEROTRACE_REPO_URL` - the variable both installers read -
points the lookup, and `update`, at a fork or an internal mirror.

| To turn it off | Effect |
| --- | --- |
| `ZEROTRACE_NO_UPDATE_CHECK=1` | no lookup, no notice (`NO_UPDATE_NOTIFIER=1` does the same) |
| `CI` set to anything but `0`/`false` | the same: a CI run never looks |
| `updates: {check: false}` in `~/.zerotrace/config.yml` or a repo's `.zerotrace.yml` | the same, for that user or repo |
| `updates.check` locked off in the [org policy](POLICY.md) | cannot be overridden, and `zerotrace update` declines to run |

A fleet that pins the installed version (`ZEROTRACE_VERSION`, above) should lock the key off, so
a laptop neither nags nor moves off the version that was tested; `deploy/policy.example.yml`
shows the line.

# Supported platforms

Versions below were verified against primary sources (Wikipedia release history pages,
cross-checked with vendor release notes where linked) on **2026-09-17**. Re-verify at
the next update rather than reusing this table unchanged — these will drift.

Floors: Python `>=3.11` (see `pyproject.toml`), git `>=2.9` (`core.hooksPath`'s
introduction).

## macOS

| Release               | Version | Released   | Notes                                                                                            |
| --------------------- | ------- | ---------- | ------------------------------------------------------------------------------------------------ |
| Golden Gate (current) | 27      | 2026-09-14 | **Apple Silicon only** — drops Intel entirely. Last release with full Rosetta 2.          |
| Tahoe (current − 1)  | 26      | 2025-09-15 | Last release with Intel support. Verify Intel-Mac support against this release, not Golden Gate. |

Never depend on a system Python — `python3` may be only a Command Line Tools stub;
Homebrew Python (or the standalone binary) is the real dependency.

## Windows

| Release                         | Released   | Notes                                                                                                          |
| ------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------- |
| 11, version 25H2 (current)      | 2025-09-30 | Ships as an enablement package on the 24H2 servicing branch.                                                   |
| 11, version 24H2 (current − 1) | 2024-10-01 | First version to require an x86-64-v2 CPU (POPCNT + SSE4.2) at the kernel level — it will not boot otherwise. |

Version 26H1 (2026-02-10) is an ARM64-only platform release, not a general floor.
Version 26H2 was not yet GA as of 2026-09-17. Git for Windows is required (its
bundled `sh.exe` is what runs the POSIX hook shims); a bare `git.exe` without it is
not a supported target.

## Linux

| Distro   | Current                                   | Current − 1              | Notes                                            |
| -------- | ----------------------------------------- | ------------------------- | ------------------------------------------------ |
| Ubuntu   | 26.04 LTS "Resolute Raccoon" (2026-04-23) | 24.04 LTS (2024-04-25)    | 26.10 not GA yet.                                |
| Debian   | 13 "Trixie" (2025-08-09)                  | 12 "Bookworm" (oldstable) |                                                  |
| Fedora   | 44 (2026-04-28)                           | 43 (2025-10-28)           |                                                  |
| Arch     | rolling                                   | n/a                       | Always "current" by definition.                  |
| openSUSE | Leap 16 (2025-10-01)                      | Leap 15.6                 | Leap 16 replaced YaST with Agama/Cockpit/Myrlyn. |

PEP 668 (externally-managed environments) is standard on all of the above as of
these releases: `pipx`/`uv tool install`/the standalone binary are the documented
install path; a bare `pip install` is expected to refuse.

### Immutable / atomic distros

Fedora Atomic Desktops (Silverblue = GNOME, Kinoite = KDE; versioned with Fedora),
openSUSE Aeon/Kalpa (Tumbleweed-based, rolling), NixOS (26.05 stable / rolling
unstable), and SteamOS 3.x (Arch-based, e.g. 3.8.16) all have a read-only `/usr`.
Install must land under `~/.local` or `~/.zerotrace` and never touch a system path.
NixOS additionally has no FHS, so a prebuilt glibc-linked binary will not run
unmodified there without `nix-ld`; a proper `flake.nix` packaging is tracked as
separate, not-yet-scoped work.

### musl / Alpine

Best-effort only. The standalone binary is glibc-linked; `pipx`/`pip` is the real
answer on Alpine when a Python interpreter is present.

## WSL

Treated as its own install target, not as Windows: Git for Windows and each WSL
distro run separate git installs with separate global config, so `zerotrace install`
must be run once **in each environment** that will commit. See
`docs/DEPLOYMENT.md` for the detection and bootstrap design.

## What the installer does, step by step

```
[1/6] Checking this machine     OS, git >= 2.9, Python >= 3.11, the venv module, free disk
[2/6] Creating the environment  ~/.zerotrace/venv, hash-locked wheels, then the wheel itself
[3/6] Checking Docker           installed? running? may this user talk to it? image present?
[4/6] Installing the git hooks  16 shims + core.hooksPath, chaining whatever was there before
[5/6] Preparing the local model docker compose up, then the model pull (skip with --no-model)
[6/6] Validating the install    a throwaway repo, a staged fake credential, a refused commit
```

Everything lands in two places: `~/.zerotrace` (the environment, the hooks, the state and the
install log) and `~/.local/bin` (`zerotrace`, `zerotrace-uninstall`). On Windows both live
under `%USERPROFILE%\.zerotrace`, and `%USERPROFILE%\.zerotrace\bin` is added to the user
PATH through the registry (not `setx`, which truncates a PATH longer than 1024 characters).

**Never the system Python.** PEP 668 marks a distribution's Python as externally managed, and
Homebrew, Debian, Ubuntu and Fedora all refuse `pip install --user` on it. ZeroTrace builds a
virtualenv it owns, which is also what makes uninstalling exact.

### Where the hooks live

The install points git's `core.hooksPath` at one folder of small shell shims. To find it (or to
show it in a demo) ask git, which prints the folder it really uses on every OS:

```bash
git config --global core.hooksPath      # e.g. C:/Users/<you>/.zerotrace/hooks
zerotrace doctor                        # the "global hooksPath" row, and whether ZeroTrace owns it
```

| Install | Windows | macOS | Linux |
| --- | --- | --- | --- |
| `zerotrace install --global` (default, this user) | `%USERPROFILE%\.zerotrace\hooks` | `~/.zerotrace/hooks` | `~/.zerotrace/hooks` |
| `zerotrace install --system` (every user, MDM/IT) | `%ProgramData%\zerotrace\hooks` | `/usr/local/share/zerotrace/hooks` | `/usr/local/share/zerotrace/hooks` |

`ZEROTRACE_HOME` moves the per-user base (`$ZEROTRACE_HOME/hooks`) and `--hooks-dir` names the
folder outright. Open it from a terminal:

```powershell
explorer ((git config --global core.hooksPath) -replace '/', '\')     # Windows
```

```bash
open "$(git config --global core.hooksPath)"               # macOS
xdg-open "$(git config --global core.hooksPath)"           # Linux
```

Inside are 16 shims, one per git hook name, and a `.zerotrace-managed` marker. Three do work:
`pre-commit` (scans the staged diff), `post-commit` (notices a `--no-verify` commit) and
`pre-push` (re-scans what is about to leave the machine); the rest only chain the repo's own
hooks. `<hooks>/../template/hooks` holds the same shims as an `init.templateDir` fallback for new
repositories. Per-repository state (audit log, exceptions, verdict cache, scan stamps) lives in
`<repo>/.git/zerotrace/`, never in the work tree. The shims are regenerated, not edited: re-run
`zerotrace install --global` after upgrading.

### Options

| Option               | What it is for                                                         |
| -------------------- | ---------------------------------------------------------------------- |
| `--version vX.Y.Z` | a particular release instead of the latest                             |
| `--ref main`       | build from a git branch - development                                  |
| `--from <dir>`     | install from release files already downloaded: air-gapped machines, CI |
| `--no-model`       | skip Docker and the model entirely (several GB on a first run)         |
| `--with-pii`       | also install the Presidio NER engine (not hash-locked)                 |
| `--verbose`        | print every line instead of one progress bar                           |
| `--ascii`          | draw with ASCII only                                                   |

### Installing without a network

Everything the installer needs can be fetched in advance:

```bash
gh release download v0.3.0 --dir ./zt-release          # wheel, lock, installers, SHA256SUMS
pip download --require-hashes --only-binary :all: \
    -r ./zt-release/requirements-install.txt -d ./wheelhouse
# on the target machine:
PIP_NO_INDEX=1 PIP_FIND_LINKS=$PWD/wheelhouse bash ./zt-release/install.sh \
    --from ./zt-release --no-model
```

`PIP_NO_INDEX` and `PIP_FIND_LINKS` are pip's own variables; the installer passes nothing of
its own and simply lets pip read them.

## Docker

The AI tie-break runs `ollama/ollama` (pinned by version *and* digest in
`docker/docker-compose.yml`, which ships inside the wheel) with Qwen2.5-Coder 3B. It is
optional, and the installer treats it that way.

| State              | What you are told                                                         | Effect                                   |
| ------------------ | ------------------------------------------------------------------------- | ---------------------------------------- |
| running            | the image is pulled and`zerotrace-ollama` started                       | MEDIUM findings are settled by the model |
| installed, stopped | `start Docker Desktop (open -a Docker) …, then run zerotrace model up` | HIGH/CRITICAL still block, MEDIUM warns  |
| permission denied  | `sudo usermod -aG docker $USER`, and that this group is root-equivalent | as above                                 |
| not installed      | the install command for this OS, plus native Ollama as an alternative     | as above                                 |
| unresponsive       | that the daemon did not answer in 15s                                     | as above                                 |

`zerotrace model status` asks again at any time; `zerotrace model up` starts it; `zerotrace model down` stops it and keeps the image and weights, `--purge` removes those too.

## Uninstall

One command, and it removes exactly what was installed, in the order that keeps a machine
consistent: the git hooks first (so no repo points at an interpreter that is about to vanish),
then the model container, the environment, the launchers, the PATH lines and `~/.zerotrace`.

```bash
zerotrace-uninstall               # macOS / Linux / WSL
zerotrace-uninstall --purge       # ... and the model image and weights
./install.sh --uninstall          # the same, from a clone or piped
```

```powershell
zerotrace-uninstall               # Windows
pwsh -File .\install.ps1 -Uninstall
```

It never touches a line in your shell profile that it did not write (they carry an
`# added by ZeroTrace installer` marker), never touches a repository, and running it twice
says "nothing to remove" rather than failing.

Re-installing is the same one line as a first install, so install/uninstall cycles are a
reasonable way to test a machine. After each cycle:

```bash
zerotrace doctor      # is this repo actually protected? which hooks path won?
zerotrace doctor -i   # the same checks full-screen, with the fixes one key away (tui extra)
git config --global core.hooksPath   # empty after an uninstall
```

## Checking the terminal you will demo from

Consoles differ in what they can draw. `zerotrace ui` renders every screen — logo, findings
report, fix preview, progress bar, panels and status glyphs — through the same code paths the
real commands use:

```bash
zerotrace ui                 # auto-detect this console
zerotrace ui --tier png      # force the inline image (Kitty/iTerm2 protocols)
zerotrace ui --tier card     # the colour card rendering of the mark
zerotrace ui --tier unicode  # monochrome half-block silhouette
zerotrace ui --tier ascii    # what a legacy cmd.exe / cp437 console sees
zerotrace ui --tier all      # every tier in one pass
```

What ZeroTrace does automatically:

| Console                                                      | Behaviour                                                                    |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| Windows Terminal, iTerm2, GNOME Terminal, VS Code, JetBrains | shaded Unicode mark, colour, rounded box borders                             |
| Kitty, WezTerm, Ghostty, iTerm2, Konsole                     | inline PNG logo via the terminal's image protocol                            |
| any truecolour UTF-8 terminal                                | the mark as designed: dark silhouette on a light card (fg+bg per half block) |
| legacy`cmd.exe`, PowerShell 5.1 (cp437/cp1252)             | ASCII mark, ASCII box borders,`+`/`x` instead of `✓`/`✗`           |
| redirected output, CI logs,`NO_COLOR`                      | no colour, no image escapes, no in-place redraw                              |
| narrow terminals (< 60 columns)                              | smaller mark, then wordmark only                                             |

Keyboard and mouse: the hook's fix menu and the full-screen apps take `↑`/`↓` and `Enter`
in every interactive terminal, and a click wherever the terminal reports mouse events —
Windows Terminal, iTerm2, VS Code and JetBrains terminals all do. On the legacy Windows console
the menu reads clicks through the console's native input API, so no terminal setting is needed.
Where nothing can be drawn (`TERM=dumb`, piped input) the menu becomes a typed prompt.

If something still looks wrong, `zerotrace ui` prints the detected capabilities (encoding,
colour system, size, relevant environment variables) — include that output in a bug report.
