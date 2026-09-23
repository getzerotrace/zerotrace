# ADR 0003: Global `core.hooksPath` as the distribution mechanism

**Status:** accepted

**Context:** a pre-commit hook installed per repository (`.pre-commit-config.yaml` +
`pre-commit install`) only protects repos whose owners opt in and every clone that ran the
install. That defeats the purpose of a leak-prevention control. Developers have dozens of repos,
and the risky commit is usually in the one nobody configured.

**Decision:** ZeroTrace installs a managed hooks directory and sets `core.hooksPath` at
`--global` (per user) or `--system` (per machine, via MDM) scope. Every hook name gets a shim that
chains the repo's own hook and any previously configured hooks directory, then runs ZeroTrace for
`pre-commit` and `pre-push`. The pre-commit shim reattaches `/dev/tty`, so interactive remediation
works inside `git commit`; with no terminal it falls back to a headless report.

**Alternatives considered:**
- *pre-commit framework per repo*: opt-in and per clone. We keep `.pre-commit-hooks.yaml` for
  teams that standardise on it.
- *`init.templateDir`*: only affects future clones and is silently skipped by existing repos.
- *IDE plugin only*: misses CLI, GUI clients and agents.

**Consequences:**
- A repo-local `core.hooksPath` (husky) overrides us. `doctor` detects it and `doctor --fix`
  patches that one repo in addition to, never instead of, the global/system install - there is
  no supported "install into just this repo" mode, since that would recreate the opt-in gap
  this ADR exists to close.
- `pre-commit install` refuses to run while `core.hooksPath` is set, so the shim runs
  `.pre-commit-config.yaml` itself.
- The hook still depends on the client, so it is advisory. The server-side scan remains the
  enforcement point (ADR 0001).
