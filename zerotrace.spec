# PyInstaller spec for the zerotrace CLI: a single-file binary that needs no Python on the
# target machine. Same command on every OS (path separators, exe suffix etc. are handled here,
# not on the command line):
#
#   pyinstaller zerotrace.spec
#
# Output: dist/zerotrace (Linux/macOS) or dist/zerotrace.exe (Windows).
import sys

from PyInstaller.utils.hooks import collect_data_files

# Bundles detectors/rules/default.yml and evals/classifier_cases.jsonl (see the "wheel must
# carry the rule pack and eval cases" checks in ci.yml / release.yml) alongside the compiled code.
datas = collect_data_files("zerotrace")

# The compose file that `zerotrace model up` runs. The wheel gets it through
# force-include (pyproject.toml); an editable install - which is what the release job builds
# the binary from - keeps it at docker/, so name it here or the binary cannot start the model.
datas += [
    ("docker/docker-compose.yml", "zerotrace/docker"),
    ("docker/ollama-entrypoint.sh", "zerotrace/docker"),
]

a = Analysis(
    ["scripts/pyinstaller_entry.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=["detect_secrets.plugins"],
    hookspath=[],
    runtime_hooks=[],
    # The binary is the guardrail: hook, scan, doctor. The release job installs `.[dev]`, which
    # now pulls in textual, and PyInstaller would follow the lazy `ui.tui*` imports and bundle
    # it half-working (textual.widgets loads its modules dynamically). Leave it out, so the
    # binary's `review` takes the inline flow and `-i` falls back to the plain report.
    excludes=["textual"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="zerotrace.exe" if sys.platform == "win32" else "zerotrace",
    console=True,
)
