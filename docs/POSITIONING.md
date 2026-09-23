# Prior art and what is actually new

Every individual piece of ZeroTrace exists elsewhere. The value is in how they are combined.
Saying so up front makes the project more credible, not less.

## What already exists
- **Deterministic secret/PII hooks:** detect-secrets (Yelp), Gitleaks, trufflehog, git-secrets,
  GitGuardian `ggshield` (which also offers a global-hook install), `pii-secret-check-hooks`.
- **Local PII linters** aimed at committed files (for example `piilint`).
- **Local-LLM second opinion:** public write-ups describe regex-first hooks that send flagged
  hunks to a local `qwen2.5-coder` via Ollama. Their authors conclude that a small local model
  is not a security boundary and can be wrong both ways, and we agree.
- **Vendor block-at-commit** experiences (Checkmarx, GitGuardian) and scanners packaged as
  agent skills.

## Where ZeroTrace is different
1. **Secret + PII in one policy engine**, including India-specific PII (PAN, checksum-validated
   Aadhaar) and internal identifiers (QX-IDs, employee email domains).
2. **Bounded, asymmetric AI.** The model only settles MEDIUM findings, can only allow or
   escalate those, never sees HIGH findings, and never sees values (shape features and masked
   windows only). Its safety is measured (`zerotrace eval`, unsafe-allow rate).
3. **Guided remediation, not pass/fail.** Language-aware env/vault rewrites, unstage +
   gitignore + `.env.example`, synthetic PII, and time-bound reasoned exceptions, applied to
   the index so unrelated work is never staged.
4. **Fleet-ready distribution.** One global/system install with hook chaining, layered org
   policy with locked keys, a pre-push backstop, and a config-only path from laptop inference
   to a company-hosted endpoint.

## The "we already have a vault" objection
Measured: repositories whose CI/CD shows a secrets manager in use still leaked at **5.1%**,
slightly worse than the 4.6% GitHub average. A vault governs values it already holds; it cannot
see a credential being typed into a file, copied into `.env`, or a customer record becoming a
test fixture. See `docs/RESEARCH.md` for the evidence, the gap map and the 30-second answer.

## Honest limits
Client hooks are advisory (`--no-verify`, husky overrides), so server-side scanning stays the
enforcement point. Detection is best-effort, and this is not a DLP platform or a compliance
certification.
