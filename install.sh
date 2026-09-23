#!/usr/bin/env bash
# ZeroTrace installer - macOS, Linux, WSL and Git Bash.
#
#   install:    curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/getzerotrace/zerotrace/main/install.sh | bash
#   uninstall:  zerotrace-uninstall        (or: ./install.sh --uninstall)
#
# What it does, in six steps: checks this machine, builds an environment that belongs to
# ZeroTrace alone, checks Docker, installs the git hooks for every repo on the machine, brings
# the local model up, and then PROVES the guardrail works by trying to commit a secret into a
# throwaway repository. Steps three to six are `zerotrace setup`, so they behave the same here
# and on Windows.
#
# It never installs into your system Python and never touches a project's virtualenv. That is
# not tidiness: `pip install --user` is refused outright by Homebrew's Python and by Debian,
# Ubuntu and Fedora since PEP 668, which is how the previous version of this script failed on
# a current Mac.
#
# Run from a clone, it installs THAT clone. Otherwise it installs the latest published
# release, verifying every downloaded file against the release's SHA256SUMS.
#
# Options:
#   --uninstall           remove everything this script installed
#   --version <vX.Y.Z>    a published release (default: the latest one)
#   --ref <branch|tag>    build from git instead - for development
#   --local               install the clone this script sits in (the default when it does)
#   --from <dir>          install from release files already downloaded (air-gapped, CI)
#   --no-model            skip the Docker/model step (several GB on a first run)
#   --with-pii            also install the Presidio NER engine (not hash-locked)
#   --ascii               draw with ASCII only
#   --verbose, -v         print every line instead of one progress bar
#   --help
#
# Environment: ZEROTRACE_HOME, ZEROTRACE_BIN_DIR, ZEROTRACE_REPO_URL, ZEROTRACE_REF,
#   ZEROTRACE_EXTRAS, ZEROTRACE_NO_MODIFY_PATH=1, NO_COLOR, and pip's own PIP_FIND_LINKS /
#   PIP_NO_INDEX for an offline install from a wheelhouse.
set -euo pipefail

if [ -z "${HOME:-}" ]; then
  printf '%s\n' "HOME is not set, so there is nowhere to install to." >&2
  printf '%s\n' "Set HOME, or point ZEROTRACE_HOME and ZEROTRACE_BIN_DIR somewhere writable." >&2
  exit 1
fi

REPO_URL="${ZEROTRACE_REPO_URL:-https://github.com/getzerotrace/zerotrace}"
REF="${ZEROTRACE_REF:-}"
EXTRAS="${ZEROTRACE_EXTRAS:-}"
HOME_DIR="${ZEROTRACE_HOME:-$HOME/.zerotrace}"
BIN_DIR="${ZEROTRACE_BIN_DIR:-$HOME/.local/bin}"
VENV_DIR="$HOME_DIR/venv"
LOG_FILE="$HOME_DIR/install.log"
PY_MIN_MAJOR=3
PY_MIN_MINOR=11
TOTAL_STEPS=6                 # two here, four in `zerotrace setup`
MARKER="# added by ZeroTrace installer"
# Stamped by scripts/release.py into the copy attached to each release, so
# `releases/latest/download/install.sh` installs exactly the release it came from.
RELEASE_VERSION=""

UNINSTALL=0
LOCAL_DIR=""
FROM_DIR=""
WANT_VERSION=""
PULL_MODEL=1
ASCII_FORCED=0
VERBOSE=0
SELFTEST=0
PURGE=0
[ -n "${ZEROTRACE_ASCII:-}" ] && ASCII_FORCED=1

# Spelled out rather than read back out of this file with sed: under `curl | bash` there is no
# file to read - $0 is `bash`.
usage() {
  cat <<'USAGE'
ZeroTrace installer

  curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/getzerotrace/zerotrace/main/install.sh | bash

From a clone it installs that clone; otherwise the latest published release.

Options:
  --uninstall         remove everything this script installed (also: zerotrace-uninstall)
  --purge             with --uninstall: also delete the model image and weights
  --version <vX.Y.Z>  install a particular release
  --ref <branch>      build from git instead - for development
  --local             install the clone this script sits in (already the default there)
  --from <dir>        install from release files already downloaded (air-gapped, CI)
  --no-model          skip the Docker and model step (several GB on a first run)
  --with-pii          also install the Presidio NER engine
  --ascii             draw with ASCII only            --verbose, -v   print every line
  --help              this text
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall|--remove) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    --version) [ $# -ge 2 ] || { usage >&2; exit 2; }; WANT_VERSION="$2"; shift 2 ;;
    --version=*) WANT_VERSION="${1#--version=}"; shift ;;
    --ref) [ $# -ge 2 ] || { usage >&2; exit 2; }; REF="$2"; shift 2 ;;
    --ref=*) REF="${1#--ref=}"; shift ;;
    --from) [ $# -ge 2 ] || { usage >&2; exit 2; }; FROM_DIR="$2"; shift 2 ;;
    --from=*) FROM_DIR="${1#--from=}"; shift ;;
    --local)
      # `dirname "$0"` is meaningless under `curl | bash`: $0 is `bash` and no script exists
      # on disk to install from. Test for the file, not for a slash.
      if [ -f "$0" ]; then
        LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
      else
        printf '%s\n' "--local needs the script on disk: clone, then ./install.sh" >&2
        exit 2
      fi
      shift ;;
    --no-model) PULL_MODEL=0; shift ;;
    --with-pii) EXTRAS="${EXTRAS:+$EXTRAS,}pii-ner"; shift ;;
    --with-llm) shift ;;                       # accepted for compatibility; the client is stdlib
    --ascii) ASCII_FORCED=1; shift ;;
    --verbose|-v) VERBOSE=1; shift ;;
    --selftest) SELFTEST=1; shift ;;           # draw the chrome and exit (a test hook)
    --help|-h) usage; exit 0 ;;
    *) printf 'unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

# ── presentation ─────────────────────────────────────────────────────────────────────────
# Two independent questions: "may I use colour" (a terminal, and NO_COLOR unset) and "may I
# draw box characters" (a UTF-8 locale). A UTF-8 pipe still wants no escapes; an ASCII
# terminal still wants colour.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
  TTY=1
  ESC=$(printf '\033')
  N="${ESC}[0m"; B="${ESC}[1m"; D="${ESC}[2m"
  G="${ESC}[38;5;71m"; Y="${ESC}[38;5;179m"; R="${ESC}[38;5;167m"; MUTE="${ESC}[38;5;245m"
else
  TTY=0
  ESC=''; N=''; B=''; D=''; G=''; Y=''; R=''; MUTE=''
fi

case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
  *[Uu][Tt][Ff]8*|*[Uu][Tt][Ff]-8*) UNICODE=1 ;;
  *) UNICODE=0 ;;
esac
[ "$ASCII_FORCED" = 1 ] && UNICODE=0

if [ "$UNICODE" = 1 ]; then
  TICK='✓'; CROSS='✗'; WARN_G='!'; ARROW='→'; FULL='█'; EMPTY='░'
else
  TICK='ok'; CROSS='xx'; WARN_G='!'; ARROW='->'; FULL='#'; EMPTY='-'
fi

# The accent from src/zerotrace/ui/theme.py - deep teal to emerald. tests/test_theme.py fails
# if these drift from the Python ones, because a brand that is one colour in the installer and
# another in the tool is two brands.
ACCENT_RGB='5;150;105'
ACCENT_256='29'
RAMP_RGB='17;94;89 17;102;93 17;111;96 17;119;100 17;127;104 17;135;107 16;144;111 16;152;115 16;160;118 16;169;122 16;177;126 16;185;129'
RAMP_256='23 23 29 29 29 29 36 36 36 42 42 42'

case "${COLORTERM:-}" in
  truecolor|24bit) TRUECOLOR=1 ;;
  *) TRUECOLOR=0 ;;
esac

RAMP=''
if [ "$TTY" = 1 ]; then
  if [ "$TRUECOLOR" = 1 ]; then
    for rgb in $RAMP_RGB; do RAMP="$RAMP ${ESC}[38;2;${rgb}m"; done
    ACCENT="${ESC}[1;38;2;${ACCENT_RGB}m"
  else
    for idx in $RAMP_256; do RAMP="$RAMP ${ESC}[38;5;${idx}m"; done
    ACCENT="${ESC}[1;38;5;${ACCENT_256}m"
  fi
else
  ACCENT=''
fi

say() { printf '%s\n' "$*"; }

# The mark and the wordmark, the same art `zerotrace install` prints (ui/assets/mark.small.
# uni.txt, inverted so the cut-outs that carry the face stay unpainted).
banner() {
  [ "$TTY" = 0 ] && { say "ZeroTrace installer"; say ""; return; }
  say ""
  if [ "$UNICODE" = 1 ]; then
    while IFS= read -r line; do printf '  %s%s%s\n' "$ACCENT" "$line" "$N"; done <<'MARK'
███▀██████████████████▀███
██  ▀███████▀████████▀ ▀██
██   ▀████▀    ▀████▀   ██
██  ▀▄▄      ▄█▄   ▄█  ███
███▄  ▀      ████ ▀▀  ▄███
█████       ██▀▀██  ▄█████
█████       █       ██████
████▀    ▄    ▄▄▄   ▀█████
████▄    ▀    ▀▀     ▄████
████       ▀▀█▄ ▄    █████
████▄▄       █▀▀█  ▄▄█████
█████▀       ▀  ▄▄ ▀██████
██████▄     ▄██▀▀ ▄███████
████████▄▄▄▄▄▄▄▄██████████
MARK
    say ""
  fi
  while IFS= read -r line; do
    [ "$UNICODE" = 1 ] || line=$(printf '%s' "$line" | tr '█' '#')
    printf '  %s%s%s\n' "$ACCENT" "$line" "$N"
  done <<'WORDMARK'
█████  █████  ████   █████  █████  ████    ███   █████  █████
   ██  ██     ██ ██  ██ ██    ██   ██ ██  ██ ██  ██     ██
  ██   ████   ████   ██ ██    ██   ████   █████  ██     ████
 ██    ██     ██ ██  ██ ██    ██   ██ ██  ██ ██  ██     ██
█████  █████  ██ ██  █████    ██   ██ ██  ██ ██  █████  █████
WORDMARK
  printf '\n  %ssecret & PII guardrail · no trace. no leaks. stays safe.%s\n\n' "$MUTE" "$N"
}

# ── the progress bar ─────────────────────────────────────────────────────────────────────
# One bar, pinned to the last line, with the log scrolling above it: a bar printed above its
# own log is pushed off the screen by the next thing that happens. Into a pipe or a CI log the
# whole mechanism degrades to one line per event - carriage returns in a log file are noise.
PCT=0
BAR_ON=0
BAR_LABEL=''
STEP_NO=0

measure_cols() {
  local c=''
  if [ "$TTY" = 1 ]; then
    # Through fd 2: command substitution gives stdout a pipe, so `tput cols` there answers
    # with terminfo's default 80 on a window of any size.
    c=$(stty size <&2 2>/dev/null | awk '{print $2}') || c=''
    [ -n "$c" ] || c=$(tput cols <&2 2>/dev/null) || c=''
  fi
  case "$c" in ''|*[!0-9]*) c="${COLUMNS:-80}" ;; esac
  case "$c" in ''|*[!0-9]*) c=80 ;; esac
  printf '%s' "$c"
}
COLS=$(measure_cols)
BAR_FRAME=0

bar_on() { [ "$TTY" = 1 ] && [ "$VERBOSE" = 0 ]; }

bar_draw() {
  bar_on || return 0
  [ $# -gt 0 ] && BAR_LABEL="$1"
  BAR_FRAME=$((BAR_FRAME + 1))
  [ "$((BAR_FRAME % 10))" = 0 ] && COLS=$(measure_cols)

  local room lw w fill bar done_cells stop end
  room=$((COLS - 11))
  lw=0
  if [ "$COLS" -ge 60 ]; then
    lw=26
    [ "$((COLS / 3))" -lt "$lw" ] && lw=$((COLS / 3))
  fi
  w=$((room - lw - 2))
  if [ "$w" -lt 10 ]; then lw=0; w=$room; fi
  [ "$w" -lt 10 ] && w=10

  fill=$((PCT * w / 100))
  bar=''; done_cells=0; stop=0
  for esc in $RAMP; do
    end=$(((stop + 1) * w / 12))
    [ "$end" -gt "$fill" ] && end=$fill
    if [ "$end" -gt "$done_cells" ]; then
      bar="$bar$esc"
      while [ "$done_cells" -lt "$end" ]; do bar="$bar$FULL"; done_cells=$((done_cells + 1)); done
    fi
    stop=$((stop + 1))
  done
  while [ "$done_cells" -lt "$fill" ]; do bar="$bar$FULL"; done_cells=$((done_cells + 1)); done
  if [ "$done_cells" -lt "$w" ]; then
    bar="$bar$D"
    while [ "$done_cells" -lt "$w" ]; do bar="$bar$EMPTY"; done_cells=$((done_cells + 1)); done
  fi

  if [ "$lw" -gt 0 ]; then
    printf '\r  %s[%s%s]%s %s%3d%%%s  %s%.*s%s%s' "$D" "$bar" "$D" "$N" "$B" "$PCT" "$N" \
      "$MUTE" "$lw" "$BAR_LABEL" "$N" "${ESC}[K"
  else
    printf '\r  %s[%s%s]%s %s%3d%%%s%s' "$D" "$bar" "$D" "$N" "$B" "$PCT" "$N" "${ESC}[K"
  fi
  BAR_ON=1
}

bar_clear() {
  [ "$BAR_ON" = 1 ] || return 0
  printf '\r%s' "${ESC}[K"
  BAR_ON=0
}

step() {
  STEP_NO=$((STEP_NO + 1))
  local floor=$(((STEP_NO - 1) * 100 / TOTAL_STEPS))
  [ "$floor" -gt "$PCT" ] && PCT="$floor"
  if ! bar_on; then
    printf '%s[%s/%s]%s %s\n' "$ACCENT" "$STEP_NO" "$TOTAL_STEPS" "$N" "$*"
    return 0
  fi
  bar_draw "$*"
}
ok()   { if bar_on; then bar_draw "$*"; else printf '      %s%s%s %s\n' "$G" "$TICK" "$N" "$*"; fi; }
info() { if bar_on; then bar_draw "$*"; else printf '      %s%s%s\n' "$MUTE" "$*" "$N"; fi; }
# Warnings and errors print in BOTH modes: a warning that scrolled past inside a progress bar
# was never delivered.
warn() { bar_clear; printf '      %s%s%s %s\n' "$Y" "$WARN_G" "$N" "$*"; bar_draw; }
die()  { bar_clear; printf '\n  %s%s %s%s\n\n' "$R" "$CROSS" "$*" "$N" >&2; exit 1; }

# Ctrl-C is a normal way to leave an installer and must not leave the terminal mid-frame.
on_interrupt() {
  bar_clear
  [ "$TTY" = 1 ] && { tput cnorm 2>/dev/null || true; }
  printf '\n  %s%s interrupted. Nothing further was installed.%s\n' "$Y" "$WARN_G" "$N" >&2
  exit 130
}
trap on_interrupt INT TERM

TICK_SLEEP=0.1
sleep 0.1 2>/dev/null || TICK_SLEEP=1

# spin LABEL TARGET_PCT COMMAND... - run a command with the bar creeping toward TARGET_PCT,
# keeping its output for the failure path only. pip's resolver prints forty lines nobody
# reads, right up until it fails and every one of them matters.
spin() {
  local label="$1" target="$2" was="$PCT" pid ticks=0
  shift 2
  if ! bar_on; then
    info "$label..."
    "$@" >>"$LOG_FILE" 2>&1 || return 1
    PCT="$target"
    return 0
  fi
  "$@" >>"$LOG_FILE" 2>&1 &
  pid=$!
  bar_draw "$label"
  while kill -0 "$pid" 2>/dev/null; do
    sleep "$TICK_SLEEP"
    ticks=$((ticks + 1))
    if [ "$((ticks % 5))" = 0 ] && [ "$PCT" -lt "$((target - 1))" ]; then PCT=$((PCT + 1)); fi
    bar_draw "$label"
  done
  if ! wait "$pid"; then
    PCT="$was"
    bar_draw "$label"
    return 1
  fi
  PCT="$target"
  bar_draw "$label"
}

show_log() {
  [ -f "$LOG_FILE" ] || return 0
  bar_clear
  tail -n 25 "$LOG_FILE" | sed 's/^/      /' >&2
}

# ── finding a Python ─────────────────────────────────────────────────────────────────────
# Defined here, above the uninstall branch, because bash runs a function definition when it
# reaches it: an uninstall that calls find_python before this point is "command not found".
py_is_new_enough() {
  "$1" -c "import sys; sys.exit(0 if sys.version_info >= ($PY_MIN_MAJOR, $PY_MIN_MINOR) else 1)" \
    2>/dev/null
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && py_is_new_enough "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

# ── uninstall ────────────────────────────────────────────────────────────────────────────
# The generated `zerotrace-uninstall` is the one implementation: it knows the paths this run
# actually used. This branch runs it, and only falls back when it is missing - which means an
# install from v0.2 or earlier, back when the package went into the user site.
# Only reached when the generated uninstaller is missing: an install from v0.2 or earlier,
# when the package went into the user site - or a machine where ZeroTrace was never installed.
# It says which of the two it found, because "removed it" on a clean machine is a lie that
# makes people go looking for what else the script touched.
legacy_uninstall() {
  local python hooks found=0
  hooks=$(git config --global --get core.hooksPath 2>/dev/null || true)
  case "$hooks" in
    "$HOME_DIR"/*|"$HOME/.zerotrace"/*)
      git config --global --unset core.hooksPath || true
      found=1
      say "  ${G}${TICK}${N} unset the global core.hooksPath" ;;
  esac
  # Only a package in the USER SITE, which is where v0.2 and earlier installed it. Anything
  # else - a virtualenv, an editable checkout a developer is working in, a distribution
  # package - was put there by somebody else, and an uninstaller that removes other people's
  # installs is worse than one that leaves something behind.
  python=$(find_python || true)
  if [ -n "$python" ]; then
    local location user_site
    # `|| true` matters: `set -o pipefail` is on, `pip show` exits 1 when the package is not
    # installed, and an assignment whose command substitution fails ends the script - which is
    # exactly how `--uninstall` died after printing nothing but the banner.
    location=$("$python" -m pip show zerotrace 2>/dev/null | sed -n 's/^Location: //p' || true)
    user_site=$("$python" -m site --user-site 2>/dev/null || true)
    if [ -n "$location" ] && [ -n "$user_site" ] && [ "$location" = "$user_site" ]; then
      "$python" -m pip uninstall --yes --quiet zerotrace >/dev/null 2>&1 || true
      found=1
      say "  ${G}${TICK}${N} removed the zerotrace package from your user site"
    elif [ -n "$location" ]; then
      warn "a zerotrace is installed in $location; this installer did not put it there, so it is left alone"
    fi
  fi
  if [ -d "$HOME_DIR" ]; then
    rm -rf "$HOME_DIR"
    found=1
    say "  ${G}${TICK}${N} removed $HOME_DIR"
  fi
  remove_path_lines && found=1
  if [ "$found" = 0 ]; then
    say "  ${MUTE}nothing to remove: ZeroTrace is not installed for this user.${N}"
  else
    say "  ${MUTE}repositories, their history and their files were not touched.${N}"
  fi
}

# Deletes the marker line and the export line that follows it - the pair this installer
# appends - and nothing else in a file it does not own. Returns 0 only if it removed something.
remove_path_lines() {
  local rc removed=1
  for rc in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.zshrc" "$HOME/.profile" \
            "$HOME/.config/fish/config.fish"; do
    [ -f "$rc" ] || continue
    grep -qF "$MARKER" "$rc" 2>/dev/null || continue
    awk -v marker="$MARKER" 'index($0, marker) {skip = 2} skip > 0 {skip--; next} {print}' \
      "$rc" > "$rc.zerotrace-new" && mv "$rc.zerotrace-new" "$rc"
    say "  ${G}${TICK}${N} removed the PATH entry from $rc"
    removed=0
  done
  return $removed
}

if [ "$UNINSTALL" = 1 ]; then
  banner
  if [ -x "$BIN_DIR/zerotrace-uninstall" ]; then
    if [ "$PURGE" = 1 ]; then exec "$BIN_DIR/zerotrace-uninstall" --purge; fi
    exec "$BIN_DIR/zerotrace-uninstall"
  fi
  legacy_uninstall
  exit 0
fi

banner

if [ "$SELFTEST" = 1 ]; then
  step "Checking this machine"
  ok "python 3.12.0"
  for p in 0 25 50 82; do PCT=$p; bar_draw "resolving and downloading"; done
  warn "a warning drawn while the bar was on screen"
  bar_clear
  say "selftest done"
  exit 0
fi

# ── 1. this machine ──────────────────────────────────────────────────────────────────────
step "Checking this machine"

os_name() {
  case "$(uname -s)" in
    Darwin) echo macos ;;
    Linux)  grep -qi microsoft /proc/version 2>/dev/null && echo wsl || echo linux ;;
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    *) echo unknown ;;
  esac
}
OS="$(os_name)"
[ "$OS" = unknown ] && die "unsupported OS: $(uname -s). See docs/INSTALL.md."
ok "$OS ($(uname -m))"

command -v git >/dev/null 2>&1 || die "git is required (ZeroTrace protects git repos).
      Debian/Ubuntu/WSL   sudo apt install git
      macOS               xcode-select --install"
GIT_VERSION=$(git --version | awk '{print $3}')
ok "git $GIT_VERSION"

install_python() {
  local sudo_cmd=""
  [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && sudo_cmd="sudo"
  if command -v apt-get >/dev/null 2>&1; then
    $sudo_cmd apt-get update -y && $sudo_cmd apt-get install -y python3 python3-venv python3-pip
  elif command -v dnf >/dev/null 2>&1; then $sudo_cmd dnf install -y python3 python3-pip
  elif command -v yum >/dev/null 2>&1; then $sudo_cmd yum install -y python3 python3-pip
  elif command -v pacman >/dev/null 2>&1; then $sudo_cmd pacman -Sy --noconfirm python python-pip
  elif command -v zypper >/dev/null 2>&1; then $sudo_cmd zypper install -y python3 python3-pip
  elif command -v apk >/dev/null 2>&1; then $sudo_cmd apk add --no-cache python3 py3-pip
  elif command -v brew >/dev/null 2>&1; then brew install python@3.12
  else return 1
  fi
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  warn "no Python >= $PY_MIN_MAJOR.$PY_MIN_MINOR - trying to install one"
  mkdir -p "$HOME_DIR"
  spin "installing Python" 12 install_python || {
    show_log
    die "could not install Python $PY_MIN_MAJOR.$PY_MIN_MINOR+ automatically.
      Debian/Ubuntu/WSL   sudo apt install python3 python3-venv
      Fedora              sudo dnf install python3
      macOS               brew install python@3.12"
  }
  PYTHON="$(find_python || true)"
  [ -n "$PYTHON" ] || die "Python was installed but no suitable interpreter is on PATH.
      Open a new terminal and run this script again."
fi
ok "python $("$PYTHON" -c 'import platform; print(platform.python_version())') ${MUTE}($PYTHON)${N}"

# python3-venv is a separate package on Debian and Ubuntu, so a working python3 does not imply
# a working `-m venv`. Finding that out here, with the line that fixes it, beats a stack trace
# halfway through.
"$PYTHON" -c "import venv, ensurepip" 2>/dev/null || die "this Python cannot create virtualenvs.
      Debian/Ubuntu/WSL   sudo apt install python3-venv"
ok "venv available"

# ~400 MB is the environment; the model, if it is wanted, is several GB and lands in Docker's
# own storage, so it is not measured here.
FREE_MB=$(df -Pm "$HOME" 2>/dev/null | awk 'NR==2 {print $4}' || echo "")
case "$FREE_MB" in
  ''|*[!0-9]*) : ;;
  *) if [ "$FREE_MB" -lt 400 ]; then
       die "only ${FREE_MB} MB free in $HOME; ZeroTrace needs about 400 MB."
     fi
     ok "disk ${FREE_MB} MB free" ;;
esac

# ── 2. the environment ───────────────────────────────────────────────────────────────────
step "Creating the environment"
mkdir -p "$HOME_DIR"
: > "$LOG_FILE"

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else printf ''
  fi
}

verify_against_sums() {
  # SHA256SUMS holds "<hash>  <name>" lines. A file we cannot verify is never installed:
  # this script is downloaded over the network and runs unattended.
  local dir="$1" name="$2" want got
  [ -f "$dir/SHA256SUMS" ] || { warn "no SHA256SUMS next to $name; cannot verify it"; return 0; }
  want=$(awk -v n="$name" '$2 == n || $2 == "*" n {print $1}' "$dir/SHA256SUMS" | head -1)
  [ -n "$want" ] || die "$name is not listed in SHA256SUMS; refusing to install it."
  got=$(sha256_of "$dir/$name")
  [ -n "$got" ] || { warn "no sha256 tool on this machine; skipping verification of $name"; return 0; }
  [ "$got" = "$want" ] || die "$name does not match its SHA256SUMS entry.
      expected $want
      got      $got
      Download it again, or install from a clone."
}

download() {
  curl --proto '=https' --tlsv1.2 -fsSL "$1" -o "$2"
}

latest_release_tag() {
  # The redirect on /releases/latest names the tag, and costs no API rate limit.
  local url
  url=$(curl --proto '=https' --tlsv1.2 -fsSLI -o /dev/null -w '%{url_effective}' \
        "$REPO_URL/releases/latest" 2>/dev/null) || return 1
  case "$url" in */releases/tag/*) printf '%s' "${url##*/releases/tag/}" ;; *) return 1 ;; esac
}

# Where does the package come from? A clone next to this script wins, because running
# ./install.sh inside a checkout you have been editing must install THAT checkout.
if [ -z "$LOCAL_DIR" ] && [ -z "$FROM_DIR" ] && [ -z "$REF" ] && [ -z "$WANT_VERSION" ] \
   && [ -f "$0" ]; then
  CANDIDATE=$(cd "$(dirname "$0")" && pwd 2>/dev/null) || CANDIDATE=''
  if [ -n "$CANDIDATE" ] && grep -q '^name = "zerotrace"' "$CANDIDATE/pyproject.toml" 2>/dev/null
  then
    LOCAL_DIR="$CANDIDATE"
    info "installing from this clone ($LOCAL_DIR)"
  fi
fi

SOURCE_KIND=""
if [ -n "$LOCAL_DIR" ]; then
  [ -f "$LOCAL_DIR/pyproject.toml" ] || die "--local: no pyproject.toml in $LOCAL_DIR"
  SOURCE_KIND=clone
  SOURCE_DIR="$LOCAL_DIR"
elif [ -n "$REF" ]; then
  SOURCE_KIND=clone
  SOURCE_DIR="$HOME_DIR/src"
  rm -rf "$SOURCE_DIR"
  spin "cloning $REPO_URL@$REF" 20 git clone --depth 1 --branch "$REF" "$REPO_URL.git" \
    "$SOURCE_DIR" || { show_log; die "could not clone $REPO_URL at $REF"; }
elif [ -n "$FROM_DIR" ]; then
  SOURCE_KIND=release
  ASSET_DIR="$FROM_DIR"
  [ -d "$ASSET_DIR" ] || die "--from: $ASSET_DIR is not a directory"
else
  SOURCE_KIND=release
  ASSET_DIR="$HOME_DIR/download"
  TAG="${WANT_VERSION:-${RELEASE_VERSION:-}}"
  if [ -z "$TAG" ]; then
    TAG=$(latest_release_tag) || die "could not find a published release at $REPO_URL.
      If the repository is private, clone it and run ./install.sh from there,
      or pass --ref main to build from source."
  fi
  case "$TAG" in v*) : ;; *) TAG="v$TAG" ;; esac
  rm -rf "$ASSET_DIR"
  mkdir -p "$ASSET_DIR"
  info "release $TAG"
  BASE="$REPO_URL/releases/download/$TAG"
  download "$BASE/SHA256SUMS" "$ASSET_DIR/SHA256SUMS" \
    || die "could not download $TAG. Check the version, or install from a clone."
  WHEEL_NAME=$(awk '$2 ~ /\.whl$/ {print $2}' "$ASSET_DIR/SHA256SUMS" | sed 's/^\*//' | head -1 || true)
  [ -n "$WHEEL_NAME" ] || die "release $TAG has no wheel listed in SHA256SUMS."
  spin "downloading $WHEEL_NAME" 25 download "$BASE/$WHEEL_NAME" "$ASSET_DIR/$WHEEL_NAME" \
    || { show_log; die "could not download $WHEEL_NAME from $TAG"; }
  download "$BASE/requirements-install.txt" "$ASSET_DIR/requirements-install.txt" \
    || die "release $TAG has no requirements-install.txt; install from a clone instead."
fi

if [ "$SOURCE_KIND" = release ]; then
  if [ -z "${WHEEL_NAME:-}" ]; then
    found=$(ls "$ASSET_DIR"/*.whl 2>/dev/null | head -1) || found=''
    [ -n "$found" ] || die "no .whl in $ASSET_DIR"
    WHEEL_NAME=$(basename "$found")
  fi
  verify_against_sums "$ASSET_DIR" "$WHEEL_NAME"
  verify_against_sums "$ASSET_DIR" "requirements-install.txt"
  ok "verified $WHEEL_NAME against SHA256SUMS"
  REQUIREMENTS="$ASSET_DIR/requirements-install.txt"
  PACKAGE="$ASSET_DIR/$WHEEL_NAME"
else
  REQUIREMENTS="$SOURCE_DIR/requirements/install.txt"
  PACKAGE="$SOURCE_DIR"
  [ -f "$REQUIREMENTS" ] || die "$REQUIREMENTS is missing; is $SOURCE_DIR a ZeroTrace checkout?"
fi

# An existing environment is moved aside rather than deleted: if anything below fails, the
# hooks that are already registered keep pointing at a Python that still exists.
RESTORE_VENV=0
if [ -d "$VENV_DIR" ]; then
  rm -rf "$VENV_DIR.old"
  mv "$VENV_DIR" "$VENV_DIR.old"
  RESTORE_VENV=1
  info "replacing the existing environment"
fi

rollback() {
  rm -rf "$VENV_DIR"
  if [ "$RESTORE_VENV" = 1 ] && [ -d "$VENV_DIR.old" ]; then
    mv "$VENV_DIR.old" "$VENV_DIR"
    warn "put the previous install back; nothing on this machine changed"
  fi
}

"$PYTHON" -m venv "$VENV_DIR" >>"$LOG_FILE" 2>&1 || { rollback; show_log
  die "could not create a virtualenv in $VENV_DIR"; }
VENV_PY="$VENV_DIR/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$VENV_DIR/Scripts/python.exe"   # Git Bash builds a Windows venv
[ -x "$VENV_PY" ] || { rollback; die "the virtualenv has no interpreter in it."; }

spin "upgrading pip" 30 "$VENV_PY" -m pip install --upgrade pip \
  || warn "could not upgrade pip; continuing with the bundled one"

# Dependencies first, hash-checked, wheels only: every file is known in advance and nothing
# runs setup code while it installs. PIP_FIND_LINKS / PIP_NO_INDEX are honoured by pip itself,
# which is what makes an air-gapped install work with no extra flag here.
if ! spin "installing dependencies" 55 "$VENV_PY" -m pip install --disable-pip-version-check \
     --require-hashes --only-binary :all: -r "$REQUIREMENTS"; then
  show_log
  rollback
  die "could not install the dependencies. The full log is at $LOG_FILE"
fi

if [ "$SOURCE_KIND" = release ]; then
  INSTALL_ARGS=(--no-deps --no-index --only-binary :all: "$PACKAGE")
else
  INSTALL_ARGS=(--no-deps --no-build-isolation --no-index "$PACKAGE")
fi
if ! spin "installing zerotrace" 62 "$VENV_PY" -m pip install --disable-pip-version-check \
     "${INSTALL_ARGS[@]}"; then
  show_log
  rollback
  die "could not install zerotrace. The full log is at $LOG_FILE"
fi

case "$EXTRAS" in
  *pii-ner*)
    # Presidio and spaCy are large and are NOT in the hash-locked set, so they are installed
    # on their own and a failure here does not fail the install.
    warn "the $EXTRAS extra is not hash-locked; installing it from the index"
    spin "installing the PII NER engine" 66 "$VENV_PY" -m pip install \
      --disable-pip-version-check presidio-analyzer presidio-anonymizer \
      || warn "could not install the NER engine; the regex PII detectors still work" ;;
esac

rm -rf "$VENV_DIR.old"
VERSION=$("$VENV_PY" -m zerotrace version 2>/dev/null | awk '{print $2}' || true)
ok "zerotrace ${VERSION:-installed} in $VENV_DIR"

# ── the launcher, and the uninstaller that knows these paths ─────────────────────────────
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/zerotrace" <<EOF
#!/bin/sh
$MARKER
# Delete this file and $HOME_DIR to remove ZeroTrace, or run zerotrace-uninstall.
exec "$VENV_PY" -m zerotrace "\$@"
EOF
chmod 755 "$BIN_DIR/zerotrace"

# Generated here because only this run knows where everything went, and `install.sh
# --uninstall` runs it - so removal has one implementation rather than two that drift.
cat > "$BIN_DIR/zerotrace-uninstall" <<EOF
#!/bin/sh
$MARKER
# Removes ZeroTrace: the git hooks first (so no repo is left pointing at an interpreter that
# is about to vanish), then the model container, then this installation.
#   --purge   also delete the Ollama image and the downloaded model (gigabytes)
set -u
PURGE=0
for arg in "\$@"; do
  case "\$arg" in
    --purge) PURGE=1 ;;
    --help|-h) echo "usage: zerotrace-uninstall [--purge]"; exit 0 ;;
  esac
done

VENV_PY="$VENV_PY"
HOME_DIR="$HOME_DIR"
BIN_DIR="$BIN_DIR"

if [ -x "\$VENV_PY" ]; then
  echo "zerotrace: removing the git hooks"
  "\$VENV_PY" -m zerotrace uninstall --global || true
  if [ "\$PURGE" = 1 ]; then
    "\$VENV_PY" -m zerotrace model down --purge || true
  else
    "\$VENV_PY" -m zerotrace model down || true
  fi
else
  hooks=\$(git config --global --get core.hooksPath 2>/dev/null || true)
  case "\$hooks" in
    "\$HOME_DIR"/*) git config --global --unset core.hooksPath || true ;;
  esac
fi

echo "zerotrace: removing \$HOME_DIR"
rm -rf "\$HOME_DIR"

for rc in "\$HOME/.bashrc" "\$HOME/.bash_profile" "\$HOME/.zshrc" "\$HOME/.profile" \\
          "\$HOME/.config/fish/config.fish"; do
  [ -f "\$rc" ] || continue
  grep -qF "$MARKER" "\$rc" 2>/dev/null || continue
  awk -v marker="$MARKER" 'index(\$0, marker) {skip = 2} skip > 0 {skip--; next} {print}' \\
    "\$rc" > "\$rc.zerotrace-new" && mv "\$rc.zerotrace-new" "\$rc"
  echo "zerotrace: removed the PATH entry from \$rc"
done

echo "zerotrace: your repositories, their history and their files were not touched."
if [ "\$PURGE" = 0 ]; then
  echo "zerotrace: the Ollama image and the downloaded model were kept (zerotrace-uninstall --purge removes them)."
fi
# Last: a script that deletes itself mid-run still finishes on POSIX, but this reads clearly.
rm -f "\$BIN_DIR/zerotrace" "\$BIN_DIR/zerotrace-uninstall"
echo "zerotrace: done."
EOF
chmod 755 "$BIN_DIR/zerotrace-uninstall"
ok "$BIN_DIR/zerotrace"

# ── PATH ─────────────────────────────────────────────────────────────────────────────────
# Two questions, and answering them with one variable is a bug: "is it on PATH in the shell
# that ran me" and "will it be on PATH in the next one" have different answers on a re-install.
case ":$PATH:" in
  *":$BIN_DIR:"*) USABLE_NOW=1 ;;
  *) USABLE_NOW=0 ;;
esac

profile_add() {
  local profile="$1" line="$2"
  if [ -f "$profile" ] && grep -Fq "$BIN_DIR" "$profile" 2>/dev/null; then return 0; fi
  mkdir -p "$(dirname "$profile")" 2>/dev/null || return 1
  printf '\n%s\n%s\n' "$MARKER" "$line" >> "$profile" 2>/dev/null || return 1
  PROFILES_EDITED="${PROFILES_EDITED:+$PROFILES_EDITED, }$profile"
}

PROFILES_EDITED=''
if [ "$USABLE_NOW" = 0 ] && [ -z "${ZEROTRACE_NO_MODIFY_PATH:-}" ]; then
  POSIX_LINE="export PATH=\"$BIN_DIR:\$PATH\""
  if [ -f "$HOME/.bash_profile" ]; then profile_add "$HOME/.bash_profile" "$POSIX_LINE"
  else profile_add "$HOME/.bashrc" "$POSIX_LINE"; fi
  if command -v zsh >/dev/null 2>&1 || [ -f "$HOME/.zshrc" ]; then
    ZSH_DIR="$HOME"
    case "${ZDOTDIR:-}" in "$HOME"|"$HOME"/*) ZSH_DIR="$ZDOTDIR" ;; esac
    profile_add "$ZSH_DIR/.zshrc" "$POSIX_LINE"
  fi
  if command -v fish >/dev/null 2>&1 || [ -f "$HOME/.config/fish/config.fish" ]; then
    profile_add "$HOME/.config/fish/config.fish" "fish_add_path $BIN_DIR"
  fi
  profile_add "$HOME/.profile" "$POSIX_LINE"
  [ -n "$PROFILES_EDITED" ] && ok "added to PATH in $PROFILES_EDITED"
fi
PATH="$BIN_DIR:$PATH"
export PATH

# ── 3-6. Docker, hooks, model, validation ────────────────────────────────────────────────
# One implementation for every OS: see src/zerotrace/setup.py.
bar_clear
SETUP_ARGS=(setup --step-offset "$STEP_NO" --steps "$TOTAL_STEPS")
[ "$PULL_MODEL" = 0 ] && SETUP_ARGS+=(--no-model)
set +e
"$VENV_PY" -m zerotrace "${SETUP_ARGS[@]}"
SETUP_CODE=$?
set -e

if [ "$SETUP_CODE" != 0 ]; then
  die "the guardrail is not in place. Run \`$BIN_DIR/zerotrace doctor\` to see why, then
      run this installer again. Nothing else on this machine was changed."
fi

if [ "$USABLE_NOW" = 0 ]; then
  say ""
  printf '  %s%s%s to use it in %sthis%s terminal:  %sexport PATH="%s:$PATH"%s\n' \
    "$Y" "$ARROW" "$N" "$B" "$N" "$B" "$BIN_DIR" "$N"
  printf '     %severy new terminal picks it up on its own%s\n' "$MUTE" "$N"
fi
say ""
