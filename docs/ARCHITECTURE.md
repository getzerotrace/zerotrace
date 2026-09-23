# Architecture

## One question, two enforcement points
> *Is this content safe to hand on — to git history, or to a model — and if not, what is the
> safest fix?*

The same detectors, post-processing and policy engine serve both:

| Entry point | Input | Outcome |
|---|---|---|
| `zerotrace run` (git hook) | the staged diff | BLOCK / WARN / ALLOW + a human-approved fix |
| `zerotrace gateway` | an AI-agent / MCP-tool / RAG payload on stdin | verdict + **sanitised text** safe to forward |

Detection runs cheap and deterministic first. The LLM is a last-resort tie-breaker for
ambiguous findings, and it only ever sees redacted features.

```
git commit ─► global core.hooksPath shim (chains repo/previous hooks, reattaches /dev/tty)
   └─► zerotrace run --hook
        1. collectors/staged_diff.py      staged diff -> added lines + window (also: a commit, a tree)
        2. detectors/
             sensitive_files.py           .env, keys, keystores, tfstate, kubeconfig (whole file)
             rulepack.py + rules/*.yml    provider formats: AWS, GitHub, OpenAI, Stripe, DB URIs…
             code_assign.py               <credential identifier> = "<literal>", any language
             secrets.py                   detect-secrets (entropy, keywords, JWT, private keys)
             pii.py                       email/phone/QX-ID/PAN/Aadhaar/card/IBAN (+ opt-in Presidio)
        3. pipeline.postprocess           placeholder/UUID/lockfile filters, test/docs downgrade,
                                          .secrets.baseline, dedupe (highest severity per line)
        4. classifier/batch.py            MEDIUM only: concurrent, cached, one deadline
             redact.py -> prompt.py -> llm.py (Ollama | OpenAI-compatible) -> schema.py
        5. policy/engine.py               BLOCK / WARN / ALLOW (the only decision point)
        6. ui/terminal.py + ui/menu.py    explain -> preview -> [V/R/U/E/A] menu (arrows, letter, click)
           ui/tui*.py (zerotrace review) the same fixes full-screen; tui_common.py is the shared frame
        7. remediation/applier.py         patch the INDEX blob; mirror to work tree if identical
        8. audit/                         hash-chained log + exceptions in .git/zerotrace/
git push ─► pre-push shim ─► zerotrace pre-push   (every outgoing commit, deterministic only)

agent/tool payload ─► zerotrace gateway
        1. gateway/payload.py             arbitrary text -> the same Unit list the collectors emit
        2. the detectors above, plus
             confidentiality.py           INTERNAL ONLY / RESTRICTED classification markers
             prompt_injection.py          instruction override, role hijack, exfiltration,
                                          and classifier-hijack attempts aimed at our own tie-break
        3. pipeline.postprocess + policy/engine.py   (shared, unchanged)
        4. gateway/__init__.py            masks each finding in place and returns
                                          GatewayResult(verdict, sanitized_text, decisions)
```

Findings whose remediation is per value — PII, prompt injection, confidentiality — are kept
per value in `dedupe`, not collapsed to one per line: at the gateway each one drives its own
masking action, so collapsing them would silently forward an injected instruction that shared a
line with a secret.

## Module map
- `installer.py`: global/system/repo install, hook shims, chaining, uninstall/restore.
- `platform_env.py`: detects Windows/macOS/Linux/WSL1/WSL2 and classifies a repo path as
  native, DrvFs (`/mnt/<drive>`) or a `\\wsl$\` UNC path; `installer.py` and `doctor.py` both
  call it so they never disagree about where they're running.
- `config.py`: layered config (defaults ← org ← user ← repo) with org-locked keys.
- `collectors/`: diff → `Unit(path, file_class, line_no, text, window, rev)` + `Changeset`.
- `detectors/`: each returns `Finding(rule_id, kind, severity, confidence, …)`. Detectors
  never decide policy.
- `pipeline.py`: one path for `run`, `review`, `scan`, `pre-push` and the gateway.
- `gateway/`: runtime enforcement point. `payload.py` turns arbitrary text (JSON, prose, tool
  output) into `Unit`s; `__init__.py` masks findings and returns a verdict plus the full
  decision list for the audit log. It sanitises rather than blocks, so agent workflows continue.
- `policy/engine.py`: pure decision function with asymmetric model trust (ADR 0002).
- `classifier/`: optional. `redact.py` runs before anything else in here.
- `remediation/`: language-aware proposals, index-safe application, unstage + gitignore.
- `doctor.py`, `evals/`: health checks and classifier measurement.
- `ui/logo.py`, `ui/progress.py`, `ui/capability.py`: terminal-capability detection with ASCII
  fallbacks, so output degrades instead of raising on legacy consoles.

## Where state lives
Audit log, exceptions and the verdict cache live in `.git/zerotrace/`, inside the git dir, so a
global install never leaves untracked files in anyone's work tree. The repo policy
(`.zerotrace.yml`) and `.secrets.baseline` (hashes only) are committed and reviewed like code.

## Interactivity
Git runs hooks with stdout on the terminal but not always stdin. The shim reattaches
`/dev/tty` when a terminal exists, so the fix menu appears inside `git commit`. IDEs and GUI
clients get a headless report plus `zerotrace review`. An approval is never guessed.

## Performance budget
- Added lines only; provider rules are keyword-prefiltered.
- A typical blocked commit takes about 100 ms (measured in the demo). The model loads lazily,
  only when a MEDIUM finding exists, and stays warm with `keep_alive`. Verdicts are cached per
  finding fingerprint.
- Presidio/spaCy is never imported unless opted in (importing it costs seconds).
