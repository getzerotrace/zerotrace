"""The lock files every install uses must still match pyproject.toml.

`scripts/lock_deps.py` writes them. This catches a dependency added to or raised in
pyproject.toml without re-running it: CI would otherwise keep testing the old version, or
install without the new package at all.
"""
import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("packaging", reason="packaging reads the requirement specifiers")
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", re.MULTILINE)


def _locks() -> dict:
    """LOCKS from scripts/lock_deps.py, so the test and the generator agree on what is built."""
    spec = importlib.util.spec_from_file_location("lock_deps", ROOT / "scripts" / "lock_deps.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LOCKS


def _pins(lock: str) -> dict[str, list[str]]:
    pins: dict[str, list[str]] = {}
    for name, version in _PIN.findall((ROOT / "requirements" / lock).read_text(encoding="utf-8")):
        pins.setdefault(canonicalize_name(name), []).append(version)
    return pins


def _wanted(extras: list[str], inputs: list[str]) -> list[Requirement]:
    """Everything a lock is built from, with the project's own extras (dev -> tui) expanded."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    optional = project["optional-dependencies"]
    pending = [*project["dependencies"], *(text for extra in extras for text in optional[extra])]
    for source in inputs:
        for line in (ROOT / "requirements" / source).read_text(encoding="utf-8").splitlines():
            if line.split("#")[0].strip():
                pending.append(line.split("#")[0].strip())
    expanded, wanted = set(extras), []
    while pending:
        requirement = Requirement(pending.pop())
        if canonicalize_name(requirement.name) == canonicalize_name(project["name"]):
            for extra in requirement.extras - expanded:
                expanded.add(extra)
                pending.extend(optional[extra])
            continue
        wanted.append(requirement)
    return wanted


@pytest.mark.parametrize("lock", sorted(_locks()))
def test_each_lock_satisfies_what_it_is_built_from(lock):
    extras, inputs = _locks()[lock]
    pins = _pins(lock)
    for requirement in _wanted(extras, inputs):
        versions = pins.get(canonicalize_name(requirement.name), [])
        assert versions, (f"{requirement.name} is missing from requirements/{lock}: "
                          "run python scripts/lock_deps.py")
        assert any(requirement.specifier.contains(v, prereleases=True) for v in versions), (
            f"requirements/{lock} pins {requirement.name}=={versions}, which does not satisfy "
            f"{requirement}: run python scripts/lock_deps.py")


@pytest.mark.parametrize("lock", sorted(_locks()))
def test_every_pin_carries_a_hash(lock):
    """pip's --require-hashes refuses the file otherwise; say so here, with the file named."""
    text = (ROOT / "requirements" / lock).read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text[text.index("\n", text.index("#    ")) + 1:])
    unhashed = [block.split()[0] for block in blocks if block.strip() and "--hash=sha256:" not in block]
    assert unhashed == []
