#requires -Version 5.1
<#
.SYNOPSIS
  Internal contributor bootstrap for ZeroTrace (private/org-only repo).

.DESCRIPTION
  This repo is not public yet, so the iwr-to-raw-URL one-liner in README.md
  doesn't work anonymously. Run this instead, from a checkout you already
  have access to via your own git credentials:

    git clone https://github.com/getzerotrace/zerotrace.git
    cd zerotrace
    .\scripts\dev_bootstrap.ps1

  Sets up .venv, installs the locked dev dependencies and zerotrace (editable), enables the
  global git hook on this machine, runs doctor, then runs the full test
  suite - so both contributors can confirm a checkout is healthy the same way.
#>
$ErrorActionPreference = "Stop"

function Write-Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "!! $msg" -ForegroundColor Yellow }

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pyCmd = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
if (-not (Get-Command $pyCmd -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: no python found. See docs/INSTALL.md" -ForegroundColor Red
    exit 1
}
Write-Info "Using $(& $pyCmd --version)"

if (-not (Test-Path ".venv")) {
    Write-Info "Creating .venv"
    & $pyCmd -m venv .venv
}
& .\.venv\Scripts\Activate.ps1

Write-Info "Installing the locked dev dependencies, then zerotrace (editable)"
# The same install CI runs: exact, hash-checked wheels from requirements\dev.txt (so nothing runs
# setup code while installing), then this checkout with no index and no build isolation.
python -m pip install --quiet --require-hashes --only-binary :all: -r requirements/dev.txt
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: installing requirements/dev.txt failed" -ForegroundColor Red; exit 1 }
python -m pip install --quiet --no-deps --no-build-isolation --no-index --only-binary :all: -e .
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: installing zerotrace failed" -ForegroundColor Red; exit 1 }

Write-Info "Enabling the global git hook"
try { zerotrace install --global } catch { Write-Warn "install --global reported an issue, see output above" }

Write-Info "Running doctor"
try { zerotrace doctor } catch {}

Write-Info "Running the test suite"
pytest -q
