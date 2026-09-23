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
- **No plaintext secret persistence.** Nothing writes a raw candidate to disk or
  logs. Baselines and exceptions store salted fingerprints only.
- **Fail closed.** Scanner crash, model timeout, or unparseable model output ->
  block/warn, never silently allow.
- **Pinned integrity.** The served model digest is checked against the pin in
  `.zerotrace.yml` (`zerotrace doctor --pin-model`); on mismatch the model is not used.

## Non-guarantees (be honest)
- A client-side hook can be bypassed (`git commit --no-verify`, direct plumbing).
- A 3B local model is **not** a security boundary and can be wrong both ways.
- Detection is best-effort; this is not a compliance certification.
