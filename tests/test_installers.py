"""Properties of the two bootstrap installers that are invisible in a diff.

They cannot import anything from the package - they run before Python exists - so the colours
and the logo they draw are copies. These tests fail when a copy drifts, which is the only way
to keep one brand across three languages.
"""
import re
import subprocess
from pathlib import Path

import pytest

from zerotrace.ui import logo, theme

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "install.sh"
PS1 = ROOT / "install.ps1"


def _index_mode(filename: str) -> str:
    """The mode git RECORDS, not the one this checkout happens to have: a fresh clone gets the
    former, and a Windows working tree has no POSIX bit for os.access to read."""
    done = subprocess.run(["git", "ls-files", "-s", "--", filename], cwd=ROOT,
                          capture_output=True, text=True, check=False)
    if done.returncode != 0 or not done.stdout.strip():
        pytest.skip(f"{filename} is not tracked here")
    return done.stdout.split()[0]


def test_install_sh_is_committed_executable():
    """`./install.sh` is the documented way to install from a clone, and it is what the
    release job and the e2e tests run."""
    assert _index_mode("install.sh") == "100755", (
        "restore it with `git update-index --chmod=+x install.sh`")


def test_install_sh_has_no_crlf():
    r"""A `\r` in the shebang is "/usr/bin/env bash\r: not found", the single most confusing
    way a shell script can fail."""
    assert b"\r\n" not in SH.read_bytes()


def test_install_sh_is_valid_bash():
    """Parsed by a real bash, wherever one exists.

    On the Windows runner `bash` is the WSL launcher stub, which exits 1 without running
    anything when no distribution is installed - a failure about the machine, not about the
    script. So the shell is proven to work before it is trusted to judge; the Linux and macOS
    jobs are where this check really runs.
    """
    if subprocess.run(["bash", "-c", "exit 0"], capture_output=True).returncode != 0:
        pytest.skip("no working bash on this machine (the Windows runner's is a WSL stub)")
    done = subprocess.run(["bash", "-n", str(SH)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_powershell_installer_is_ascii():
    """Windows PowerShell 5.1 reads a non-ASCII .ps1 as the ANSI code page unless it carries a
    BOM, which turns the logo into mojibake on the one console this script exists for."""
    PS1.read_text(encoding="ascii")      # raises if anything is not ASCII


@pytest.mark.parametrize("script", [SH, PS1])
def test_both_installers_carry_the_same_accent_ramp(script):
    text = script.read_text(encoding="utf-8")
    for red, green, blue in theme.RAMP_RGB:
        assert f"{red};{green};{blue}" in text, f"{script.name} is missing ramp stop {red},{green},{blue}"
    assert "{};{};{}".format(*theme.ACCENT_RGB) in text


def test_the_shell_installer_draws_the_real_mark():
    """The wordmark and the mark are pasted into the shell scripts; if the asset is
    regenerated and they are not, the installer shows the old logo forever."""
    expected = logo._inverted_mask(logo._mark_lines("mark.small.uni.txt"))
    text = SH.read_text(encoding="utf-8")
    for line in expected:
        assert line in text, "install.sh no longer matches ui/assets/mark.small.uni.txt"


def test_the_powershell_installer_draws_the_real_mark():
    """Encoded F/T/B, because the file has to stay ASCII - decode it and compare."""
    expected = logo._inverted_mask(logo._mark_lines("mark.small.uni.txt"))
    text = PS1.read_text(encoding="ascii")
    block = re.search(r"\$mark = @\((.*?)\)\n", text, re.DOTALL)
    assert block, "the mark array is gone from install.ps1"
    rows = re.findall(r"'([FTB ]+)'", block.group(1))
    decoded = [row.replace("F", "█").replace("T", "▀").replace("B", "▄")
               for row in rows]
    assert decoded == expected, "install.ps1 no longer matches ui/assets/mark.small.uni.txt"


def test_both_installers_draw_the_same_wordmark():
    wordmark = logo._block_wordmark()
    sh_text = SH.read_text(encoding="utf-8")
    ps_text = PS1.read_text(encoding="ascii")
    for line in wordmark:
        assert line.rstrip() in sh_text
        assert line.rstrip().replace("█", "#") in ps_text


@pytest.mark.parametrize("flag", ["--uninstall", "--purge", "--version", "--ref", "--from",
                                  "--no-model", "--with-pii", "--local"])
def test_every_shell_flag_is_documented_in_its_own_help(flag):
    text = SH.read_text(encoding="utf-8")
    usage = text[text.index("usage() {"):text.index("USAGE\n}")]
    assert flag in usage, f"{flag} is accepted but not in --help"


def test_the_two_installers_offer_the_same_options():
    """A Windows user reading the README must not find a flag that only exists on macOS."""
    sh_text = SH.read_text(encoding="utf-8")
    ps_text = PS1.read_text(encoding="ascii")
    pairs = {"--uninstall": "$Uninstall", "--purge": "$Purge", "--version": "$Version",
             "--ref": "$Ref", "--from": "$From", "--no-model": "$NoModel",
             "--with-pii": "$WithPii", "--local": "$Local"}
    for shell_flag, powershell_flag in pairs.items():
        assert shell_flag in sh_text
        assert powershell_flag in ps_text, f"{powershell_flag} is missing from install.ps1"


def test_neither_installer_writes_into_the_system_python():
    """PEP 668 refuses `pip install --user` on a current Python, and the previous version of
    these scripts did exactly that. The environment is ours; nothing else is touched."""
    for script in (SH, PS1):
        # Comments are skipped: both scripts explain at the top WHY they no longer do this.
        text = re.sub(r"<#.*?#>", "", script.read_text(encoding="utf-8"), flags=re.DOTALL)
        code = "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith(("#", "rem ")))
        assert "pip install --user" not in code
        assert '"--user"' not in code, f"{script.name} installs into the user site"


def test_the_installers_verify_what_they_download():
    for script in (SH, PS1):
        text = script.read_text(encoding="utf-8")
        assert "SHA256SUMS" in text
        assert "--require-hashes" in text, f"{script.name} installs dependencies unpinned"


def test_the_release_version_placeholder_is_where_the_release_job_expects_it():
    """scripts/release.py stamps these lines, so the copy attached to a release installs that
    release rather than whatever is newest."""
    assert re.search(r'^RELEASE_VERSION=""$', SH.read_text(encoding="utf-8"), re.MULTILINE)
    assert re.search(r'^\$ReleaseVersion = ""$', PS1.read_text(encoding="ascii"), re.MULTILINE)
