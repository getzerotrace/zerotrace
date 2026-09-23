"""One version everywhere. `zerotrace version`, the wheel, the skill manifests and the release
tag must agree: release.yml refuses a tag that differs from pyproject.toml, and before 0.2.0
`__version__` (0.2.0) and pyproject.toml (0.1.0) had already drifted apart."""
import json
import re
import tomllib
from pathlib import Path

import zerotrace

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_the_package_reports_the_pyproject_version():
    assert zerotrace.__version__ == _pyproject_version()


def test_the_skill_manifests_carry_the_same_version():
    for manifest in ("skill/skill.json", "marketplace/submission.json"):
        data = json.loads((ROOT / manifest).read_text(encoding="utf-8"))
        assert data["version"] == _pyproject_version(), manifest


def test_the_changelog_has_a_section_for_the_current_version():
    """release.yml takes the release notes from `## [X.Y.Z]`; without it they are a stub."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = re.escape(_pyproject_version())
    assert re.search(rf"^## \[{version}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE)
    assert re.search(rf"^\[{version}\]: https://", changelog, re.MULTILINE), "compare link"
