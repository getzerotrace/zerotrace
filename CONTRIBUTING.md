# Contributing

## Ground rules

1. **Never commit a real secret or real PII, even as a test case.** Fixtures use
   synthetic values only. The repo dogfoods its own hook (`pre-commit install`).
2. Deterministic detection is the source of truth. LLM code paths must remain
   *optional* and must never be able to weaken a deterministic block.
3. Any code that touches a candidate value must go through `redaction` first if
   it can reach a log, a prompt, or disk.

## Which identity your commits carry

Commits here should carry your work identity, not whichever one your machine defaults to. Set
it per clone, or - better - once for every clone of this account's repositories:

```bash
git config --local user.name "Your Name" && git config --local user.email "you@company.example"

# or, for every repository whose remote belongs to this account (git 2.36+):
#   ~/.gitconfig
#   [includeIf "hasconfig:remote.*.url:git@github.com:getzerotrace/**"]
#       path = ~/.config/git/work.gitconfig
```

Check it before your first push with `git log -1 --format='%an <%ae>'`, and make sure that
address is on your GitHub account (Settings → Emails) or the commit will not be attributed to
you. Pushing over SSH keeps the identity and the key together; `ssh -T git@github.com` names
the account you are actually authenticated as.

## Dev setup

Clone it, then either run the bootstrap script or set up manually.

```bash
git clone https://github.com/getzerotrace/zerotrace.git && cd zerotrace
./scripts/dev_bootstrap.sh          # macOS/Linux - venv, editable install, hook, doctor, tests
.\scripts\dev_bootstrap.ps1         # Windows, same steps
```

Or manually, with the same locked install CI runs:

```bash
python3 -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install --require-hashes --only-binary :all: -r requirements/dev.txt
python -m pip install --no-deps --no-build-isolation --no-index --only-binary :all: -e .
zerotrace install --global                             # also runs .pre-commit-config.yaml
pytest -q
```

## Dependencies

Every install — CI, the bootstrap scripts, the release build — uses the hash-pinned locks in
`requirements/`: `runtime.txt` (the package + its build backend), `dev.txt` (+ the dev and tui
extras) and `release.txt` (+ PyInstaller). Each package is a wheel whose hash is known in
advance, so nothing runs setup code while installing. After changing dependencies in
`pyproject.toml` (or `requirements/*.in`), regenerate them and commit the result:

```bash
python scripts/lock_deps.py              # needs uv: pipx install uv
python scripts/lock_deps.py --upgrade    # move every pin to the newest allowed version
```

`tests/test_locks.py` fails when a lock no longer satisfies `pyproject.toml`, so a forgotten
re-lock shows up in CI rather than as a silently older dependency.

## Releasing

Releases are automated by `.github/workflows/release.yml`. As you work, add user-facing
changes under `## [Unreleased]` in `CHANGELOG.md`: that is where the release notes come from,
and a release with an empty `[Unreleased]` is refused.

**The usual way: the button.** GitHub → **Actions** → **release** → **Run workflow**, branch
`main`, then choose:

| Choice | When | Example |
|---|---|---|
| `patch` | fixes only | 0.2.0 → 0.2.1 |
| `minor` | new features, nothing breaks | 0.2.0 → 0.3.0 |
| `major` | something existing users rely on changes | 0.2.0 → 1.0.0 |

Add a one-line summary if you like; it goes into the commit and tag message. The workflow
commits `chore: prepare vX.Y.Z - summary` to main, runs the full suite, builds the three
binaries, pushes the annotated tag `vX.Y.Z` and publishes the release.

**From the command line** (the same result, if you prefer to review the bump first):

```bash
python scripts/release.py bump minor            # or patch / major / an exact 1.0.0
git commit -am "chore: prepare v0.3.0 - one-line summary"
git push                                        # a new version on main starts the release
```

**By hand**, as before: push an annotated `vX.Y.Z` tag that matches `pyproject.toml`.

A version that already has its release is skipped, so re-running is harmless. Nothing is
published until it has installed and uninstalled cleanly on Linux, macOS and Windows. If a run
fails half-way, re-run it - the workflow releases the version already prepared on main instead
of burning another number. Never move a tag that has been pushed; fix forward with a patch.

**[docs/RELEASING.md](docs/RELEASING.md)** has the whole flow, every safeguard, what to do when
a step fails, and what each published asset is for.

### One-time repository settings

Nothing to add: the workflow uses the built-in `GITHUB_TOKEN` and asks for write access only in
the two jobs that need it. Check these once:

- **Settings → Actions → General → Actions permissions**: actions must be allowed to run (they
  already are if CI runs).
- **Settings → Actions → General → Workflow permissions**: "Read repository contents" is fine
  for this repository, because the jobs request `contents: write` themselves. If an
  organisation policy caps that, choose "Read and write permissions".
- **Branch protection or rulesets on `main`**: if pushes to `main` require a pull request, the
  button cannot commit the version bump. Either let *GitHub Actions* bypass the rule, or use
  the command-line way through a pull request (merging it starts the release).
- **Tag rulesets**: if tags matching `v*` are protected, allow GitHub Actions to create them.

## Tests you must add for a new detector

- a true-positive fixture, a placeholder false-positive fixture, and a policy test
  asserting the block/warn/allow decision.

## Commit / PR

- Conventional Commits. One ADR per non-trivial design decision in `docs/ADR/`.
- CI must pass: lint, type-check, tests, and the **egress-deny** and
  **no-plaintext-secret-in-artifacts** gates.

## License

ZeroTrace is licensed under [Apache-2.0](LICENSE). By submitting a contribution
you agree it is licensed under the same terms (see `LICENSE` section 5).

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
