#requires -Version 5.1
<#
Intune Win32 app install script for ZeroTrace (machine-wide, every user on the laptop).

Packaging:
  1. Put this script, install.ps1 (repo root) and policy.yml (a copy of policy.example.yml) in
     one folder.
  2. Wrap it with the Microsoft Win32 Content Prep Tool (IntuneWinAppUtil.exe) into a .intunewin.
  3. In Intune > Apps > Windows > Add (Win32 app):
       Install command:   powershell.exe -NoProfile -ExecutionPolicy Bypass -File intune-install.ps1
       Uninstall command: powershell.exe -NoProfile -ExecutionPolicy Bypass -File intune-uninstall.ps1
       Detection rule:    file exists -> %ProgramData%\zerotrace\policy.yml
  Intune runs Win32 app installs as SYSTEM by default, which is what `zerotrace install --system`
  needs to protect every user's repos on the machine. See docs/DEPLOYMENT.md §3.
#>
$ErrorActionPreference = "Stop"

# 1. Install the tool (signed single-file binary once published; falls back to pip --user).
$installer = Join-Path $PSScriptRoot "install.ps1"
if (Test-Path $installer) {
    & $installer
} else {
    (New-Object System.Net.WebClient).DownloadString(
        "https://raw.githubusercontent.com/getzerotrace/zerotrace/main/install.ps1") | Invoke-Expression
}

# 2. Machine-wide git hook: every user's repos on this laptop are now protected.
zerotrace install --system

# 3. Locked org policy (block_severity, model endpoint, etc. can't be weakened per-repo).
$policyDir = Join-Path $env:ProgramData "zerotrace"
New-Item -ItemType Directory -Force -Path $policyDir | Out-Null
$policySrc = Join-Path $PSScriptRoot "policy.yml"
if (Test-Path $policySrc) {
    Copy-Item -Path $policySrc -Destination (Join-Path $policyDir "policy.yml") -Force
} else {
    Write-Warning "policy.yml not found next to this script; ship one alongside it (see policy.example.yml)."
}
