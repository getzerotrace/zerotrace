#!/bin/bash
# Jamf Pro script payload for ZeroTrace (machine-wide, every user on the Mac).
#
# Upload as a Script in Jamf Pro, attach to a Policy (e.g. trigger "Enrollment Complete" or a
# recurring check-in policy), and scope it to the target smart group. Jamf runs policy scripts
# as root, which is what `zerotrace install --system` needs. See docs/DEPLOYMENT.md §3.
set -euo pipefail

# 1. Install the tool (signed single-file binary once published; falls back to pip --user).
#    This runs as root on every managed Mac, so the download is strict: HTTPS only, including
#    any redirect (--proto '=https'), TLS 1.2 or newer, and saved to a file first so a
#    connection that drops half-way never runs a truncated script.
INSTALLER_URL="https://raw.githubusercontent.com/getzerotrace/zerotrace/main/install.sh"
INSTALLER="$(mktemp)"
trap 'rm -f "$INSTALLER"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL "$INSTALLER_URL" -o "$INSTALLER"
bash "$INSTALLER"

# 2. Machine-wide git hook: every user's repos on this Mac are now protected.
ZEROTRACE_BIN="$(command -v zerotrace || echo "$HOME/.local/bin/zerotrace")"
"$ZEROTRACE_BIN" install --system

# 3. Locked org policy (block_severity, model endpoint, etc. can't be weakened per-repo).
POLICY_DIR="/Library/Application Support/zerotrace"
mkdir -p "$POLICY_DIR"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/policy.yml" ]; then
  cp "$SCRIPT_DIR/policy.yml" "$POLICY_DIR/policy.yml"
else
  echo "warn: policy.yml not found next to this script; ship one alongside it in the Jamf" \
       "package, or fetch it from your internal CDN (see policy.example.yml)." >&2
fi
