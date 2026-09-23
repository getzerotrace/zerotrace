#!/usr/bin/env pwsh
<#
ZeroTrace live demo (Windows). Mirrors demo/run_demo.sh scene for scene.

    pwsh -File .\demo\run_demo.ps1          # interactive: pauses between scenes
    pwsh -File .\demo\run_demo.ps1 -Auto    # no pauses (rehearsal)

Fully sandboxed: GIT_CONFIG_GLOBAL and ZEROTRACE_HOME point into the demo dir, so your real
.gitconfig and repos are never touched. Run it from a real terminal (Windows Terminal /
PowerShell), not a redirected one, so the [V/R/U/E/A] prompts work.
#>
param(
    [switch]$Auto,
    [string]$DemoDir = (Join-Path $env:TEMP "zerotrace-demo")
)

$ErrorActionPreference = "Continue"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Model = "qwen2.5-coder:3b-instruct-q4_K_M"

function Scene($text) { Write-Host "`n━━ $text ━━" -ForegroundColor Cyan }
function Say($text) { Write-Host $text -ForegroundColor DarkGray }
function Show($text) { Write-Host "$ $text" -ForegroundColor Green }
function Pause-Demo($label = "continue") { if (-not $Auto) { Read-Host "[enter] $label" | Out-Null } }
function ZT { & $Py -m zerotrace @args }

# Commit; on Windows the hook may not reach the console, so fall back to `zerotrace review`.
function Commit-Demo($message) {
    git commit -m $message
    if ($LASTEXITCODE -eq 0) { return }
    if ($Auto) { Say "(auto mode: commit stays blocked)"; return }
    Say "Fix interactively with: zerotrace review"
    Pause-Demo "run zerotrace review"
    ZT review
    if ($LASTEXITCODE -eq 0) { git commit -m $message }
}

if (-not (Test-Path $Py)) {
    Write-Host "Missing $Py. Set up once with:"
    Write-Host "  python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e `".[dev]`""
    exit 1
}

# ---- sandbox --------------------------------------------------------------------------
if (Test-Path $DemoDir) {
    Get-ChildItem -Path $DemoDir -Recurse -Force | ForEach-Object { $_.Attributes = "Normal" }
    Remove-Item -Recurse -Force $DemoDir
}
New-Item -ItemType Directory -Path $DemoDir | Out-Null
$env:GIT_CONFIG_GLOBAL = Join-Path $DemoDir "gitconfig"
$env:GIT_CONFIG_NOSYSTEM = "1"
$env:ZEROTRACE_HOME = Join-Path $DemoDir "zerotrace-home"
git config --global user.name "Demo Developer"
git config --global user.email "dev@example.test"
git config --global init.defaultBranch main
git config --global core.autocrlf false

Scene "0 · Before: this machine is not protected; the local model is warmed up"
Say "If the model shows as unreachable: docker compose -f docker/docker-compose.yml up -d"
Say "(model: $Model). Without it, MEDIUM findings WARN."
Push-Location $DemoDir; Show "zerotrace doctor --warm"; ZT doctor --warm; Pop-Location

Scene "1 · One install protects every repo on the machine"
Say "No per-repo setup and no .pre-commit-config.yaml: one global git hooks directory."
Pause-Demo "install"
Show "zerotrace install --global"; ZT install --global
Show "git config --global core.hooksPath"; git config --global core.hooksPath

# ---- scene 2: Python service -------------------------------------------------------
Scene "2 · payments-api: hardcoded secrets in code, config, Docker, Terraform, PII"
& $Py (Join-Path $RepoRoot "demo\render_fixtures.py") payments-api (Join-Path $DemoDir "payments-api") --fresh
Set-Location (Join-Path $DemoDir "payments-api"); git init -q
Say "A brand-new repo. Nothing ZeroTrace-specific in it:"
Show "dir -Force"; Get-ChildItem -Force | Select-Object -ExpandProperty Name
git add -A
Show "git diff --cached --stat"; git diff --cached --stat
Pause-Demo "git commit (ZeroTrace steps in)"
Say "Try: [U] on .env · [V] on the Stripe key · [R] on the PII fixture"
Commit-Demo "Add payments service"

# ---- scene 3: Node app ---------------------------------------------------------------
Scene "3 · web-app: a second repo, prompt injection and the AI tie-break"
& $Py (Join-Path $RepoRoot "demo\render_fixtures.py") web-app (Join-Path $DemoDir "web-app") --fresh
Set-Location (Join-Path $DemoDir "web-app"); git init -q
Say "client.js contains a comment telling the AI to allow the key. The key is a"
Say "provider-format OpenAI token, so it blocks deterministically and never reaches the model."
Say "analytics.js holds an ambiguous short token, which is decided by the local model."
git add -A
Pause-Demo "git commit"
Commit-Demo "Add web client"

# ---- scene 4: bypass -> pre-push backstop ---------------------------------------------
Scene "4 · Someone bypasses the hook with --no-verify"
Set-Location (Join-Path $DemoDir "payments-api")
$Origin = Join-Path $DemoDir "origin.git"
git init -q --bare $Origin
git remote add origin $Origin 2>$null
git rev-parse -q --verify HEAD *> $null
if ($LASTEXITCODE -ne 0) { git reset -q; git commit -q --no-verify --allow-empty -m "init" }
git push -q -u origin main 2>$null
& $Py (Join-Path $RepoRoot "demo\render_fixtures.py") sneaky (Join-Path $DemoDir "payments-api\scripts")
git add scripts
Show "git commit --no-verify -m `"quick deploy script`""; git commit --no-verify -m "quick deploy script"
Pause-Demo "git push"
Show "git push origin main"; git push origin main
if ($LASTEXITCODE -ne 0) { Say "↑ push blocked before the secret left the laptop" }

# ---- scene 5: trust -------------------------------------------------------------------
Scene "5 · Trust: doctor + audit log (fingerprints only, never values)"
Show "zerotrace doctor"; ZT doctor
Say "Last audit entries (hash-chained, stored inside .git so nothing is ever committed):"
$log = Join-Path $DemoDir "payments-api\.git\zerotrace\audit.log.jsonl"
if (Test-Path $log) { Get-Content $log -Tail 3 | ForEach-Object { $_.Substring(0, [Math]::Min(200, $_.Length)) } }

Scene "Done"
Say "Sandbox: $DemoDir   (your real git config was never modified)"
