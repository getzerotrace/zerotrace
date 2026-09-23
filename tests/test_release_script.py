"""scripts/release.py: the version bump, the CI release plan, notes and tag messages.

Each test works on a copy of the real version files and CHANGELOG, so the formats it checks
are the ones the release workflow will meet.
"""
import importlib.util
import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_FILES = ("pyproject.toml", "src/zerotrace/__init__.py", "skill/skill.json",
          "marketplace/submission.json", "CHANGELOG.md")


def _load():
    spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load()


@pytest.fixture
def project(tmp_path) -> Path:
    """The version files and CHANGELOG, copied, with one note waiting under [Unreleased]."""
    for name in _FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    changelog = tmp_path / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    body, end = release._unreleased_span(text)
    changelog.write_text(text[:body] + "### Fixed\n- A fix worth releasing.\n\n" + text[end:],
                         encoding="utf-8")
    return tmp_path


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(root: Path) -> Path:
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@example.test", "-c", "user.name=T", "add", "-A")
    _git(root, "-c", "user.email=t@example.test", "-c", "user.name=T", "commit", "-qm", "base")
    return root


@pytest.mark.parametrize("how,expected", [("patch", "1.4.3"), ("minor", "1.5.0"),
                                          ("major", "2.0.0"), ("1.10.0", "1.10.0"),
                                          ("v2.0.0", "2.0.0")])
def test_next_version(how, expected):
    assert release.next_version("1.4.2", how) == expected


@pytest.mark.parametrize("how", ["1.4.2", "1.4.1", "0.9.9", "1.5", "banana"])
def test_a_version_that_does_not_move_forward_is_refused(how):
    with pytest.raises(release.ReleaseError):
        release.next_version("1.4.2", how)


def test_bump_writes_one_version_everywhere_and_cuts_the_changelog(project):
    old = release.current_version(project)
    new = release.bump("minor", root=project, today="2026-10-01")
    assert new == release.next_version(old, "minor")

    def read(name: str) -> str:
        return (project / name).read_text(encoding="utf-8")

    assert tomllib.loads(read("pyproject.toml"))["project"]["version"] == new
    assert f'__version__ = "{new}"' in read("src/zerotrace/__init__.py")
    for manifest in ("skill/skill.json", "marketplace/submission.json"):
        assert json.loads(read(manifest))["version"] == new, manifest

    changelog = (project / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [Unreleased]\n\n## [{new}] - 2026-10-01\n### Fixed\n- A fix worth releasing." in changelog
    # Read, not repeated: the project has been renamed once already, and a test that carries
    # its own copy of the URL fails for a reason that has nothing to do with the release.
    repo = release._repo_url(project)
    assert f"[Unreleased]: {repo}/compare/v{new}...HEAD" in changelog
    assert f"[{new}]: {repo}/compare/v{old}...v{new}" in changelog
    assert f"## [{old}] - " in changelog, "earlier sections are kept"


def test_bump_changes_only_the_version_lines(project):
    before = {name: (project / name).read_text(encoding="utf-8").splitlines()
              for name in _FILES[:-1]}
    release.bump("patch", root=project, today="2026-10-01")
    for name, lines in before.items():
        after = (project / name).read_text(encoding="utf-8").splitlines()
        changed = [pair for pair in zip(lines, after, strict=True) if pair[0] != pair[1]]
        assert len(changed) == 1, (name, changed)


def test_bump_refuses_empty_release_notes_and_leaves_every_file_alone(project):
    changelog = project / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    body, end = release._unreleased_span(text)
    changelog.write_text(text[:body] + "\n" + text[end:], encoding="utf-8")
    snapshot = {name: (project / name).read_text(encoding="utf-8") for name in _FILES}
    with pytest.raises(release.ReleaseError, match="empty"):
        release.bump("patch", root=project)
    assert {name: (project / name).read_text(encoding="utf-8") for name in _FILES} == snapshot


def test_plan_releases_a_version_that_has_no_tag_yet(project):
    root = _repo(project)
    version = release.current_version(root)
    decision = release.plan(env={}, root=root)
    assert decision == {"release": "true", "version": version, "tag": f"v{version}"}


def test_plan_skips_a_version_that_is_already_tagged(project):
    root = _repo(project)
    _git(root, "tag", f"v{release.current_version(root)}")
    assert release.plan(env={}, root=root)["release"] == "false"


def test_plan_accepts_a_pushed_tag_only_when_it_matches_the_version(project):
    root = _repo(project)
    tag = f"v{release.current_version(root)}"
    assert release.plan({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": tag}, root)["release"] == "true"
    with pytest.raises(release.ReleaseError, match="does not match"):
        release.plan({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v9.9.9"}, root)


def test_plan_refuses_a_release_without_a_changelog_section(project):
    pyproject = project / "pyproject.toml"
    current = f'version = "{release.current_version(project)}"'
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace(current, 'version = "9.9.9"', 1),
                         encoding="utf-8")
    root = _repo(project)
    with pytest.raises(release.ReleaseError, match="no '## \\[9.9.9\\]' section"):
        release.plan(env={}, root=root)


def test_notes_are_exactly_that_version_s_section(project):
    version = release.current_version(project)
    text = release.notes(f"v{version}", root=project)
    assert text.startswith(f"## [{version}] - ")
    assert "## [Unreleased]" not in text and "/compare/" not in text
    assert text.count("\n## [") == 0, "stops before the previous version"


def test_tag_message_takes_the_summary_from_the_prepare_commit(project):
    root = _repo(project)
    ident = ("-c", "user.email=t@example.test", "-c", "user.name=T")
    _git(root, *ident, "commit", "-q", "--allow-empty", "-m", "chore: prepare v1.2.3 - faster scans")
    sha = _git(root, "rev-parse", "HEAD")
    assert release.tag_message("v1.2.3", sha, root) == "v1.2.3 - faster scans"
    _git(root, *ident, "commit", "-q", "--allow-empty", "-m", "fix: something else")
    assert release.tag_message("v1.2.3", _git(root, "rev-parse", "HEAD"), root) == "v1.2.3"


def _tag(root: Path, name: str) -> None:
    _git(root, "-c", "user.email=t@example.test", "-c", "user.name=T", "tag", "-a", name,
         "-m", name)


def test_a_version_lower_than_one_already_released_is_refused(project):
    """Someone edits pyproject.toml by hand, or a bad merge brings an old version back. That
    version has no tag, so the old rule ("untagged means release it") would publish it and
    overwrite the meaning of a number people already have."""
    root = _repo(project)
    _tag(root, "v9.9.9")
    with pytest.raises(release.ReleaseError, match="not higher than the released v9.9.9"):
        release.plan({}, root=root)


def test_a_tag_on_a_side_branch_is_refused(project):
    """Releases are cut from main, so what ships is what was reviewed there."""
    root = _repo(project)
    _git(root, "branch", "-M", "main")
    _git(root, "checkout", "-q", "-b", "side")
    (root / "extra.txt").write_text("not on main\n", encoding="utf-8")
    _git(root, "-c", "user.email=t@example.test", "-c", "user.name=T", "add", "-A")
    _git(root, "-c", "user.email=t@example.test", "-c", "user.name=T", "commit", "-qm", "side")
    version = release.current_version(root)
    env = {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": f"v{version}"}
    with pytest.raises(release.ReleaseError, match="not on main"):
        release.plan(env, root=root)


def test_a_tag_on_main_is_accepted(project):
    root = _repo(project)
    _git(root, "branch", "-M", "main")
    version = release.current_version(root)
    env = {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": f"v{version}"}
    assert release.plan(env, root=root)["release"] == "true"


def test_pending_says_when_a_prepared_version_has_no_tag_yet(project):
    """What makes re-running the whole release workflow safe: the bump job asks this first and
    releases the prepared version instead of burning another number."""
    root = _repo(project)
    version = release.current_version(root)
    assert release.pending(root)["pending"] == "true"
    _tag(root, f"v{version}")
    assert release.pending(root)["pending"] == "false"


@pytest.fixture
def installers(project) -> Path:
    """A copy of the repository that has the two installers in it, so stamping can write into
    it without the tests touching the real checkout."""
    for name in ("install.sh", "install.ps1"):
        shutil.copy(ROOT / name, project / name)
    return project


def test_stamp_writes_the_version_into_installer_copies(installers):
    written = release.stamp("v1.2.3", "dist", root=installers)
    assert {path.name for path in written} == {"install.sh", "install.ps1"}
    out = installers / "dist"
    assert 'RELEASE_VERSION="v1.2.3"' in (out / "install.sh").read_text(encoding="utf-8")
    assert '$ReleaseVersion = "v1.2.3"' in (out / "install.ps1").read_text(encoding="utf-8")
    # The originals are placeholders still: a clone installs from itself, not from a release.
    assert 'RELEASE_VERSION=""' in (ROOT / "install.sh").read_text(encoding="utf-8")


def test_a_stamped_installer_is_still_executable(installers):
    """It is downloaded and run; losing the bit turns `./install.sh` into "permission denied"."""
    import os
    written = release.stamp("v1.2.3", "dist", root=installers)
    shell = [path for path in written if path.name == "install.sh"][0]
    assert os.access(shell, os.X_OK)


def test_stamping_refuses_a_version_that_is_not_a_tag(installers):
    with pytest.raises(release.ReleaseError):
        release.stamp("latest", "dist", root=installers)


@pytest.mark.parametrize("escape", ["../outside", "../../../../tmp/evil", "/tmp/evil",
                                    "dist/../../outside"])
def test_stamping_refuses_to_write_outside_the_repository(project, escape):
    """The destination comes from the command line; it must not be a way to write files
    anywhere on the machine."""
    with pytest.raises(release.ReleaseError, match="outside the repository"):
        release.stamp("v1.2.3", escape, root=project)


def test_a_symlink_that_leaves_the_repository_is_refused(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere")
    link = project / "dist"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(release.ReleaseError, match="outside the repository"):
        release.stamp("v1.2.3", "dist", root=project)


def test_a_relative_destination_lands_inside_the_repository(installers):
    written = release.stamp("v1.2.3", "dist", root=installers)
    assert all(str(path).startswith(str(installers.resolve())) for path in written)
    assert (installers / "dist" / "install.sh").exists()


@pytest.mark.parametrize("bad_sha", [
    "HEAD; rm -rf /",            # a shell metacharacter, in case a caller ever uses a shell
    "--upload-pack=touch /tmp/x",  # an option, not a revision
    "$(whoami)",
    "main",                      # a ref, not an object name: refs can be made to point anywhere
    "",
])
def test_a_commit_that_is_not_a_hex_object_name_never_reaches_git(project, bad_sha):
    """`tag-message` is called with values that came from the workflow's own input box, so
    everything it hands to git is rebuilt from a strict pattern first."""
    with pytest.raises(release.ReleaseError):
        release.tag_message("v1.2.3", bad_sha, root=project)


@pytest.mark.parametrize("bad_tag", ["v1.2", "1.2.3", "v1.2.3 --exec=id", "../../etc/passwd",
                                     "v1.2.3\nv1.2.4"])
def test_a_tag_that_is_not_a_version_is_refused(project, bad_tag):
    with pytest.raises(release.ReleaseError):
        release.tag_message(bad_tag, "0" * 40, root=project)
    with pytest.raises(release.ReleaseError):
        release.notes(bad_tag, root=project)


def test_a_checked_value_is_the_rebuilt_match_not_the_original(project):
    """Rebuilding is the point: the validated string is a new object, so a tainted one cannot
    be passed through by a later refactor that forgets to re-validate."""
    assert release.checked_sha("  " + "a" * 40 + "  ") == "a" * 40
    assert release.checked_tag(" v1.2.3 ") == "v1.2.3"


def test_the_real_changelog_notes_for_the_current_version_are_extractable():
    """What the next run of the release workflow will read from this checkout."""
    version = release.current_version()
    assert release.notes(f"v{version}").startswith(f"## [{version}] - ")
