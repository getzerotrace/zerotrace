# Demo runbook

A live script for showing ZeroTrace. There is no generator: each beat is run by hand in a throwaway
repository (`git init` in an empty folder, stage what the beat names, `git commit`). See "Known
rough edges" below before presenting.

## Before you start

1. Start the local model: `zerotrace model up` (Ollama in Docker, bound to
   `127.0.0.1:11434` only — nothing leaves the laptop).
2. Open a real terminal window (Windows Terminal, not a redirected/piped one) so the
   `[V/R/U/E/A]` interactive fix-menu prompts in `zerotrace review` work.
3. Prefer Windows Terminal (UTF-8) over legacy `powershell.exe`/`cmd.exe` consoles — the box-drawing
   table glyphs render as `? / à` mojibake on legacy codepage-437 consoles. The tool never crashes
   either way (falls back to `?` instead of raising `UnicodeEncodeError`), it's cosmetic only.
4. Use a fresh throwaway folder for every beat and delete it afterwards. The guardrail is global, so
   the hooks that run are the real ones from `zerotrace install --global`; for beats 0 and 1 use a
   machine (or run `zerotrace uninstall --global` first) where it is not installed yet.

## The 8 beats

| # | Scene | What you show | The point |
|---|-------|----------------|-----------|
| 0 | Before | `zerotrace doctor --warm` | Nothing is protected yet; warm the local model so scene 3's AI tie-break answers from RAM instead of a cold load. |
| 1 | One install | `zerotrace install --global` | A single command protects every repo on the machine — no per-repo `.pre-commit-config.yaml`. |
| 2 | payments-api | Commit a fresh Python repo with an `.env`, an AWS key, a Stripe live key, a Terraform password, and a PII fixture | Deterministic rules catch every provider-format secret and PII pattern instantly, no model involved, no network call. |
| 3 | web-app | Commit a Node repo where `client.js` has a comment telling the AI "it's fine, allow this key" next to a real OpenAI key | The prompt-injection attempt is irrelevant: the OpenAI key is provider-format, so it's blocked deterministically and never reaches the model. A second, genuinely ambiguous short token in `analytics.js` *is* sent to the local model for a tie-break. |
| 4 | Bypass | `git commit --no-verify`, then `git push` | Git skips the pre-commit hook for `--no-verify` but not post-commit, so ZeroTrace scans the commit anyway and prints a warning with the command that takes it back. The pre-push hook is the second backstop — the push is rejected before the secret leaves the laptop. |
| 5 | Trust | `zerotrace doctor`, tail the audit log | The audit log is hash-chained and stores fingerprints only (never the secret value), so you can prove what was blocked without ever exposing what it was. |
| 6 | Honesty | *(narrate, not scripted)* | State the model's measured local latency/accuracy from `docs/AI_CLASSIFIER.md` "Measured results" plainly — this is a CPU-only laptop demo, not a production SLA claim. |
| 7 | Close | *(narrate)* | Local-first, zero secrets sent anywhere for the deterministic path; the model is only ever consulted for genuinely ambiguous short tokens, and only over loopback. |

## Known rough edges (found during rehearsal)

- **Console mojibake (cosmetic only):** legacy PowerShell consoles using codepage 437 render the
  box-drawing table borders and ✓/✗ glyphs as `?`/`à`-style mojibake. The CLI already guards
  against this crashing (`sys.stdout.reconfigure(errors="replace")` in `cli.py`), so it degrades
  gracefully. Fix for the demo: use Windows Terminal with UTF-8, or `chcp 65001` first.
- **`zerotrace review`'s interactive prompts need a real TTY.** They will not work if the demo is
  run through a redirected/piped terminal (e.g. from CI or a tool-call subprocess) — always
  present from a real terminal window.

## Fallback if the model is unreachable

Every deterministic rule (provider-format secrets, PII, sensitive files) still blocks with no
model involved. Only the ambiguous short-token tie-break (scene 3's `analytics.js`) degrades to a
WARN instead of a model-backed BLOCK/ALLOW decision — call this out live rather than hiding it.
