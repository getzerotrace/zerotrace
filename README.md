```
   ▄                  ▄
  ██▄       ▄        ▄█▄
  ███▄    ▄████▄    ▄███
  ██▄▀▀██████▀ ▀███▀ ██    █████  █████  ████   █████  █████  ████    ███   █████  █████
   ▀██▄██████    █▄▄██▀       ██  ██     ██ ██  ██ ██    ██   ██ ██  ██ ██  ██     ██
     ███████  ▄▄  ██▀        ██   ████   ████   ██ ██    ██   ████   █████  ██     ████
     ███████ ███████        ██    ██     ██ ██  ██ ██    ██   ██ ██  ██ ██  ██     ██
    ▄████▀████▀▀▀███▄      █████  █████  ██ ██  █████    ██   ██ ██  ██ ██  █████  █████
    ▀████▄████▄▄█████▀
    ███████▄▄ ▀█▀████      secret & PII guardrail
    ▀▀███████ ▄▄ ██▀▀      commits · AI agents · local-first
     ▄███████▄██▀▀█▄
      ▀█████▀  ▄▄█▀
        ▀▀▀▀▀▀▀▀
```

[![CI](https://github.com/getzerotrace/zerotrace/actions/workflows/ci.yml/badge.svg)](https://github.com/getzerotrace/zerotrace/actions/workflows/ci.yml)
[![License: Apache-2.0](<https://img.shields.io/badge/License-Apache%202.0-blue.svg>)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

**Leave zero trace.** Secrets and personal data are caught *at commit time*, on the developer's
machine, and fixed before they ever reach git history — in every repository, with one install.

## Install

```bash
curl -fsSL https://getzerotrace.github.io/install.sh | bash
```

```powershell
irm https://getzerotrace.github.io/install.ps1 | iex
```

That is the whole install. No pip, pipx or uv first — it builds an environment of its own and
never touches your system Python. When it finishes, **every** repository on the machine is
protected, including ones you clone tomorrow, and commits made by IDEs, GUI clients and AI
coding agents.

```bash
zerotrace-uninstall          # removes exactly what was installed; --purge also drops the model
```

Options, air-gapped installs, supported OS versions and what each of the six install steps does:
[docs/INSTALL.md](docs/INSTALL.md).

## How it works

Two enforcement points, one policy engine:

1. **Commit time** — a git hook inspects *only the lines being added*, finds secrets and PII with
   deterministic detectors, lets a small local LLM settle the *ambiguous* cases (it sees only
   redacted "shape" features, never the value), explains the risk, and applies a
   developer-approved fix to the staged copy.
2. **AI runtime** — `zerotrace gateway` runs the same detectors over an AI-agent, MCP-tool or RAG
   payload *before a model sees it*, masking secrets and PII and neutralising indirect prompt
   injection.

**Design contract:** deterministic first. The model can never unblock a high-confidence secret,
and any error fails **closed**.

| It catches | Such as | Default |
| --- | --- | --- |
| Provider-format credentials | AWS, GitHub, OpenAI, Anthropic, Stripe, Slack, Azure, GCP, Vault, DB URIs with passwords | **block** |
| Credentials hardcoded in code or config | `clientSecret = "…"`, `ENV API_TOKEN=…`, in 9 languages plus HCL, YAML and `.properties` | block, or AI tie-break |
| Sensitive files | `.env`, `id_rsa`, `*.pem`, keystores, `terraform.tfstate`, kubeconfig | **block** → unstage |
| PII | emails, phones, PAN, Aadhaar (Verhoeff), cards (Luhn), IBAN | warn or block → synthetic data |

Placeholders (`${VAR}`, `<your-key>`, `changeme`), lockfile hashes and UUIDs are filtered out
before any decision. The full table, the severity modifiers and how exceptions work are in
[docs/POLICY.md](docs/POLICY.md).

## Day to day

```bash
git commit -m "…"       # a finding is explained, and fixed, without leaving the commit
zerotrace review        # resolve a block your IDE or GUI client swallowed
zerotrace doctor        # is this machine, and this repo, actually protected?
zerotrace model status  # where the AI tie-break stands
```

Every command, the fix menu, the three full-screen apps, the gateway and the live demo:
[docs/USING.md](docs/USING.md).

### Docker is optional

The AI tie-break runs a small model in a container. The installer never fails because of it: it
reports what it found and carries on. With no Docker, HIGH and CRITICAL findings still block
deterministically and ambiguous ones warn — it fails closed, never open. `zerotrace model up`
gets from "Docker is running" to "the model answers"; `--no-model` skips the step entirely.
Each Docker state, and what it costs you, is in
[docs/INSTALL.md](docs/INSTALL.md#docker).

## Docs

| | |
| --- | --- |
| [INSTALL.md](docs/INSTALL.md) | the installer step by step, options, Docker states, offline installs, uninstall, supported OS versions |
| [USING.md](docs/USING.md) | the commands, the fixes, the full-screen apps, the AI gateway, the demo |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | pipeline and module map |
| [POLICY.md](docs/POLICY.md) · [PII.md](docs/PII.md) | what is detected, how it is graded, exceptions |
| [AI_CLASSIFIER.md](docs/AI_CLASSIFIER.md) · [AWS_INFERENCE.md](docs/AWS_INFERENCE.md) | the model, redaction, measured results, moving inference to AWS |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | rolling out to every developer: MDM, org policy, CI backstop, agents, WSL |
| [RELEASING.md](docs/RELEASING.md) | code → version → tag → build → verify → release → install, and what to do when a step fails |
| [THREAT_MODEL.md](docs/THREAT_MODEL.md) · [SECURITY.md](SECURITY.md) · [ADR/](docs/ADR/) | what it defends against, how to report a hole, the decisions behind it |
| [POSITIONING.md](docs/POSITIONING.md) · [RESEARCH.md](docs/RESEARCH.md) | prior art, what is new here, and why this matters when the company already runs a vault |
| [CONTRIBUTING.md](CONTRIBUTING.md) | `./scripts/dev_bootstrap.sh`, and the rest of the workflow |

ZeroTrace is a **helpful guardrail, not a security boundary**: pair it with server-side push
protection and credential rotation.

## License

Apache-2.0, see [LICENSE](LICENSE). Contributions are accepted under the same terms.
