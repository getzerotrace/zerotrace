# Sample org rollout kit

Fleet deployment snippets to go with `docs/DEPLOYMENT.md` §3 (MDM rollout). Nothing here is
wired into CI; these are starting points for an IT/security team to adapt.

| File | Use |
|---|---|
| `policy.example.yml` | Org policy with locked keys (`block_severity`, `model.endpoint`, `model.allow_remote`). Copy to `/etc/zerotrace/policy.yml` (Linux/macOS) or `%ProgramData%\zerotrace\policy.yml` (Windows), or point `ZEROTRACE_POLICY` at a custom path. |
| `intune-install.ps1` / `intune-uninstall.ps1` | Win32 app install/uninstall scripts for Microsoft Intune. Package with the repo's `install.ps1` and a `policy.yml` (copied from `policy.example.yml`). |
| `jamf-postinstall.sh` | Policy script for Jamf Pro (macOS). Package with a `policy.yml` alongside it, or fetch one from an internal host. |

All three run the same three steps: install the tool, `zerotrace install --system` (machine-wide
`core.hooksPath`, covers every user), then drop the locked org policy file. See
`docs/DEPLOYMENT.md` §3 for the full rollout story, including the model tier and the WSL caveat.
