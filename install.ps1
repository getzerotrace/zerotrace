#requires -Version 5.1
<#
.SYNOPSIS
  ZeroTrace installer for Windows.

.DESCRIPTION
  irm https://raw.githubusercontent.com/getzerotrace/zerotrace/main/install.ps1 | iex

  Does what install.sh does, in the same six steps and with the same layout on disk: checks
  the machine, builds a virtualenv that belongs to ZeroTrace alone, then hands over to
  `zerotrace setup` for the Docker check, the git hooks, the local model and a self-test that
  proves a staged secret is really refused.

  It never installs into your system Python and never needs an elevated prompt. That is not
  tidiness: `pip install --user` is refused by a current Python under PEP 668, which is how
  the previous version of this script failed.

  Windows PowerShell 5.1 AND PowerShell 7: no ternaries, no `??`, no `-AsByteStream`. 5.1 is
  still the default on a stock Windows 11, and a script that only runs in 7 does not run for
  the people most likely to paste it.

.PARAMETER Uninstall
  Remove everything this script installed. `zerotrace-uninstall` does the same.

.PARAMETER Purge
  With -Uninstall: also delete the model image and the downloaded weights (gigabytes).

.PARAMETER Version
  Install a particular release, e.g. -Version v0.3.0. Default: the latest published release.

.PARAMETER Ref
  Build from a git branch or tag instead - for development.

.PARAMETER From
  Install from release files already downloaded (air-gapped machines, and CI).

.PARAMETER NoModel
  Skip the Docker and model step. The guardrail is installed either way.

.EXAMPLE
  irm https://raw.githubusercontent.com/getzerotrace/zerotrace/main/install.ps1 | iex

.EXAMPLE
  # Options need the script on disk - `iex` cannot pass arguments.
  irm https://raw.githubusercontent.com/getzerotrace/zerotrace/main/install.ps1 -OutFile i.ps1
  .\i.ps1 -Version v0.3.0

.NOTES
  Under a Restricted execution policy `irm | iex` is refused. That is the policy working; the
  way past it for one command is:
      powershell -ExecutionPolicy Bypass -Command "irm <url> | iex"
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Purge,
    [string]$Version = "",
    [string]$Ref = $(if ($env:ZEROTRACE_REF) { $env:ZEROTRACE_REF } else { "" }),
    [string]$From = "",
    [switch]$Local,
    [switch]$NoModel,
    [switch]$WithPii,
    [switch]$WithLlm,
    [switch]$Ascii,
    [switch]$Plain
)

$ErrorActionPreference = "Stop"
# A native command writing to stderr is a native command talking, not a failure. Without this,
# `docker info` on a machine with no daemon - or pip writing one progress line - ends the
# script with an exception in the middle of an install.
$PSNativeCommandUseErrorActionPreference = $false

$Repo = $(if ($env:ZEROTRACE_REPO_URL) { $env:ZEROTRACE_REPO_URL }
          else { "https://github.com/getzerotrace/zerotrace" })
$HomeDir = $(if ($env:ZEROTRACE_HOME) { $env:ZEROTRACE_HOME }
             else { Join-Path $env:USERPROFILE ".zerotrace" })
$BinDir = $(if ($env:ZEROTRACE_BIN_DIR) { $env:ZEROTRACE_BIN_DIR } else { Join-Path $HomeDir "bin" })
$VenvDir = Join-Path $HomeDir "venv"
$LogFile = Join-Path $HomeDir "install.log"
$PyMinMajor = 3
$PyMinMinor = 11
$TotalSteps = 6                  # two here, four in `zerotrace setup`
$Marker = "# added by ZeroTrace installer"
# Stamped by scripts/release.py into the copy attached to each release.
$ReleaseVersion = ""

$Extras = $(if ($env:ZEROTRACE_EXTRAS) { $env:ZEROTRACE_EXTRAS } else { "" })
if ($WithPii) { $Extras = $(if ($Extras) { "$Extras,pii-ner" } else { "pii-ner" }) }

# --- presentation ------------------------------------------------------------------------
# Two independent questions, the same two install.sh asks: may I use colour (a console, and
# NO_COLOR unset), and can this console render block characters (its output encoding).
$script:Tty = $false
try { $script:Tty = (-not [Console]::IsOutputRedirected) -and (-not $env:NO_COLOR) }
catch { $script:Tty = $false }

$script:Vt = $false
try { $script:Vt = $script:Tty -and [bool]$Host.UI.SupportsVirtualTerminal } catch { $script:Vt = $false }
$E = [char]27

function Test-BlockGlyphs {
    # Asked by round-tripping the characters through the active encoding rather than by
    # comparing code pages: cp437 renders these perfectly and cp1252 cannot, and the code page
    # number alone does not tell you which you have.
    try {
        $enc = [Console]::OutputEncoding
        $probe = [string][char]0x2588 + [string][char]0x2591
        return ($enc.GetString($enc.GetBytes($probe)) -eq $probe)
    } catch { return $false }
}

$script:PrevEncoding = $null
$script:Unicode = $false
if ($script:Tty -and -not ($Ascii -or $env:ZEROTRACE_ASCII)) {
    if (Test-BlockGlyphs) {
        $script:Unicode = $true
    } else {
        try {
            $script:PrevEncoding = [Console]::OutputEncoding
            [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
            $script:Unicode = Test-BlockGlyphs
            if (-not $script:Unicode -and $null -ne $script:PrevEncoding) {
                [Console]::OutputEncoding = $script:PrevEncoding
                $script:PrevEncoding = $null
            }
        } catch { $script:Unicode = $false }
    }
}

# The accent from src/zerotrace/ui/theme.py. tests/test_theme.py fails if these drift.
$AccentRgb = "5;150;105"
$RampRgb = @("17;94;89", "17;102;93", "17;111;96", "17;119;100", "17;127;104", "17;135;107",
             "16;144;111", "16;152;115", "16;160;118", "16;169;122", "16;177;126", "16;185;129")
$Accent = $(if ($script:Vt) { "$E[1;38;2;${AccentRgb}m" } else { "" })
$Reset  = $(if ($script:Vt) { "$E[0m" } else { "" })
$Dim    = $(if ($script:Vt) { "$E[2m" } else { "" })
$Bold   = $(if ($script:Vt) { "$E[1m" } else { "" })

$Tick = $(if ($script:Unicode) { [char]0x2713 } else { "ok" })
$Cross = $(if ($script:Unicode) { [char]0x2717 } else { "xx" })
$Arrow = $(if ($script:Unicode) { [char]0x2192 } else { "->" })
$Full = $(if ($script:Unicode) { [char]0x2588 } else { "#" })
$Empty = $(if ($script:Unicode) { [char]0x2591 } else { "-" })

function Restore-Console {
    if ($null -ne $script:PrevEncoding) {
        try { [Console]::OutputEncoding = $script:PrevEncoding } catch { }
        $script:PrevEncoding = $null
    }
}

function Write-Banner {
    if (-not $script:Tty) { Write-Host "ZeroTrace installer"; return }
    Write-Host ""
    if ($script:Unicode) {
        # The same mark `zerotrace install` prints (src/zerotrace/ui/assets/mark.small.uni.txt,
        # inverted so the cut-outs that carry the face stay unpainted).
        # Encoded F/T/B (full, upper half, lower half) rather than written with the block
        # characters themselves: a .ps1 that is not ASCII has to carry a BOM for Windows
        # PowerShell 5.1 to read it correctly, and a file that renders as mojibake on the one
        # console this script exists for is not worth the shorter source.
        # tests/test_installers.py checks this against ui/assets/mark.small.uni.txt.
        $mark = @(
            'FFFTFFFFFFFFFFFFFFFFFFTFFF', 'FF  TFFFFFFFTFFFFFFFFT TFF',
            'FF   TFFFFT    TFFFFT   FF', 'FF  TBB      BFB   BF  FFF',
            'FFFB  T      FFFF TT  BFFF', 'FFFFF       FFTTFF  BFFFFF',
            'FFFFF       F       FFFFFF', 'FFFFT    B    BBB   TFFFFF',
            'FFFFB    T    TT     BFFFF', 'FFFF       TTFB B    FFFFF',
            'FFFFBB       FTTF  BBFFFFF', 'FFFFFT       T  BB TFFFFFF',
            'FFFFFFB     BFFTT BFFFFFFF', 'FFFFFFFFBBBBBBBBFFFFFFFFFF')
        foreach ($row in $mark) {
            $line = $row.Replace('F', [string][char]0x2588).Replace('T', [string][char]0x2580)
            $line = $line.Replace('B', [string][char]0x2584)
            Write-Host ("  " + $Accent + $line + $Reset)
        }
        Write-Host ""
    }
    $wordmark = @(
        '#####  #####  ####   #####  #####  ####    ###   #####  #####',
        '   ##  ##     ## ##  ## ##    ##   ## ##  ## ##  ##     ##',
        '  ##   ####   ####   ## ##    ##   ####   #####  ##     ####',
        ' ##    ##     ## ##  ## ##    ##   ## ##  ## ##  ##     ##',
        '#####  #####  ## ##  #####    ##   ## ##  ## ##  #####  #####')
    foreach ($row in $wordmark) {
        $line = $(if ($script:Unicode) { $row.Replace('#', [string][char]0x2588) } else { $row })
        Write-Host ("  " + $Accent + $line + $Reset)
    }
    Write-Host ""
    Write-Host ("  " + $Dim + "secret & PII guardrail " + [string][char]0x00B7 +
                " no trace. no leaks. stays safe." + $Reset)
    Write-Host ""
}

# --- the progress bar --------------------------------------------------------------------
# One bar pinned to the last line, the same shape install.sh draws. Without VT support, or
# with -Plain, it degrades to one line per step: carriage returns in a transcript are noise.
$script:Pct = 0
$script:StepNo = 0
$script:BarLabel = ""
$script:BarOn = $false

function Use-Bar { return ($script:Vt -and -not $Plain) }

function Get-ConsoleWidth {
    try { if ($Host.UI.RawUI.WindowSize.Width -gt 0) { return $Host.UI.RawUI.WindowSize.Width } }
    catch { }
    return 80
}

function Write-Bar {
    if (-not (Use-Bar)) { return }
    $cols = Get-ConsoleWidth
    $room = $cols - 11
    $labelWidth = 0
    if ($cols -ge 60) { $labelWidth = [Math]::Min(26, [int]($cols / 3)) }
    $width = $room - $labelWidth - 2
    if ($width -lt 10) { $labelWidth = 0; $width = $room }
    if ($width -lt 10) { $width = 10 }

    $fill = [int]($script:Pct * $width / 100)
    $bar = ""
    $done = 0
    for ($i = 0; $i -lt $RampRgb.Count; $i++) {
        $end = [Math]::Min([int](($i + 1) * $width / $RampRgb.Count), $fill)
        if ($end -gt $done) {
            $bar += "$E[38;2;$($RampRgb[$i])m" + ($Full.ToString() * ($end - $done))
            $done = $end
        }
    }
    if ($done -lt $fill) { $bar += $Full.ToString() * ($fill - $done); $done = $fill }
    if ($done -lt $width) { $bar += $Dim + ($Empty.ToString() * ($width - $done)) }

    $label = ""
    if ($labelWidth -gt 0) {
        $label = $script:BarLabel
        if ($label.Length -gt $labelWidth) { $label = $label.Substring(0, $labelWidth) }
        $label = "  $Dim$label$Reset"
    }
    $percent = "{0,3}" -f $script:Pct
    Write-Host ("`r  $Dim[$Reset$bar$Reset$Dim]$Reset $Bold$percent%$Reset$label$E[K") -NoNewline
    $script:BarOn = $true
}

function Clear-Bar {
    if ($script:BarOn) { Write-Host ("`r$E[K") -NoNewline; $script:BarOn = $false }
}

function Write-Step([string]$Text) {
    $script:StepNo++
    $floor = [int](($script:StepNo - 1) * 100 / $TotalSteps)
    if ($floor -gt $script:Pct) { $script:Pct = $floor }
    $script:BarLabel = $Text
    if (Use-Bar) { Write-Bar } else { Write-Host "[$($script:StepNo)/$TotalSteps] $Text" }
}

function Write-Ok([string]$Text) {
    if (Use-Bar) { $script:BarLabel = $Text; Write-Bar }
    else { Write-Host "      $Tick $Text" -ForegroundColor Green }
}

function Write-Note([string]$Text) {
    if (Use-Bar) { $script:BarLabel = $Text; Write-Bar } else { Write-Host "      $Text" }
}

# Warnings and errors print in both modes: a warning that scrolled past inside a progress bar
# was never delivered.
function Write-Warn([string]$Text) {
    Clear-Bar
    Write-Host "      ! $Text" -ForegroundColor Yellow
    Write-Bar
}

function Stop-Install([string]$Text) {
    Clear-Bar
    Restore-Console
    Write-Host ""
    Write-Host "  $Cross $Text" -ForegroundColor Red
    Write-Host ""
    exit 1
}

function Invoke-Native([scriptblock]$ZeroTraceCommand) {
    # A scriptblock is bound to the scope it was written in, so the preference has to be set
    # where the name lookup will land - script AND global, because under `irm | iex` the
    # script's top level IS the global scope.
    #
    # The parameter's name is deliberately one nobody else would use: a scriptblock resolves
    # its variables in the scope that RUNS it, so a caller passing `{ & $Body }` found this
    # parameter - itself - and recursed until PowerShell reported a call depth overflow.
    $prevScript = $script:ErrorActionPreference
    $prevGlobal = $global:ErrorActionPreference
    $script:ErrorActionPreference = "Continue"
    $global:ErrorActionPreference = "Continue"
    try { & $ZeroTraceCommand } finally {
        $script:ErrorActionPreference = $prevScript
        $global:ErrorActionPreference = $prevGlobal
    }
}

# Runs a step, keeping its output for the failure path only: pip's resolver prints forty
# lines nobody reads, right up until it fails and every one of them matters.
function Invoke-Logged([string]$Label, [int]$Target, [scriptblock]$Body) {
    $script:BarLabel = $Label
    Write-Bar
    if (-not (Use-Bar)) { Write-Note "$Label..." }
    # Reset first: $LASTEXITCODE is global and survives, so a body that sets no exit code at
    # all would otherwise be judged by whatever ran before it.
    $global:LASTEXITCODE = 0
    # The preferences are set HERE rather than by calling Invoke-Native with `{ & $Body }`.
    # That looked like reuse and was infinite recursion: a scriptblock resolves its variables
    # in the scope that runs it, so the `$Body` inside it found Invoke-Native's OWN parameter -
    # itself - and PowerShell ended the install with "call depth overflow".
    $prevScript = $script:ErrorActionPreference
    $prevGlobal = $global:ErrorActionPreference
    $script:ErrorActionPreference = "Continue"
    $global:ErrorActionPreference = "Continue"
    try {
        # Captured, then appended - NOT `*>> $LogFile`. A redirection holds the file open for
        # as long as the command runs, and the next step then fails with "the process cannot
        # access the file because it is being used by another process".
        $output = & $Body 2>&1
    } finally {
        $script:ErrorActionPreference = $prevScript
        $global:ErrorActionPreference = $prevGlobal
    }
    $code = $LASTEXITCODE
    if ($output) {
        try { ($output | Out-String) | Add-Content -Path $LogFile -Encoding UTF8 } catch { }
    }
    if ($code -ne 0) { return $false }
    $script:Pct = $Target
    Write-Bar
    return $true
}

function Show-Log {
    Clear-Bar
    if (Test-Path $LogFile) {
        Get-Content -Path $LogFile -Tail 25 -Encoding UTF8 | ForEach-Object { Write-Host "      $_" }
    }
}

# --- finding a Python ----------------------------------------------------------------------
function Test-StubPython([string]$Exe) {
    # The Microsoft Store "App execution alias" is a zero-byte stub that opens the Store.
    try {
        $item = Get-Item -LiteralPath $Exe -ErrorAction Stop
        return ($item.Length -eq 0)
    } catch { return $false }
}

function Find-Python {
    $candidates = @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3"),
                    @("python3"), @("python"))
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $command = Get-Command $exe -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        if ($command.Source -and (Test-StubPython $command.Source)) { continue }
        $checkArgs = @()
        if ($candidate.Length -gt 1) { $checkArgs += $candidate[1] }
        $checkArgs += @("-c", "import sys; sys.exit(0 if sys.version_info >= ($PyMinMajor, $PyMinMinor) else 1)")
        Invoke-Native { & $exe @checkArgs 2>$null 1>$null }
        if ($LASTEXITCODE -eq 0) {
            if ($candidate.Length -gt 1) { return @($exe, $candidate[1]) }
            return @($exe)
        }
    }
    return $null
}

function Invoke-Py([string[]]$PyCmd, [string[]]$PyArgs) {
    $exe = $PyCmd[0]
    $rest = @()
    if ($PyCmd.Length -gt 1) { $rest = $PyCmd[1..($PyCmd.Length - 1)] }
    & $exe @($rest + $PyArgs)
}

# --- the Windows PATH ------------------------------------------------------------------------
# `setx` is the obvious tool and the wrong one: it truncates a PATH longer than 1024
# characters, and a corporate PATH is routinely longer. This goes through the registry, keeps
# the value's type (a PATH holding %USERPROFILE% is REG_EXPAND_SZ, and rewriting it as a plain
# string turns every variable in it into a literal), and broadcasts the change so a new
# Explorer-launched shell sees it without a sign-out.
function Set-UserPathEntry([string]$Dir, [switch]$Remove) {
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
        if ($null -eq $key) { return $false }
        try {
            $current = [string]$key.GetValue("Path", "", "DoNotExpandEnvironmentNames")
            $entries = @($current -split ";" | Where-Object { $_ -ne "" })
            $has = @($entries | Where-Object { $_.TrimEnd("\") -ieq $Dir.TrimEnd("\") }).Count -gt 0
            if ($Remove) {
                if (-not $has) { return $false }
                $entries = @($entries | Where-Object { $_.TrimEnd("\") -ine $Dir.TrimEnd("\") })
            } else {
                if ($has) { return $false }
                $entries += $Dir
            }
            $kind = "ExpandString"
            try { if ($key.GetValueKind("Path") -eq "String") { $kind = "String" } } catch { }
            $key.SetValue("Path", ($entries -join ";"), $kind)
        } finally { $key.Close() }
    } catch { return $false }
    try {
        Add-Type -Namespace ZeroTrace -Name Env -MemberDefinition @'
[DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam,
    string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@ -ErrorAction Stop
        $out = [UIntPtr]::Zero
        [void][ZeroTrace.Env]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero,
                                                  "Environment", 2, 1000, [ref]$out)
    } catch { }
    return $true
}

# --- uninstall --------------------------------------------------------------------------------
function Remove-ZeroTrace {
    $venvPy = Join-Path $VenvDir "Scripts\python.exe"
    $found = $false
    if (Test-Path $venvPy) {
        Write-Host "zerotrace: removing the git hooks"
        Invoke-Native { & $venvPy -m zerotrace uninstall --global }
        if ($Purge) { Invoke-Native { & $venvPy -m zerotrace model down --purge } }
        else { Invoke-Native { & $venvPy -m zerotrace model down } }
        $found = $true
    } else {
        $hooks = Invoke-Native { & git config --global --get core.hooksPath }
        if ($hooks -and $hooks.StartsWith($HomeDir)) {
            Invoke-Native { & git config --global --unset core.hooksPath }
            Write-Host "zerotrace: unset the global core.hooksPath"
            $found = $true
        }
    }
    foreach ($name in @("zerotrace.cmd", "zerotrace-uninstall.cmd", "zerotrace-uninstall.ps1")) {
        $launcher = Join-Path $BinDir $name
        if (Test-Path $launcher) {
            Remove-Item -Force -ErrorAction SilentlyContinue $launcher
            $found = $true
        }
    }
    if (Test-Path $HomeDir) {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $HomeDir
        Write-Host "zerotrace: removed $HomeDir"
        $found = $true
    }
    if (Set-UserPathEntry -Dir $BinDir -Remove) {
        Write-Host "zerotrace: removed $BinDir from your user PATH"
        $found = $true
    }
    if (-not $found) {
        Write-Host "zerotrace: nothing to remove: ZeroTrace is not installed for this user."
        return
    }
    Write-Host "zerotrace: your repositories, their history and their files were not touched."
    if (-not $Purge) {
        Write-Host "zerotrace: the model image and weights were kept (zerotrace-uninstall -Purge removes them)."
    }
}

if ($Uninstall) {
    Write-Banner
    Remove-ZeroTrace
    Restore-Console
    exit 0
}

Write-Banner

# --- 1. this machine ---------------------------------------------------------------------------
Write-Step "Checking this machine"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Stop-Install "git is required (ZeroTrace protects git repos): https://git-scm.com/download/win"
}
$gitVersion = (Invoke-Native { & git --version }) -join " "
Write-Ok $gitVersion

$python = Find-Python
if (-not $python) {
    Write-Warn "no Python $PyMinMajor.$PyMinMinor or newer - trying winget"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Stop-Install ("no Python $PyMinMajor.$PyMinMinor+ and no winget. Install Python from " +
                      "https://python.org (tick 'Add to PATH'), then run this script again.")
    }
    Invoke-Native {
        & winget install --id Python.Python.3.12 -e --source winget `
            --accept-package-agreements --accept-source-agreements
    }
    # winget updates the registered PATH but not this process.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    $python = Find-Python
    if (-not $python) {
        Stop-Install "Python was installed but is not on PATH yet. Open a new terminal and run this script again."
    }
}
$pythonVersion = (Invoke-Py $python @("-c", "import platform; print(platform.python_version())")) |
    Select-Object -Last 1
Write-Ok "python $pythonVersion"

Invoke-Native { Invoke-Py $python @("-c", "import venv, ensurepip") 2>$null 1>$null }
if ($LASTEXITCODE -ne 0) { Stop-Install "this Python cannot create virtualenvs (venv/ensurepip are missing)." }
Write-Ok "venv available"

# --- 2. the environment -------------------------------------------------------------------------
Write-Step "Creating the environment"
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
Set-Content -Path $LogFile -Value "" -Encoding UTF8

function Get-Sha256([string]$Path) {
    # .NET rather than Get-FileHash: that cmdlet lives in a module, and a machine whose
    # PSModulePath points somewhere else answers "the term Get-FileHash is not recognized" -
    # at which point an installer that verifies downloads cannot verify anything. The type
    # below is part of the runtime itself and is there on 5.1 and 7 alike.
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try { $bytes = $sha.ComputeHash($stream) } finally { $stream.Dispose() }
    } finally { $sha.Dispose() }
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Test-AgainstSums([string]$Dir, [string]$Name) {
    $sums = Join-Path $Dir "SHA256SUMS"
    if (-not (Test-Path $sums)) { Write-Warn "no SHA256SUMS next to $Name; cannot verify it"; return }
    $want = ""
    foreach ($line in Get-Content -Path $sums -Encoding UTF8) {
        $parts = $line -split "\s+", 2
        if ($parts.Count -eq 2 -and $parts[1].TrimStart("*").Trim() -eq $Name) {
            $want = $parts[0].Trim().ToLower()
        }
    }
    if (-not $want) { Stop-Install "$Name is not listed in SHA256SUMS; refusing to install it." }
    $got = Get-Sha256 (Join-Path $Dir $Name)
    if ($got -ne $want) {
        Stop-Install ("$Name does not match its SHA256SUMS entry.`n      expected $want`n" +
                      "      got      $got`n      Download it again, or install from a clone.")
    }
}

function Get-LatestTag {
    # The redirect on /releases/latest names the tag and costs no API rate limit.
    try {
        $response = Invoke-WebRequest -Uri "$Repo/releases/latest" -UseBasicParsing `
            -MaximumRedirection 5 -ErrorAction Stop
        $url = ""
        try { $url = [string]$response.BaseResponse.ResponseUri } catch { }
        if (-not $url) { try { $url = [string]$response.BaseResponse.RequestMessage.RequestUri } catch { } }
        if ($url -match "/releases/tag/(.+)$") { return $Matches[1] }
    } catch { }
    return ""
}

# A clone next to this script wins: running .\install.ps1 inside a checkout you have been
# editing must install THAT checkout.
$scriptDir = $PSScriptRoot
$sourceKind = ""
$sourceDir = ""
$assetDir = ""
if (-not $Version -and -not $From -and -not $Ref -and $scriptDir -and
    (Test-Path (Join-Path $scriptDir "pyproject.toml"))) {
    if ((Get-Content (Join-Path $scriptDir "pyproject.toml") -Raw) -match 'name = "zerotrace"') {
        $Local = $true
    }
}
if ($Local) {
    if (-not $scriptDir) { Stop-Install "-Local needs the script on disk: clone, then .\install.ps1" }
    $sourceKind = "clone"; $sourceDir = $scriptDir
    Write-Note "installing from this clone ($sourceDir)"
} elseif ($Ref) {
    $sourceKind = "clone"; $sourceDir = Join-Path $HomeDir "src"
    if (Test-Path $sourceDir) { Remove-Item -Recurse -Force $sourceDir }
    if (-not (Invoke-Logged "cloning $Ref" 20 { & git clone --depth 1 --branch $Ref "$Repo.git" $sourceDir })) {
        Show-Log; Stop-Install "could not clone $Repo at $Ref"
    }
} elseif ($From) {
    if (-not (Test-Path $From)) { Stop-Install "-From: $From is not a directory" }
    $sourceKind = "release"; $assetDir = $From
} else {
    $sourceKind = "release"
    $assetDir = Join-Path $HomeDir "download"
    $tag = $Version
    if (-not $tag) { $tag = $ReleaseVersion }
    if (-not $tag) { $tag = Get-LatestTag }
    if (-not $tag) {
        Stop-Install ("could not find a published release at $Repo.`n      If the repository is " +
                      "private, clone it and run .\install.ps1 from there,`n      or pass -Ref main.")
    }
    if (-not $tag.StartsWith("v")) { $tag = "v$tag" }
    if (Test-Path $assetDir) { Remove-Item -Recurse -Force $assetDir }
    New-Item -ItemType Directory -Force -Path $assetDir | Out-Null
    Write-Note "release $tag"
    $base = "$Repo/releases/download/$tag"
    try {
        Invoke-WebRequest -Uri "$base/SHA256SUMS" -OutFile (Join-Path $assetDir "SHA256SUMS") -UseBasicParsing
    } catch { Stop-Install "could not download $tag. Check the version, or install from a clone." }
    $wheelName = ""
    foreach ($line in Get-Content (Join-Path $assetDir "SHA256SUMS") -Encoding UTF8) {
        $parts = $line -split "\s+", 2
        if ($parts.Count -eq 2 -and $parts[1].Trim().TrimStart("*").EndsWith(".whl")) {
            $wheelName = $parts[1].Trim().TrimStart("*")
        }
    }
    if (-not $wheelName) { Stop-Install "release $tag has no wheel listed in SHA256SUMS." }
    if (-not (Invoke-Logged "downloading $wheelName" 25 {
            Invoke-WebRequest -Uri "$base/$wheelName" -OutFile (Join-Path $assetDir $wheelName) -UseBasicParsing
            $global:LASTEXITCODE = 0 })) {
        Stop-Install "could not download $wheelName from $tag"
    }
    try {
        Invoke-WebRequest -Uri "$base/requirements-install.txt" -UseBasicParsing `
            -OutFile (Join-Path $assetDir "requirements-install.txt")
    } catch { Stop-Install "release $tag has no requirements-install.txt; install from a clone instead." }
}

if ($sourceKind -eq "release") {
    $wheel = Get-ChildItem -Path $assetDir -Filter "*.whl" | Select-Object -First 1
    if (-not $wheel) { Stop-Install "no .whl in $assetDir" }
    Test-AgainstSums $assetDir $wheel.Name
    Test-AgainstSums $assetDir "requirements-install.txt"
    Write-Ok "verified $($wheel.Name) against SHA256SUMS"
    $requirements = Join-Path $assetDir "requirements-install.txt"
    $package = $wheel.FullName
} else {
    $requirements = Join-Path $sourceDir "requirements\install.txt"
    $package = $sourceDir
    if (-not (Test-Path $requirements)) {
        Stop-Install "$requirements is missing; is $sourceDir a ZeroTrace checkout?"
    }
}

# An existing environment is moved aside rather than deleted: if anything below fails, the
# hooks that are already registered keep pointing at an interpreter that still exists.
$restoreVenv = $false
if (Test-Path $VenvDir) {
    if (Test-Path "$VenvDir.old") { Remove-Item -Recurse -Force "$VenvDir.old" }
    Move-Item -Path $VenvDir -Destination "$VenvDir.old"
    $restoreVenv = $true
    Write-Note "replacing the existing environment"
}

function Restore-Venv {
    if (Test-Path $VenvDir) { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $VenvDir }
    if ($restoreVenv -and (Test-Path "$VenvDir.old")) {
        Move-Item -Path "$VenvDir.old" -Destination $VenvDir
        Write-Warn "put the previous install back; nothing on this machine changed"
    }
}

if (-not (Invoke-Logged "creating the environment" 28 { Invoke-Py $python @("-m", "venv", $VenvDir) })) {
    Show-Log; Restore-Venv; Stop-Install "could not create a virtualenv in $VenvDir"
}
$venvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Restore-Venv; Stop-Install "the virtualenv has no interpreter in it." }

Invoke-Logged "upgrading pip" 30 { & $venvPy -m pip install --upgrade pip } | Out-Null

# Dependencies first, hash-checked, wheels only: every file is known in advance and nothing
# runs setup code while it installs. pip reads PIP_FIND_LINKS / PIP_NO_INDEX itself, which is
# what makes an air-gapped install work with no extra flag here.
if (-not (Invoke-Logged "installing dependencies" 55 {
        & $venvPy -m pip install --disable-pip-version-check --require-hashes `
            --only-binary :all: -r $requirements })) {
    Show-Log; Restore-Venv; Stop-Install "could not install the dependencies. The full log is at $LogFile"
}

if ($sourceKind -eq "release") {
    $installArgs = @("--no-deps", "--no-index", "--only-binary", ":all:", $package)
} else {
    $installArgs = @("--no-deps", "--no-build-isolation", "--no-index", $package)
}
if (-not (Invoke-Logged "installing zerotrace" 62 {
        & $venvPy -m pip install --disable-pip-version-check @installArgs })) {
    Show-Log; Restore-Venv; Stop-Install "could not install zerotrace. The full log is at $LogFile"
}

if ($Extras -like "*pii-ner*") {
    Write-Warn "the pii-ner extra is not hash-locked; installing it from the index"
    if (-not (Invoke-Logged "installing the PII NER engine" 66 {
            & $venvPy -m pip install --disable-pip-version-check presidio-analyzer presidio-anonymizer })) {
        Write-Warn "could not install the NER engine; the regex PII detectors still work"
    }
}

if (Test-Path "$VenvDir.old") { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$VenvDir.old" }
$installedVersion = (Invoke-Native { & $venvPy -m zerotrace version }) -join " "
Write-Ok $installedVersion

# --- the launchers ---------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
# CRLF, and written as ASCII: cmd.exe does not reliably read a batch file with bare LFs, and a
# shim that works until somebody adds a line to it is a trap for later.
$launcher = @"
@echo off
rem $Marker
rem Delete this file and $HomeDir to remove ZeroTrace, or run zerotrace-uninstall.
"$venvPy" -m zerotrace %*
"@
Set-Content -Path (Join-Path $BinDir "zerotrace.cmd") -Value $launcher -Encoding ASCII

# Generated here because only this run knows where everything went. The work is in a .ps1
# and the .cmd is a one-line wrapper: cmd.exe reads a batch file from disk as it goes, so a
# batch file that deletes the directory it lives in stops in the middle, while PowerShell
# reads the whole script first and finishes the job.
$uninstallPs1 = @"
# Removes ZeroTrace: the git hooks first (so no repo is left pointing at an interpreter that
# is about to vanish), then the model container, then this installation.
param([switch]`$Purge)
`$ErrorActionPreference = 'Continue'
`$venvPy = '$venvPy'
`$homeDir = '$HomeDir'
`$binDir  = '$BinDir'
if (Test-Path `$venvPy) {
    Write-Host 'zerotrace: removing the git hooks'
    & `$venvPy -m zerotrace uninstall --global
    if (`$Purge) { & `$venvPy -m zerotrace model down --purge } else { & `$venvPy -m zerotrace model down }
} else {
    `$hooks = & git config --global --get core.hooksPath
    if (`$hooks -and `$hooks.StartsWith(`$homeDir)) { & git config --global --unset core.hooksPath }
}
try {
    `$key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', `$true)
    if (`$null -ne `$key) {
        try {
            `$current = [string]`$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
            `$entries = @(`$current -split ';' | Where-Object { `$_ -ne '' -and `$_.TrimEnd('\\') -ine `$binDir.TrimEnd('\\') })
            if (`$entries.Count -ne @(`$current -split ';' | Where-Object { `$_ -ne '' }).Count) {
                `$kind = 'ExpandString'
                try { if (`$key.GetValueKind('Path') -eq 'String') { `$kind = 'String' } } catch { }
                `$key.SetValue('Path', (`$entries -join ';'), `$kind)
                Write-Host "zerotrace: removed `$binDir from your user PATH"
            }
        } finally { `$key.Close() }
    }
} catch { }
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path `$binDir 'zerotrace.cmd')
# Everything except the directory this uninstall is being RUN from. cmd.exe reads a batch file
# line by line as it runs, so deleting zerotrace-uninstall.cmd out from under itself ends with
# "The batch file cannot be found" and a non-zero exit after a successful removal. The launcher
# deletes its own directory as its last act instead (see the .cmd below).
if (Test-Path `$homeDir) {
    Get-ChildItem -LiteralPath `$homeDir -Force |
        Where-Object { `$_.FullName -ne `$binDir } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    if (`$binDir -notlike "`$homeDir*") {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `$homeDir
    }
    Write-Host "zerotrace: removed `$homeDir"
}
Write-Host 'zerotrace: your repositories, their history and their files were not touched.'
if (-not `$Purge) {
    Write-Host 'zerotrace: the model image and weights were kept (zerotrace-uninstall --purge removes them).'
}
"@
$uninstallPs1Path = Join-Path $BinDir "zerotrace-uninstall.ps1"
Set-Content -Path $uninstallPs1Path -Value $uninstallPs1 -Encoding UTF8

# What the launcher deletes as its last act - the part the uninstall itself cannot remove
# while it is running. In the default layout the launchers live inside ~/.zerotrace, so the
# whole directory goes; with a custom ZEROTRACE_BIN_DIR it is somebody else's directory (a
# ~/bin full of other tools), and only our own three files may be touched.
if ($BinDir -like "$HomeDir*") {
    $sweep = "rd /s /q ""$HomeDir"""
} else {
    $sweep = "del /q ""$(Join-Path $BinDir 'zerotrace.cmd')"" ""$uninstallPs1Path"" " +
             """$(Join-Path $BinDir 'zerotrace-uninstall.cmd')"""
}

$uninstallCmd = @"
@echo off
rem $Marker
setlocal
set "ZT_PURGE="
if /I "%~1"=="--purge" set "ZT_PURGE=-Purge"
if /I "%~1"=="-Purge" set "ZT_PURGE=-Purge"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$uninstallPs1Path" %ZT_PURGE%
endlocal
rem Last: stop cmd.exe reading this file, then delete the directory it is sitting in. `(goto)`
rem with no label closes the batch context - the rest of the line still runs, and nothing tries
rem to read a file that no longer exists.
(goto) 2>nul & $sweep
"@
Set-Content -Path (Join-Path $BinDir "zerotrace-uninstall.cmd") -Value $uninstallCmd -Encoding ASCII
Write-Ok (Join-Path $BinDir "zerotrace.cmd")

if (-not $env:ZEROTRACE_NO_MODIFY_PATH) {
    if (Set-UserPathEntry -Dir $BinDir) {
        Write-Ok "added $BinDir to your user PATH (cmd, PowerShell, new windows)"
    }
}
$env:Path = "$BinDir;$env:Path"

# --- 3-6. Docker, hooks, model, validation -------------------------------------------------
# One implementation for every OS: see src/zerotrace/setup.py.
Clear-Bar
$setupArgs = @("setup", "--step-offset", "$($script:StepNo)", "--steps", "$TotalSteps")
if ($NoModel) { $setupArgs += "--no-model" }
Invoke-Native { & $venvPy -m zerotrace @setupArgs }
$setupCode = $LASTEXITCODE
Restore-Console
if ($setupCode -ne 0) {
    Write-Host ""
    Write-Host "  $Cross the guardrail is not in place. Run ``zerotrace doctor`` to see why," -ForegroundColor Red
    Write-Host "     then run this installer again. Nothing else on this machine was changed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  $Arrow open a new terminal (or run ```$env:Path = `"$BinDir;`$env:Path`"``) to use zerotrace here."
Write-Host ""
