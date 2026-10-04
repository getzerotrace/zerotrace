# Security Policy

ZeroTrace runs on every commit with read access to your staged code, and can
optionally invoke a local model. It is a piece of security-critical tooling,
so its own supply chain matters as much as what it detects.

## Reporting a vulnerability
Use GitHub's private vulnerability reporting: open the **Security** tab on this
repository -> **Report a vulnerability**. Do **not** open a public issue for
anything exploitable. We aim to acknowledge within 3 business days.

## Guarantees this tool tries to keep
- **No raw values leave the machine.** Detection is fully offline (`detect-secrets`
  verification disabled). The default model endpoint is loopback-only and bypasses proxies.
  An optional remote endpoint (e.g. company-hosted on AWS) must be explicitly allowed, must
  use https, and only ever receives redacted shape features.
- **One more request, and what is in it.** Once a day, in a terminal, ZeroTrace asks the release
  page whether a newer version exists (a `HEAD` to `/releases/latest`; https only; a user agent
  of `zerotrace/<version>`, nothing about you, your repo or a finding). It is off in CI, off with
  `ZEROTRACE_NO_UPDATE_CHECK=1` or `updates.check: false`, lockable by org policy, and cannot
  fail a commit or hold one up for more than a second. `zerotrace update` runs the installer of
  the release it found only after that file matches the release's `SHA256SUMS`.
- **No plaintext secret persistence.** Nothing writes a raw candidate to disk or
  logs. Baselines and exceptions store salted fingerprints only.
- **Fail closed.** Scanner crash, model timeout, or unparseable model output ->
  block/warn, never silently allow.
- **Pinned integrity.** The served model digest is checked against the pin in
  `.zerotrace.yml` (`zerotrace doctor --pin-model`); on mismatch the model is not used.

## Non-guarantees (be honest)
- A client-side hook can be bypassed (`git commit --no-verify`, direct plumbing). ZeroTrace warns
  about a `--no-verify` commit as soon as it is made (post-commit) and blocks it at the push, but
  `git push --no-verify` skips that last client hook: server-side scanning is the real boundary.
- A 3B local model is **not** a security boundary and can be wrong both ways.
- Detection is best-effort; this is not a compliance certification.
