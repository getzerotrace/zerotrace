# Rolling ZeroTrace out to every developer

The goal is that every commit on every managed machine goes through ZeroTrace, with no
per-repo setup and no reliance on developers remembering to opt in.

## 1. How one install covers every repo

`zerotrace install --global` (or `--system`) points git's `core.hooksPath` at a
ZeroTrace-managed directory. Git then runs those hooks for **every** repository: existing clones,
new clones, `git init`, and commits made by IDEs, GUI clients and AI coding agents.
`ggshield install --mode global` and Talisman use the same mechanism.

`core.hooksPath` *replaces* `.git/hooks`, so the managed directory contains a shim for every
hook name, and each shim chains:

1. the repo's own `.git/hooks/<name>` (git-lfs, legacy custom hooks, `pre-commit install`ed
   hooks),
2. any hooks directory that was configured before ZeroTrace (a company-wide hooks dir is kept
   and restored by `zerotrace uninstall`),
3. for `pre-commit`: the repo's `.pre-commit-config.yaml` (the pre-commit framework refuses to
   `install` while `core.hooksPath` is set, so the shim runs it; opt out with
   `ZEROTRACE_CHAIN_PRECOMMIT=0`),
4. ZeroTrace itself, with the terminal reattached so the fix menu works inside `git commit`.

**Known override:** a repo-local `core.hooksPath` (husky v9 sets `.husky/_`) wins over
global/system. This is a gap in coverage, not an alternative install mode: `zerotrace doctor`
flags these repos and `zerotrace doctor --fix` patches `.husky/pre-commit` (or the effective
hooks dir) in place, in addition to - never instead of - the global/system install. The
server-side backstop (§5) covers anything that slips through regardless.

## 2. Packaging

| Channel | Command |
|---|---|
| Internal PyPI (Artifactory/Nexus) | `pipx install zerotrace` or `uv tool install zerotrace`, then `zerotrace install --global` |
| No Python on laptops | signed single-file builds (PyInstaller) for Windows/macOS/Linux, wrapped as a winget/Intune package, Homebrew tap, Jamf script or apt/rpm |
| Dev containers / Codespaces / VDI | bake `pip install zerotrace && zerotrace install --system` into the golden image or a devcontainer feature |

The base install is intentionally light: `detect-secrets`, `rich` and `pyyaml`. The model client
uses only the standard library. Presidio/spaCy NER is an opt-in extra (`zerotrace[pii-ner]`).

## 3. Fleet rollout with MDM (Intune / Jamf / SCCM / Ansible)

```bash
pipx install --global zerotrace==<pinned>      # or deploy the signed binary
zerotrace install --system                     # machine-wide core.hooksPath
install -m 0644 policy.yml /etc/zerotrace/policy.yml   # %ProgramData%\zerotrace\policy.yml on Windows
```

Example org policy. Keys listed under `locked` can't be weakened by user or repo config:

```yaml
locked: [enabled, policy.block_severity, model.endpoint, model.allow_remote]
enabled: true
policy:
  block_severity: [critical, high]
model:
  runtime: openai                       # or ollama for per-laptop inference
  endpoint: https://zerotrace-inference.internal.example
  allow_remote: true
  auth_env: ZEROTRACE_MODEL_TOKEN
  digest: "sha256:…"
rules:
  extra: [/etc/zerotrace/rules-acme.yml] # internal token formats, employee-ID patterns
```

The layer order is built-in defaults ← org policy ← `~/.zerotrace/config.yml` ← repo
`.zerotrace.yml`, and org-locked keys win. A `critical` finding can never be configured to pass.

## 4. The model tier

| Tier | When | Notes |
|---|---|---|
| Off | minimal installs | MEDIUM findings WARN (fail closed); everything deterministic still blocks |
| Local Ollama (Docker or native) | today, per laptop | loopback only; native Ollama uses the GPU (Metal on macOS) |
| Company inference endpoint (AWS) | target | laptops need no Docker and no model download; see `docs/AWS_INFERENCE.md` |

Only redacted shape features and masked code reach any model (see `docs/AI_CLASSIFIER.md`).

## 5. Server-side backstop (client hooks are advisory)

`--no-verify`, a husky override, or a machine without ZeroTrace can all bypass a client hook.
Enforce on the server as well:

- An org-wide GitHub ruleset or required workflow running `zerotrace scan --range
  ${{ github.event.pull_request.base.sha }}..${{ github.sha }} --no-model` (see
  `.github/workflows/secret-scan.yml`), plus Gitleaks as a second, independent engine.
- GitHub Secret Scanning push protection, GitLab secret push protection, or pre-receive hooks.
- Credential rotation runbooks. A blocked push still means the secret exists on a laptop.

## 6. AI coding agents

Agents commit through git, so the global hook covers them automatically, and `zerotrace gateway`
guards what an agent *reads* (masking secrets/PII and neutralising indirect prompt injection).

For catalogue distribution, `skill/` packages ZeroTrace as a **vendor-neutral skill** — a
documented CLI (scan, explain, propose, never auto-apply) with a machine-readable manifest
(`skill/skill.json`). `marketplace/` holds the BMW skills-marketplace entry and the submission
checklist. The same two commands are what an MCP adapter would expose, if the marketplace
prefers MCP.

## 7. WSL

Git for Windows and each WSL distro are **separate git installs with separate global
config** — a Windows-side `zerotrace install --global` protects nothing inside WSL, and
vice versa. `zerotrace doctor` detects which side it's running on (Windows, WSL1, WSL2,
plus the distro name and whether interop is enabled) and reports it, and it warns if the
current repo lives on a Windows drive mounted into WSL (`/mnt/<drive>`), since the exec
bit and line endings a hook needs don't reliably survive there; `zerotrace install`
refuses to write hook scripts onto such a path rather than produce a broken hook that
fails only at commit time.

**Today this means:** run `zerotrace install --global` once in Windows and once in each
WSL distro that will commit. **Not yet built:** a single Windows-side command that
enumerates and bootstraps every registered distro, and a mechanism (a scheduled task is
the current plan) to catch a distro created *after* that command ran — until then, a new
`wsl --install -d <name>` distro is unprotected until someone runs `zerotrace install`
inside it. A WSL-side hook is never allowed to shell out to the Windows binary through
interop (or vice versa): interop can be disabled per-distro or by fleet policy, and a hook
that depends on it becomes a silent, hard-to-diagnose no-op.

## 8. Governance and metrics (roadmap)

- **Done:** exceptions live in a committed, reviewable `.zerotrace-exceptions.json`
  (`zerotrace exceptions`, `--promote`, `--prune`); fingerprints only, and every entry expires.
- Opt-in fleet telemetry: counts of blocked/fixed findings by rule, never values, for a
  "leaks prevented" dashboard.
- Signed releases and hash-locked dependencies, because a tool that reads every commit is itself
  a supply-chain target.
