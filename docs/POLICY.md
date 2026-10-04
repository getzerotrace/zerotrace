# Policy engine

`policy/engine.py` is the single source of the pass/fail signal. It is a pure function that is
fully unit-tested.

| Finding | Examples | Action |
|---|---|---|
| Provider-format credential | AWS, GitHub, OpenAI, Anthropic, Stripe live, Slack, Vault, private keys | **Block** (critical) |
| Credential in config / code, random-looking | `clientSecret = "<28 random chars>"`, DB URI with password, `ENV API_TOKEN=…` | **Block** (high) |
| Sensitive file staged | `.env`, `id_rsa`, `*.pem` with a private key, keystores, `terraform.tfstate` | **Block** → [U]nstage |
| Internal PII | employee email domain, QX-ID, PAN, Aadhaar, card, IBAN | **Block** (high) |
| Ambiguous value | short token, dev-looking secret, entropy-only string, credential in a test file | **AI tie-break** → allow / warn / block |
| Generic PII | customer-looking email, phone | **Warn** (confirm or replace) |
| Placeholder / public example / known test card | `${VAR}`, `<your-key>`, `changeme`, `4242…` | Allow (recorded) |

Modifiers:
- **Test/docs paths** lower heuristic findings one level. Provider formats and PII stay.
- **Exceptions** (`[E]`) are time-bound (`exceptions.ttl_days`), need a reason, and are scoped to
  the exact line (they auto-expire if the line changes). They are written locally first
  (`.git/zerotrace/`), then promoted into the committed, PR-reviewed
  `.zerotrace-exceptions.json` with `zerotrace exceptions --promote`. Both stores hold
  fingerprints only, never values, and `zerotrace exceptions --prune` drops expired entries.
- **`.secrets.baseline`** (hashed) suppresses reviewed pre-existing values for every detector.
- `critical` is always in `block_severity`, and org policy can lock the rest.
- **`updates.check`** (default `true`) is the daily lookup for a newer release and the one-line
  notice it feeds ([docs/INSTALL.md](INSTALL.md#updating)). A user or a repo can set it to
  `false` for themselves; an organisation that owns the installed version locks it off
  (`locked: [updates.check]` with `updates: {check: false}`), after which it cannot be turned
  back on below the org layer and `zerotrace update` declines to run.
