# ADR 0001: detect-secrets for the local hook, Gitleaks for CI

**Status:** accepted

**Context:** both detect secrets and integrate with pre-commit.

**Decision:** use detect-secrets in the local Python hook — it's a library (no
separate binary), emits JSON to our policy engine, supports a hashed baseline,
runs offline, and takes custom filters. Keep Gitleaks/trufflehog for the
server-side CI re-scan (different engine = defense in depth).

**Consequences:** tighter local integration; a second engine to maintain in CI.
