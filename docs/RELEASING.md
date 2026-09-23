# Releasing

One click, or one push, produces everything a user can install - and nothing is published
until it has been proven to install on Linux, macOS and Windows.

```
  code on main
       │
       ▼
  version      scripts/release.py bump  →  pyproject.toml, __init__.py, skill.json,
                                           marketplace/submission.json, CHANGELOG section
       │
       ▼
  tag          vX.Y.Z, annotated, pushed by the workflow
       │
       ▼
  build        sdist + wheel · 3 single-file binaries · installers stamped with this version
       │       · requirements-install.txt (hash-locked) · SHA256SUMS over all of it
       ▼
  verify       install from those files on ubuntu / macos / windows, check the version,
       │       check the hooks are registered, uninstall, check the machine is clean
       ▼
  release      draft → upload → publish, notes taken from this version's CHANGELOG section
       │
       ▼
  install      curl … | bash            → the latest release, verified against SHA256SUMS
               install.sh --version vX.Y.Z → that release
```

## Starting a release

**The button.** Actions → *release* → **Run workflow** → `patch`, `minor` or `major`, and
optionally a one-line summary. That is the whole procedure. The workflow raises the version
everywhere, turns `## [Unreleased]` into `## [X.Y.Z] - <date>`, commits
`chore: prepare vX.Y.Z - <summary>` to main, and continues into the tag and the release.

**A version bump you pushed yourself.** `python scripts/release.py bump minor`, commit, push.
The push only touches `pyproject.toml`, which is what the workflow watches; the new version has
no tag, so it is released.

**A tag.** `git tag -a v0.3.0 -m "…" && git push origin v0.3.0` still works. The tag has to
name the version in `pyproject.toml`, and its commit has to be on main.

Write the release notes **before** the release: they are the `[Unreleased]` section of
`CHANGELOG.md`, and an empty one stops the run. A release without notes is a mistake, not a
formality.

## What cannot go wrong

| The mistake | What stops it |
| --- | --- |
| Releasing the same version twice | the tag exists → `plan` says no; the release exists → publish is a no-op |
| A tag that disagrees with the code | `plan` refuses a tag that does not match `pyproject.toml` |
| Going backwards (an edit, a bad merge) | `plan` refuses a version no higher than the highest released tag |
| Releasing a side branch | `plan` refuses a tag whose commit is not on main |
| Publishing something that does not install | `verify-install` installs and uninstalls it on three OSes first |
| A half-uploaded release page | the release is created as a **draft**, filled, and only then published |
| Two runs racing | `concurrency: release` - the second waits |
| Re-running after a failure, burning a version | the bump job asks `release.py pending` first and releases the prepared version instead of bumping again |
| A tampered download | every asset is listed in `SHA256SUMS`, and the installers check it before installing anything |

## When something fails

**Before the tag is pushed** (bump, plan, package, verify-install, build-binaries): fix the
cause and re-run the workflow. If the version was already prepared on main, the re-run
releases *that* version rather than bumping again.

**After the tag is pushed** (upload or publish failed): re-run the `release` job. It finds the
tag, repairs the draft with `--clobber`, and publishes.

**A published release is wrong:** never move a tag. People and caches already have it. Fix
forward with a patch release (`bump patch`), and if the bad release is dangerous, mark it as a
pre-release or delete the release page - the tag stays.

## The assets, and what each is for

| Asset | Who uses it |
| --- | --- |
| `zerotrace-X.Y.Z-py3-none-any.whl` | what the installers install |
| `zerotrace-X.Y.Z.tar.gz` | sdist, for distributions and offline builds |
| `install.sh`, `install.ps1` | the one-line installers, stamped so they install **this** release |
| `requirements-install.txt` | the hash-locked dependency set the installers use |
| `zerotrace-Linux-X64`, `zerotrace-macOS-ARM64`, `zerotrace-Windows-X64` | single-file binaries for machines with no Python |
| `SHA256SUMS` | verifies every one of the above |

`https://github.com/<owner>/<repo>/releases/latest/download/install.sh` always points at the
newest release's installer, which is why the one-liner in the README needs no version in it.

## Repository settings that back this up

These are GitHub settings, not code, and they are worth setting once:

- **main**: block force-pushes and deletion. No required PR - the release bot pushes
  `chore: prepare …` straight to main, and a required check on direct pushes would reject it.
- **Tags `v*`**: block deletion and updates. Creation stays open so the workflow can tag.
- **Actions**: default workflow permissions read-only; every job that writes asks for it.
- Once the repository is public: turn on secret scanning with push protection, and private
  vulnerability reporting (SECURITY.md already points at it).

## Checking a release by hand

```bash
gh release view v0.3.0
gh release download v0.3.0 --dir /tmp/zt      # then verify and install exactly what CI did
cd /tmp/zt && shasum -a 256 --check SHA256SUMS
bash install.sh --from /tmp/zt --no-model
zerotrace version && zerotrace doctor
zerotrace-uninstall
```
