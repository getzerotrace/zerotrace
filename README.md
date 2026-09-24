<table align="center" border="0"><tr>
<td valign="middle"><img src="src/zerotrace/ui/assets/logo2.png" alt="ZeroTrace" width="130"></td>
<td valign="middle"><pre>
███████ ███████ ██████   ██████  ████████ ██████   █████   ██████ ███████ 
   ███  ██      ██   ██ ██    ██    ██    ██   ██ ██   ██ ██      ██      
  ███   █████   ██████  ██    ██    ██    ██████  ███████ ██      █████   
 ███    ██      ██   ██ ██    ██    ██    ██   ██ ██   ██ ██      ██      
███████ ███████ ██   ██  ██████     ██    ██   ██ ██   ██  ██████ ███████ 
</pre></td>
</tr></table>

<p align="center"><strong>secret &amp; PII guardrail · no trace. no leaks. stays safe.</strong></p>

[![CI](https://github.com/getzerotrace/zerotrace/actions/workflows/ci.yml/badge.svg)](https://github.com/getzerotrace/zerotrace/actions/workflows/ci.yml) [![License: Apache-2.0](<https://img.shields.io/badge/License-Apache%202.0-blue.svg>)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

A secret in a commit is not a mistake you fix by deleting the line. It is in the reflog, in
every clone, and in the fork someone made this morning — the only real remedy is to revoke the
credential. **ZeroTrace stops it one step earlier**: at `git commit`, on the developer's own
machine, in every repository, from one install.

It is **local-first**. Detection is offline, nothing is uploaded, and the optional local model
only ever sees redacted shape features — never a value.

## Install

```bash
curl -fsSL https://getzerotrace.github.io/zerotrace/install.sh | bash
```

```powershell
irm https://getzerotrace.github.io/zerotrace/install.ps1 | iex
```

That is the whole install. No pip, pipx or uv first: it builds an environment of its own and
never touches your system Python. When it finishes, **every** repository on the machine is
protected — including the ones you clone tomorrow, and commits made by IDEs, GUI clients and AI
coding agents. Removing it is one command too:

```bash
zerotrace-uninstall          # --purge also deletes the model image and weights
```

The installer walks six steps and says what it found at each one:

```
[1/6] Checking this machine     git, Python 3.11+, the venv module, free disk
[2/6] Creating the environment  ~/.zerotrace/venv, hash-locked wheels, verified downloads
[3/6] Checking Docker           installed? running? may this user use it?
[4/6] Installing the git hooks  every repo on this machine, chaining any hooks already there
[5/6] Preparing the local model optional; --no-model skips several GB
[6/6] Validating the install    stages a fake credential and proves the commit is refused
```

Options, air-gapped installs, supported OS versions and the uninstall order:
**[docs/INSTALL.md](docs/INSTALL.md)**.

## What it looks like when it catches something

`git commit` stops, and the reason is on the screen — with the fix already written:

```
                                   Staged findings
╭────┬───┬────────────────────┬─────────────────────────────────┬──────────┬────────╮
│  # │   │ Location           │ Rule                            │ Severity │ Action │
├────┼───┼────────────────────┼─────────────────────────────────┼──────────┼────────┤
│  1 │ █ │ settings.py:3      │ aws-access-key-id               │ critical │ BLOCK  │
│  2 │ █ │ settings.py:4      │ connection-string-with-password │ high     │ BLOCK  │
╰────┴───┴────────────────────┴─────────────────────────────────┴──────────┴────────╯
2 blocking — the commit is held until each one is fixed, excepted, or abandoned

╭──────────────────────────────── BLOCK settings.py:3 ─────────────────────────────────╮
│ aws-access-key-id (critical)                                                         │
│ AWS access key ID: AWS access keys grant programmatic access to cloud resources.     │
│ Once in git history they must be revoked in IAM, not just deleted.                   │
│                                                                                      │
│   3 AWS_ACCESS_KEY_ID = "<AWS_ACCESS_KEY_ID len=20>"                                 │
╰──────────────────────────────────────────────────────────────────────────────────────╯
  fix suggested: AWS_ACCESS_KEY_ID = os.environ["AWS_ACCESS_KEY_ID"]
```

The value is masked even here: no raw secret reaches the terminal, the logs or a model prompt.
Under the finding is a menu — arrow keys, a letter, or the mouse:

| Key   | What it does                                                                                                                                 |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `V` | rewrite it as an env or vault lookup, in the language of the file (`os.environ["X"]`, `process.env.X`, `os.Getenv("X")`, `${X}`, …) |
| `R` | replace it with a safe placeholder, or synthetic PII that will not re-trigger a detector                                                     |
| `U` | unstage the file, add it to`.gitignore`, leave a keys-only `.env.example`                                                                |
| `E` | record a time-bound exception, with a written reason                                                                                         |
| `A` | abort and fix it yourself                                                                                                                    |

Fixes are written to the **staged copy**, and mirrored into your working tree only where the
line still matches — so unrelated unstaged edits are never swept into the commit.

## What it catches

| Layer                                 | Examples                                                                                                                                                              | Default                                  |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| Provider rule pack (26 rules)         | AWS, GitHub, GitLab, OpenAI, Anthropic, Stripe, Slack, Google, GCP, Azure, HuggingFace, Databricks, npm, Vault, DB connection strings, JDBC,`Authorization: Bearer` | **BLOCK**                          |
| Credentials hardcoded in code         | `clientSecret = "…"`, `api_key: str = "…"`, `ENV API_TOKEN=…` — Python, JS/TS, Go, Java/Kotlin, C#, Ruby, PHP, Rust, HCL, YAML, `.properties`             | graded by entropy: BLOCK or AI tie-break |
| Sensitive files                       | `.env`, `id_rsa`, `*.pem` holding a private key, keystores, `terraform.tfstate`, kubeconfig, `.npmrc`, `.git-credentials`                                 | **BLOCK** → unstage               |
| Entropy and keywords (detect-secrets) | random-looking strings, JWTs, private keys                                                                                                                            | MEDIUM → AI tie-break                   |
| PII                                   | emails (internal domains rank higher), phones, PAN, Aadhaar (Verhoeff), cards (Luhn), IBAN                                                                            | WARN/BLOCK → synthetic data             |
| Composed secrets                      | a key assembled from`part_a + part_b` in the same file                                                                                                              | **BLOCK**                          |

Placeholders (`${VAR}`, `<your-key>`, `changeme`, `os.environ[...]`, documented AWS examples),
lockfile hashes and UUIDs are filtered out **before** any decision — which is what keeps the
false-positive rate low enough that nobody starts reaching for `--no-verify`.

Grading rules, severity modifiers and exceptions: **[docs/POLICY.md](docs/POLICY.md)**.

## How it works

One policy engine, **two enforcement points**:

1. **Commit time.** A git hook reads *only the lines being added*, runs the deterministic
   detectors, and asks a small local model about the genuinely ambiguous ones. A typical
   blocked commit takes about 100 ms.
2. **AI runtime.** `zerotrace gateway` runs the same detectors over an AI-agent, MCP-tool or RAG
   payload *before a model sees it*: secrets and PII are masked to typed tokens
   (`<STRIPE_LIVE_KEY len=32>`) and indirect prompt injection is neutralised.

```
staged diff ─► detectors ─► filters ─► policy engine ─► BLOCK / WARN / ALLOW ─► fix menu
                               ▲             │
                        placeholders,   ambiguous only
                        UUIDs, hashes        ▼
                                       local model  (shape features, never the value)
```

**The design contract.** The model never sees a HIGH or CRITICAL finding and can never unblock
one. Any error, timeout, bad response or unreachable endpoint fails **closed**. A **pre-push**
hook re-scans outgoing commits, so a `git commit --no-verify` is still caught before it leaves
the machine.

Module map and data flow: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

### Docker is optional

The AI tie-break runs a small model (Qwen2.5-Coder 3B) in a container. The installer never fails
because of it — it reports what it found and carries on:

| What it finds             | What you get                                                                       |
| ------------------------- | ---------------------------------------------------------------------------------- |
| Docker running            | the image and model are pulled; ambiguous findings are settled by the model        |
| installed, daemon stopped | the command to start it on your OS; HIGH/CRITICAL still block, ambiguous ones WARN |
| permission denied         | the`usermod -aG docker` line, and the warning that comes with it                 |
| not installed             | the install command for your OS, and native Ollama as an alternative               |
| a model already answering | it is used as it is, and nothing is started                                        |

`zerotrace model status` asks again at any time, `zerotrace model up` gets from "Docker is
running" to "the model answers", and `--no-model` skips the step entirely.

## The commands

| Command                                     | What it does                                                                                          |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `zerotrace doctor [-i]`                   | is this machine, and this repo, actually protected?`-i` is full-screen, with the fixes one key away |
| `zerotrace review`                        | resolve a block your IDE or GUI client swallowed — full-screen, or inline with`--classic`          |
| `zerotrace scan --range A..B` / `--all` | the CI and PR backstop, and the onboarding scan (`--format json`)                                   |
| `zerotrace init`                          | a repo`.zerotrace.yml` and a hashed `.secrets.baseline` for what is already in history            |
| `zerotrace model status \| up \| down`      | where the AI tie-break stands, and start or stop it                                                   |
| `zerotrace exceptions [-i]`               | list, promote and revoke exceptions (fingerprints only, never values)                                 |
| `zerotrace gateway`                       | sanitize an AI-agent / MCP-tool / RAG payload read from stdin                                         |
| `zerotrace ui [--tier …]`                | render every screen, to check the terminal you will demo from                                         |

Three of them open a full-screen app when a terminal and the `tui` extra are there (`review`,
`doctor -i`, `exceptions -i`): a table on the left, the finding in full on the right, and every
action reachable by key, by arrows or by mouse. All of it, plus the live demo:
**[docs/USING.md](docs/USING.md)**.

## Docs

|                                                                                         |                                                                                                        |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| [INSTALL.md](docs/INSTALL.md)                                                            | the installer step by step, options, Docker states, offline installs, uninstall, supported OS versions |
| [USING.md](docs/USING.md)                                                                | the commands, the fixes, the full-screen apps, the AI gateway, the demo                                |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md)                                                  | pipeline and module map                                                                                |
| [POLICY.md](docs/POLICY.md) · [PII.md](docs/PII.md)                                      | what is detected, how it is graded, how exceptions work                                                |
| [AI_CLASSIFIER.md](docs/AI_CLASSIFIER.md) · [AWS_INFERENCE.md](docs/AWS_INFERENCE.md)    | the model, the redaction, measured results, moving inference to AWS                                    |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md)                                                      | rolling out to every developer: MDM, org policy, CI backstop, agents, WSL                              |
| [RELEASING.md](docs/RELEASING.md)                                                        | code → version → tag → build → verify → release → install, and what to do when a step fails      |
| [THREAT_MODEL.md](docs/THREAT_MODEL.md) · [SECURITY.md](SECURITY.md) · [ADR/](docs/ADR/) | what it defends against, how to report a hole, the decisions behind it                                 |
| [POSITIONING.md](docs/POSITIONING.md) · [RESEARCH.md](docs/RESEARCH.md)                  | prior art, what is new here, and why this matters when the company already runs a vault                |
| [CONTRIBUTING.md](CONTRIBUTING.md)                                                       | `./scripts/dev_bootstrap.sh`, and the rest of the workflow                                           |

ZeroTrace is a **helpful guardrail, not a security boundary**: pair it with server-side push
protection and credential rotation.

## License

Apache-2.0, see [LICENSE](LICENSE). Contributions are accepted under the same terms.
