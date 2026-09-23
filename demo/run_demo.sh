#!/usr/bin/env bash
# ZeroTrace live demo (macOS / Linux). Mirrors demo/run_demo.ps1 scene for scene.
#
#   ./demo/run_demo.sh           # interactive: pauses between scenes
#   ./demo/run_demo.sh --auto    # no pauses (rehearsal / CI smoke test)
#
# Fully sandboxed: GIT_CONFIG_GLOBAL and ZEROTRACE_HOME point into the demo dir, so your
# real ~/.gitconfig and repos are never touched. Delete the demo dir to clean up.
set -uo pipefail

AUTO=0; [[ "${1:-}" == "--auto" ]] && AUTO=1
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_DIR="${ZEROTRACE_DEMO_DIR:-${TMPDIR:-/tmp}/zerotrace-demo}"
DEMO_DIR="${DEMO_DIR%/}"
PY="$REPO_ROOT/.venv/bin/python"
MODEL="qwen2.5-coder:3b-instruct-q4_K_M"

c_cyan=$'\033[36m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
scene() { printf '\n%s━━ %s ━━%s\n' "$c_cyan" "$1" "$c_off"; }
say()   { printf '%s%s%s\n' "$c_dim" "$1" "$c_off"; }
run()   { printf '%s$ %s%s\n' "$c_green" "$*" "$c_off"; "$@"; }
pause() { [[ "$AUTO" == 1 ]] && return 0; printf '%s[enter] %s%s' "$c_yellow" "${1:-continue}" "$c_off"; read -r _ </dev/tty; }
zt()    { "$PY" -m zerotrace "$@"; }

# Commit; if the hook couldn't reach a terminal, fall back to `zerotrace review` + retry.
commit() {
  if git commit -m "$1"; then return 0; fi
  if [[ "$AUTO" == 1 ]]; then say "(auto mode: commit stays blocked)"; return 1; fi
  say "Fix interactively with: zerotrace review"
  pause "run zerotrace review"
  zt review && git commit -m "$1"
}

if [[ ! -x "$PY" ]]; then
  echo "Missing $PY. Set up once with:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi

# ---- sandbox --------------------------------------------------------------------------
rm -rf "$DEMO_DIR"; mkdir -p "$DEMO_DIR"
export GIT_CONFIG_GLOBAL="$DEMO_DIR/gitconfig" GIT_CONFIG_NOSYSTEM=1
export ZEROTRACE_HOME="$DEMO_DIR/zerotrace-home"
git config --global user.name "Demo Developer"
git config --global user.email "dev@example.test"
git config --global init.defaultBranch main
git config --global advice.detachedHead false

scene "0 · Before: this machine is not protected; the local model is warmed up"
say "If the model shows as unreachable: docker compose -f docker/docker-compose.yml up -d"
say "(or natively: ollama serve && ollama pull $MODEL). Without it, MEDIUM findings WARN."
(cd "$DEMO_DIR" && run zt doctor --warm) || true

scene "1 · One install protects every repo on the machine"
say "No per-repo setup and no .pre-commit-config.yaml: one global git hooks directory."
pause "install"
run zt install --global
run git config --global core.hooksPath

# ---- scene 2: Python service -------------------------------------------------------
scene "2 · payments-api: hardcoded secrets in code, config, Docker, Terraform, PII"
"$PY" "$REPO_ROOT/demo/render_fixtures.py" payments-api "$DEMO_DIR/payments-api" --fresh
cd "$DEMO_DIR/payments-api" && git init -q
say "A brand-new repo. Nothing ZeroTrace-specific in it:"
run ls -A
git add -A
run git diff --cached --stat
pause "git commit (ZeroTrace steps in)"
say "Try: [U] on .env · [V] on the Stripe key · [R] on the PII fixture"
commit "Add payments service" || true

# ---- scene 3: Node app ---------------------------------------------------------------
scene "3 · web-app: a second repo, prompt injection and the AI tie-break"
"$PY" "$REPO_ROOT/demo/render_fixtures.py" web-app "$DEMO_DIR/web-app" --fresh
cd "$DEMO_DIR/web-app" && git init -q
say "client.js contains a comment telling the AI to allow the key. The key is a"
say "provider-format OpenAI token, so it blocks deterministically and never reaches the model."
say "analytics.js holds an ambiguous short token, which is decided by the local model."
git add -A
pause "git commit"
commit "Add web client" || true

# ---- scene 4: bypass -> pre-push backstop ---------------------------------------------
scene "4 · Someone bypasses the hook with --no-verify"
cd "$DEMO_DIR/payments-api"
git init -q --bare "$DEMO_DIR/origin.git"
git remote add origin "$DEMO_DIR/origin.git" 2>/dev/null
if ! git rev-parse -q --verify HEAD >/dev/null; then  # scene 2 was left blocked
  git reset -q; git commit -q --no-verify --allow-empty -m "init"
fi
git push -q -u origin main 2>/dev/null
"$PY" "$REPO_ROOT/demo/render_fixtures.py" sneaky "$DEMO_DIR/payments-api/scripts"
git add scripts
run git commit --no-verify -m "quick deploy script"
pause "git push"
run git push origin main || say "↑ push blocked before the secret left the laptop"

# ---- scene 5: trust -------------------------------------------------------------------
scene "5 · Trust: doctor + audit log (fingerprints only, never values)"
run zt doctor
say "Last audit entries (hash-chained, stored inside .git so nothing is ever committed):"
tail -n 3 "$DEMO_DIR/payments-api/.git/zerotrace/audit.log.jsonl" 2>/dev/null | cut -c1-200

scene "Done"
say "Sandbox: $DEMO_DIR   (your real git config was never modified)"
