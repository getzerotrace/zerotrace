#requires -Version 5.1
# Intune Win32 app uninstall script for ZeroTrace. Pair with intune-install.ps1.
$ErrorActionPreference = "Continue"
zerotrace uninstall --system
Remove-Item -Recurse -Force (Join-Path $env:ProgramData "zerotrace") -ErrorAction SilentlyContinue
