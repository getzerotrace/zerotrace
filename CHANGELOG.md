# Changelog

All notable changes documented here, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- A guided installer. `install.sh` and `install.ps1` now walk six steps - this machine, the
  environment, Docker, the git hooks, the local model, and a validation that stages a
  credential-shaped value in a throwaway repo and proves the commit is refused - with one
  progress bar, the ZeroTrace mark, and a clear sentence for every state it finds. Steps three
  to six are `zerotrace setup`, so macOS, Linux, WSL and Windows behave identically.
- `zerotrace model status | up | down [--purge]` and Docker rows in `zerotrace doctor`:
  whether Docker is installed, running and reachable by this user, whether the pinned image is
  present, whether the container is healthy and whether the model answers - each with the one
  command that fixes it on this OS. Without Docker the deterministic rules still block every
  HIGH/CRITICAL finding and ambiguous ones WARN; nothing fails because a daemon is off.
- Installing from a published release: the installer resolves the latest release (or
  `--version vX.Y.Z`), verifies every file against the release's `SHA256SUMS`, and refuses to
  install anything that does not match. `--from <dir>` installs from files downloaded earlier,
  which is what air-gapped machines and CI use.
- `zerotrace-uninstall`: one command that removes the hooks, the model container, the
  environment, the launchers and the PATH entries, keeps the multi-gigabyte model cache unless
  `--purge`, touches no repository, and says "nothing to remove" when run twice.
- Releases are verified before they are published: every release installs and uninstalls on
  Linux, macOS and Windows from its own artifacts, is created as a draft and only then made
  public, and ships `SHA256SUMS`, the hash-locked `requirements-install.txt` and installers
  stamped with their own version. `docs/RELEASING.md` documents the flow end to end.
- `policy.pii.internal_domains`: which email domains belong to an organisation is now policy
  (empty by default), so an employee address is graded HIGH where a fleet says so.
- End-to-end flows live in `tests/e2e/` and run on all three OSes in CI: install, re-install,
  uninstall, uninstall twice, a tampered wheel, a failed install rolling back, and every
  Docker state through a fake daemon.

### Changed
- Releases tag and publish themselves (`.github/workflows/release.yml`). Start one with the
  **Run workflow** button (pick patch, minor or major), by pushing a commit to main that raises
  the version, or by pushing a `vX.Y.Z` tag by hand. Each run gates on the full test suite,
  builds the three binaries, pushes an annotated tag and publishes the GitHub Release with the
  version's CHANGELOG section as notes; a version that is already released is skipped.
  `scripts/release.py` does the version bump (every declaration, the CHANGELOG section and its
  compare link), the release decision, the notes and the tag message.
- The installer builds a virtualenv of its own (`~/.zerotrace/venv`) instead of
  `pip install --user`, which a PEP 668 Python - Homebrew, Debian, Ubuntu, Fedora - refuses
  outright. A failed re-install now restores the previous environment rather than leaving the
  hooks pointing at an interpreter that no longer exists.
- One accent colour across the product (`ui/theme.py`): the logo, the product name, panel
  borders, the install progress bar and the full-screen apps are emerald instead of cyan, and
  every stop of the gradient stays readable on a light terminal as well as a dark one.
- The model image is pinned by version and digest, ships inside the wheel, and runs with
  `no-new-privileges`.
- The logo and name appear only on the first `zerotrace install` on a machine (or the first
  after an uninstall). Blocked commits, scans, pre-push reports and re-installs print just the
  findings or the result.
- Findings are listed in one order everywhere: what blocks before what only warns, and within
  each the most severe first (critical, high, medium, low), then file and line. The summary
  table, the panels under it, the full-screen reviewer and `scan --format json` all agree.

### Removed
- The hackathon use-case PDF and `docs/TEAM_PLAN.md` are no longer in the repository: both
  name people and internal systems. They live beside the clone, and `.gitignore` keeps copies
  out.

## [0.2.0] - 2026-09-22
### Added
- Runtime security gateway (`src/zerotrace/gateway`) and `zerotrace gateway` CLI subcommand:
  sanitizes arbitrary AI-agent/MCP-tool/RAG payloads (not git-backed) through the same
  detect -> decide pipeline as the git hook, masking findings out of the text instead of
  blocking, plus two new detectors for this interception point: classification markers
  (`detectors/confidentiality.py`) and indirect prompt injection (`detectors/prompt_injection.py`).
- Vendor-neutral skill packaging for the BMW skills marketplace: `skill/skill.json` (manifest)
  and `marketplace/submission.json` + `marketplace/README.md` (catalogue entry and submission
  checklist). Replaces the earlier Claude-specific `.claude-plugin` layout: ZeroTrace is a CLI
  any agent, MCP host or CI job can call, not a plugin for one vendor's client.
- MDM/fleet rollout kit (`deploy/`): Intune install/uninstall scripts, a Jamf postinstall
  script, and `policy.example.yml` for org-locked config.
- Cross-platform single-file binaries via PyInstaller (`zerotrace.spec`), built and attached
  to GitHub releases on tag push (macOS/Linux/Windows).
- `docs/DEMO_RUNBOOK.md`: click-by-click live demo script with the 8 demo beats.
- `docs/AI_CLASSIFIER.md` "Measured results": real latency and accuracy numbers from
  `zerotrace eval` on CPU-only Docker Desktop.
- Brand mark in the terminal: the logo image is rendered as a shaded Unicode ramp (ASCII on
  legacy consoles) with the ZEROTRACE wordmark beside it, shown by `zerotrace install`.
- `zerotrace ui [--tier ...]`: renders every screen so a console (CMD, PowerShell, Windows
  Terminal, IDE terminals, CI logs) can be checked in one command.
- One-line uninstall: `./install.sh --uninstall` / `install.ps1 -Uninstall`.
- `detectors/composed.py`: secrets assembled from parts (`part_a + part_b`) are detected.
- Reviewable exceptions: `.zerotrace-exceptions.json` plus `zerotrace exceptions
  [--promote|--prune]`.
- `zerotrace review`: full-screen Textual reviewer (`src/zerotrace/ui/tui.py`) —
  findings table, a detail pane showing the proposed fix as a diff, a status line, and a
  modal that requires a written reason for an exception. Keys work in either case, `F`
  applies the env/vault fix to every remaining finding after a confirmation, `O` hides
  what is already resolved, `?` opens the key list, and the cursor moves to the next open
  finding after each fix. Under 80 columns the panes stack. Falls back to the inline flow
  when there is no terminal, when `TERM=dumb`, or when `textual` is not installed;
  `--classic` forces it. Install with `pip install "zerotrace[tui]"` (`textual>=8.0`).
- `zerotrace doctor -i`: the doctor checks full-screen (`ui/tui_doctor.py`). Results appear
  as each check finishes, the pane beside them explains what the check means, and the fixes
  are one key away when they apply: `R` run again, `W` warm the model, `P` pin its digest,
  `F` patch a repo's own hook override (the last two ask first). Exits 1 while a check fails.
- `zerotrace exceptions -i`: browse both exception stores (`ui/tui_exceptions.py`); `P`
  promotes one local exception into the reviewed file, `D` revokes one (asks first, with
  Cancel focused), `X` removes the expired ones, `O` hides them.
- Every option in every full-screen app is also a button under the details: `Enter` or `→`
  on a row moves to them, `↑`/`↓` choose, `Enter` runs, `Esc`/`←` goes back, and rows,
  buttons, dialog buttons and footer keys all respond to a click. Dialogs take `←`/`→`.
- The hook's fix menu (`ui/menu.py`) takes `↑`/`↓` and `Enter`, the letter then `Enter`, or
  a mouse click, on macOS, Linux and Windows (legacy console included). It draws inline,
  erases itself and leaves a one-line record of the choice; it falls back to the typed
  prompt where a menu cannot be drawn. `zerotrace ui` previews it.
- Exceptions record the rule id and the file they cover (never the value), so a reviewer can
  tell what an entry silences; older entries still load. `audit.exceptions` gains `revoke()`,
  `promote(fingerprints)` for a single entry, and typed `Entry` rows from `listing()`.

### Changed
- `prompt_toolkit` is a new runtime dependency (pure Python; its only dependency is
  `wcwidth`). It is imported only when the hook has a finding to ask about.
- In the hook's fix menu a bare `Enter` now takes the highlighted option, which is the
  recommended fix, where it used to mean Abort. Keys typed while the scan ran are discarded
  first, so a stray `Enter` cannot choose an option nobody has seen.
- The three full-screen apps share one frame (`ui/tui_common.py`): layout, cursor handling,
  action buttons, help and `ctrl+q`. The footer shows keys as capitals, as the help and the
  buttons do, and Textual's command palette is switched off.
- The panes stack whenever the list could not show all of its columns beside the details,
  not only under 80 columns. At exactly 80 columns the reviewer's verdict column was cut off.
- The `tui` extra requires `textual>=8.0` (the version the suite runs against), and the `dev`
  extra now includes it and `pytest-asyncio`; CI checks that both import before testing.
- The PyInstaller binary leaves `textual` out explicitly, so the binary's `review` keeps the
  inline flow instead of bundling a half-working full-screen app.
- Default `model.timeout_seconds` raised from 20 s to 120 s: measured CPU-only inference is
  ~106 s p50, so the old default failed every tie-break closed to WARN in repos without a config.
- README and `docs/ARCHITECTURE.md` describe both enforcement points (commit time and AI
  runtime); `sonar-project.properties` now analyses the installer and fleet scripts.

### Fixed
- A base64-obscured Stripe live key no longer evades detection (`stripe-live-key-base64` rule).
- Unicode homoglyph identifiers (e.g. Cyrillic `а` substituted for Latin `a`) no longer bypass
  hardcoded-credential keyword matching.
- `zerotrace doctor --warm` no longer crashes with `UnicodeEncodeError` on legacy (non-UTF-8)
  Windows console codepages; falls back to `?` instead.
- A comment naming the AI tie-break's own verdict keywords (e.g. "classify
  TEST_FIXTURE_OR_PLACEHOLDER") could flip a real secret to an unsafe allow; the policy engine
  now refuses to honor an ALLOW verdict when the finding's context matches a prompt-injection
  pattern, regardless of what the model concluded (found via `zerotrace eval`).
- Gateway: a prompt injection sharing a line with a secret was dropped by `pipeline.dedupe`, so
  the injected instruction was forwarded to the model and missing from the audit record. PII,
  prompt-injection and confidentiality findings are now deduplicated per value, not per line.
- Leaving the full-screen reviewer with `ctrl+q` reported a clean review: Textual's own
  binding exits with no value, which read as success. It now means what `A` and `Q` mean —
  anything still open keeps the commit blocked.
- CI failed to collect `tests/test_review_tui.py` (`No module named 'textual'`): the `dev`
  extra did not install the `tui` extra. The full-screen tests also skip cleanly on a
  guardrail-only install instead of erroring.
- Code, paths and reasons containing square brackets were read as formatting, in the inline
  flow, the full-screen apps, the doctor table and the exception listing: `cfg[api_key]`
  vanished from the displayed line (so the preview showed a different line from the one
  written), `app/[slug]/page.tsx` lost its directory, and a `[/]` crashed the hook with an
  internal error. All such data is escaped now.
- Capital `Q` did nothing in the reviewer although the status line says "press Q"; `Y`/`N`
  in confirmations and `Q` in the help screen had the same gap.
- `doctor --pin-model` reported OK when there was no `.zerotrace.yml` to pin into; it warns.
- `Ctrl+D` (or a closed stdin) at the fix prompt printed "internal error"; it now reads as an
  abort, and nothing is committed either way.
- A malformed entry in the shared exceptions file (a string where an object belongs) could
  crash the exception listing; such entries are ignored, as they already were for matching.
- `zerotrace version` printed 0.2.0 while the package metadata said 0.1.0. Every version
  declaration (pyproject.toml, `zerotrace.__version__`, the skill and marketplace manifests)
  now agrees, and `tests/test_version.py` keeps them in step with the changelog.
- Aadhaar and payment-card detection matched checksum-valid digit runs *inside* hex digests
  (a lock file's `--hash=sha256:…`, a git object id), so committing a hash-pinned requirements
  file was blocked as national-ID or card data. Both now need a non-word character on either
  side, as phone numbers already did; standalone numbers are still caught.
- The full-screen doctor could lose its last result into a closed screen: quitting while a
  check was still running let the result arrive after the widgets were torn down, and the
  check thread died with `NoMatches` (seen on the Windows runner). Results that arrive after
  the app starts to exit are now dropped.
- `tests/test_cli.py` failed on the Windows runner because its console is a column narrower
  and folded a table cell; the test now pins a wide console, since it checks escaping.
- The exceptions browser's expiry labels are computed on whole days of a `timedelta` rather
  than chained float comparisons (the same results, which SonarQube misread as a dead branch).

### Security
- The release workflow is read-only by default; only the job that publishes the GitHub release
  gets `contents: write`, so none of the binary builds can write to the repository.
- Every install in CI, the release build and the contributor bootstrap scripts is
  hash-locked and wheel-only: `requirements/runtime.txt`, `dev.txt` and `release.txt`, written
  by `scripts/lock_deps.py` (`uv pip compile --universal --generate-hashes`), are installed with
  `pip install --require-hashes --only-binary :all:`, and the project itself with `--no-deps
  --no-build-isolation --no-index`. No dependency runs setup code while installing, and a
  tampered or unexpected file fails the hash check. `tests/test_locks.py` fails when a lock no
  longer satisfies `pyproject.toml`; Dependabot now watches the locks as well.
- Paths written into generated hook scripts must be absolute and on one line, and are rebuilt
  from the validated match. Shell quoting stopped injection, but an option-like value such
  as `-x` could still have been read as a flag by `[ -x … ]` or `exec`.
- The Jamf script (runs as root) downloads the installer over HTTPS only, redirects included
  (`--proto '=https' --tlsv1.2`), to a file before running it, so a dropped connection never
  runs a truncated script. The documented `curl` one-liners use the same flags.
- `.sonarcloud.properties` gives SonarQube's Automatic Analysis the configuration it was
  missing (it ignores `sonar-project.properties`): test sources, the supported Python
  versions, and the fixture exclusions. `.github` stays analysed.
- Generated eval passwords are shuffled with `secrets.randbelow` (Fisher-Yates) instead of
  `SystemRandom().shuffle`, so nothing in the package reads as the non-cryptographic `random`
  module. The demo fixtures now run as a non-root user and keep database backups, so the only
  mistake each one teaches is the secret it plants.

## [0.1.0] - 2026-09-17
First public release.
### Added
- Local-first pre-commit secret & PII guardrail for every repo on a machine.
- `zerotrace install --global` (or `--system` for MDM/IT rollout) sets a managed `core.hooksPath`
  that chains every hook name; `uninstall` restores whatever was there before. There is
  deliberately no per-repo install mode: `zerotrace doctor --fix` only patches a repo-local
  override (e.g. husky) that would otherwise defeat the global install.
- One-command bootstrap installers (`install.sh`, `install.ps1`) that need no pre-existing
  pip/pipx/uv - they bootstrap Python itself via `ensurepip` and the OS package manager if needed.
- Interactive remediation inside `git commit` (terminal reattach); `pre-push` backstop for
  `--no-verify`; `scan --range/--all/--format json`; `init`; `doctor [--pin-model] [--warm] [--fix]`;
  `eval`.
- Detection layers: provider rule pack (26 formats incl. DB connection strings), hardcoded
  credential assignments across 10+ languages and config formats, sensitive files, PAN/Aadhaar/
  card/IBAN with checksums, detect-secrets, regex + NER PII.
- Layered config (org ← user ← repo) with org-locked keys.
- AI tie-break: shape features + fully masked windows, few-shot injection-hardened prompt,
  JSON-schema constrained decoding, escalation to BLOCK, concurrent + cached, digest pinning,
  Ollama and OpenAI-compatible runtimes (AWS-ready), remote-endpoint guardrails.
- Language-aware fixes (`os.environ[...]`, `process.env.X`, `os.Getenv`, `System.getenv`,
  `var.x`, `${X}`), `[U]nstage + .gitignore + .env.example`.
- WSL-aware environment detection (`platform_env.py`): classifies Windows/macOS/Linux/WSL1/WSL2
  and repo paths as native/DrvFs/`\\wsl$\`, guarding hook installs on DrvFs mounts.
- Demo kit for macOS/Linux and Windows sharing one fixture renderer; 170+ tests.
### Changed
- Fixes are applied to the index blob (unstaged edits are never staged).
- State moved into `.git/zerotrace/`; Presidio is an opt-in extra; the `ollama` SDK is no
  longer needed.
### Fixed
- Secrets other than AWS/PEM could reach the model unredacted.
- The interactive menu never appeared under the pre-commit framework.

[Unreleased]: https://github.com/getzerotrace/zerotrace/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/getzerotrace/zerotrace/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/getzerotrace/zerotrace/releases/tag/v0.1.0
