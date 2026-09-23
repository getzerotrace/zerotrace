# Why a commit-time guardrail matters when the company already has a vault

Research backing ZeroTrace's positioning. Every number below is sourced; read the objection
section before any demo, because "we already use AWS Secrets Manager / CyberArk" is the first
question a serious reviewer asks.

---

## 1. The one-sentence answer

> A vault controls **where secrets live and who may fetch them**. It has no opinion on **what a
> developer types into a file**. ZeroTrace guards the moment a human or an AI agent writes a
> credential into source, which is precisely the moment vaults do not observe.

The evidence is blunt: in a study of **2,584 repositories whose CI/CD configuration showed a
secrets manager in use, 5.1% still leaked at least one secret — *higher* than the 4.6% GitHub
average**.¹ Adopting a vault does not, on its own, move the leak rate. It changes where the
authoritative copy lives; it does not stop a copy being pasted somewhere else.

## 2. Scale: the problem is growing, not shrinking

| Finding                                                                       | Figure                                                                                          | Source                     |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | -------------------------- |
| New hardcoded secrets on public GitHub in 2025                                | **28.65 million**, +34% YoY, the largest jump recorded                                    | GitGuardian 2026²         |
| Growth since 2021                                                             | leaks**+152%** vs developer base +98%                                                     | GitGuardian 2026²         |
| Internal/private repositories containing a secret                             | **32.2%** of internal repos (≈6× public); 35% of private repos in the prior year's data | GitGuardian 2026², 2025³ |
| AWS IAM keys in private repos                                                 | **8%** of private repos, 5× the public rate                                              | GitGuardian 2025³         |
| Leaked secrets still valid years later                                        | **64%** of 2022's valid secrets were still valid in Jan 2026 (70% in the prior report)    | GitGuardian 2026², 2025³ |
| Incidents originating**outside** repositories (Slack, Jira, Confluence) | **28%**, and 13 points more likely to be critical                                         | GitGuardian 2026²         |
| AI-assisted commits                                                           | **3.2% leak rate vs 1.5% baseline** (~2×)                                                | GitGuardian 2026²         |
| AI-service credentials leaked                                                 | **1.27M**, +81% YoY; 8 of the 10 fastest-growing detectors                                | GitGuardian 2026²         |
| Secrets in MCP config files                                                   | **24,008** unique secrets, 8.8% still valid                                               | GitGuardian 2026²         |
| Machine identities vs humans                                                  | **82:1**, ~half with privileged access                                                    | CyberArk 2025⁴            |

Two implications for our pitch:

1. **Private is not safe.** Most enterprise repos are private, and private repos leak *more*, not
   less, because people relax when "only we can see it". Every argument that begins "but it is an
   internal repo" is refuted by this row.
2. **AI coding agents double the rate.** This is the newest part of the problem, and ZeroTrace
   covers agent commits automatically because agents commit through git.

## 3. What a vault actually solves, and what it leaves open

AWS Secrets Manager, Azure Key Vault, CyberArk Conjur and HashiCorp Vault solve **storage,
access control, distribution, rotation and audit** for secrets that are *already* under
management. The gaps are the human paths around them:

| Gap                                               | Why it survives a vault                                                                                                                | Does ZeroTrace help?                                                                  |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| **Secret zero / bootstrap**                 | Something must authenticate*to* the vault. That bootstrap credential often ends up in a config file, a Dockerfile or a CI variable⁵ | **Yes** — it is a credential in a file at commit time                          |
| **Local development**                       | Developers copy a value out of the vault into `.env`, `settings_local.py`, a notebook or a launch config to work offline            | **Yes** — `.env` is unstaged and a keys-only `.env.example` is generated   |
| **Test fixtures with production-like data** | Vaults hold credentials, not customer records. Nothing stops a support ticket's data becoming a fixture                                | **Yes, uniquely** — PII detection (email, phone, PAN, Aadhaar, cards, IBAN)    |
| **Legacy and glue code**                    | Scripts, Terraform, Dockerfiles, RPA workflows and one-off jobs predate the vault or skip it under deadline                            | **Yes** — detection across 10+ languages and config formats                    |
| **Third parties and contractors**           | The Toyota leak came from a*subcontractor* publishing code containing a key⁶                                                        | **Yes, if installed on their machine**; otherwise the CI/server scan is the net |
| **Collaboration tools**                     | 28% of incidents are in Slack/Jira/Confluence²                                                                                        | **No** — out of scope, and we say so                                           |
| **Rotation, expiry, least privilege**       | Exactly what vaults are for                                                                                                            | **No** — complementary, not competing                                          |

**The honest framing for a judge:** ZeroTrace does not replace a vault. A vault is the
destination; ZeroTrace is the guardrail on the road that stops the value ending up somewhere
else along the way. An organisation with a mature vault still leaked at 5.1%.¹ 

## 4. The RPA case (directly relevant to the other team you mentioned)

The pattern described — **credentials placed in a repository by one developer, pulled from that
repo by a UiPath automation at run time** — is an anti-pattern by UiPath's own documentation.
The supported design is Orchestrator **Credential assets** (AES-256 encrypted, and hideable from
the RPA developer who consumes them), and for higher assurance a **credential store** backed by
CyberArk, Azure Key Vault, HashiCorp Vault, AWS Secrets Manager, BeyondTrust or Thycotic.⁷

Why this matters for us:

- A private repo holding bot credentials is a **shared, long-lived, rarely-rotated** secret store
  with no expiry and read access for everyone with repo access, including CI and any AI agent
  wired into it.
- Those credentials usually belong to a **service account with broad rights** (ERP, mail,
  finance systems), and the 82:1 machine-identity ratio⁴ is exactly this population.
- ZeroTrace blocks that commit at the source and names the correct fix. This is a real,
  in-house example to cite on stage: *"a team here is doing X today; here is what our hook does
  the moment they try it."* Say it as a pattern found in the wild, not as an accusation, and do
  not name the team or show their repo.

## 5. What it costs when this goes wrong

- **Toyota:** an access key in a public repo for **five years**; **296,019** customer records
  exposed; the key was never rotated in that window.⁶
- **Mercedes-Benz:** a single leaked GitHub token exposed the **entire internal GitHub Enterprise
  source**, including further API keys, cloud keys, connection strings and SSO passwords —
  leaked Sept 2023, found Jan 2024, roughly **four months** of unmonitored access.⁶
- Both illustrate the asymmetry we are selling: **seconds to prevent, months to detect, and a
  rotation/forensics/notification bill afterwards.** Rotation is rarely one command — it is
  finding every consumer of a shared credential, coordinating a change window, and proving no
  access occurred, which is why **64% of leaked secrets are still valid years later.**²

## 6. Where we sit against existing tools

| Tool                        | Stops it before commit                                                                         | Guided fix                    | PII                                 | Works in every repo without opt-in |
| --------------------------- | ---------------------------------------------------------------------------------------------- | ----------------------------- | ----------------------------------- | ---------------------------------- |
| GitHub push protection      | At push, and only for**partner patterns**; generic secrets were **58%** of leaks³ | No                            | No                                  | Only on GitHub                     |
| Gitleaks / trufflehog in CI | No — after the secret is in history                                                           | No                            | No                                  | Per-pipeline                       |
| GitGuardian / vendor hooks  | Yes (commercial)                                                                               | Partial                       | Limited                             | Per-repo install                   |
| Vault / Secrets Manager     | No — different layer                                                                          | N/A                           | No                                  | N/A                                |
| **ZeroTrace**         | **Yes, at commit, in every repo from one install**                                       | **Yes, language-aware** | **Yes, incl. India-specific** | **Yes (global hooks path)**  |

Our defensible combination, stated plainly: **one machine-wide install, secrets *and* PII in one
policy engine, a bounded local AI that never sees the value, and a fix rather than a failure.**

## 7. Scenarios to demo or test (each maps to a fixture we already ship)

| #  | Scenario                                                                   | Vault in place?                  | What ZeroTrace does                                  |
| -- | -------------------------------------------------------------------------- | -------------------------------- | ---------------------------------------------------- |
| 1  | Live Stripe key pasted into `payments/charge.py` while debugging a refund | Yes, unused in the moment        | Blocks; rewrites to `os.environ[...]`               |
| 2  | `DATABASE_URL` with a password in a connection string                    | Yes, but this is glue code       | Blocks; rewrites, names `DATABASE_URL`              |
| 3  | `.env` staged by accident                                                | Irrelevant — it is a local file | Unstages, gitignores, writes `.env.example`         |
| 4  | Internal employee email + employee ID in a test fixture                    | **Vault does nothing**     | Blocks; synthetic replacement                        |
| 5  | Aadhaar/PAN/card number in fixtures or logs                                | **Vault does nothing**     | Checksum-validated block (DPDP/GDPR)                 |
| 6  | Bootstrap token for the vault itself in a Dockerfile `ENV`                | The secret-zero gap⁵            | Blocks; suggests build secrets/runtime env           |
| 7  | RPA/UiPath credentials committed for a bot to read                         | Yes, but bypassed                | Blocks; points to Orchestrator credential assets     |
| 8  | AI agent writes a key into code and commits it                             | Vault unaware                    | Same hook, no extra setup (agents use git)           |
| 9  | Developer bypasses with `--no-verify`                                     | —                               | Pre-push hook catches it before it leaves the laptop |
| 10 | Someone's machine has no ZeroTrace                                         | —                               | CI`zerotrace scan --range` + push protection       |

## 8. Answering the objection, on stage, in 30 seconds

> **"We already use AWS Secrets Manager and CyberArk."**
>
> "Good — that is the right destination, and we do not replace it. But a vault governs values it
> already holds. It cannot see a developer typing a key into `charge.py` at 6pm, or copying one
> into `.env` to run tests offline, or a support ticket's customer data becoming a fixture.
> Measured: repositories that *do* use a secrets manager still leaked at 5.1%, slightly worse
> than the GitHub average. Internal repos are six times more likely to contain a hardcoded
> secret than public ones, and AI-assisted commits leak at twice the human rate. We are the
> guardrail at that exact moment — one install, every repo, and we hand back a fix, not a
> failure."

Follow-up you should be ready for:

- *"Isn't GitHub push protection enough?"* — It fires at push, on partner patterns only, and
  generic secrets were 58% of leaks.³ It also does nothing for PII or for non-GitHub remotes.
- *"Can't a developer bypass it?"* — Yes, `--no-verify`. That is why we ship a pre-push hook and
  a CI scan, and why we call the hook a guardrail, not a boundary.
- *"Does the AI see our code?"* — It sees masked code and shape features, never values, on
  localhost. HIGH-confidence findings never reach the model at all.

## 9. Compliance hooks worth one slide (BMW context)

- **UNECE R155 / ISO-SAE 21434** push cybersecurity requirements from OEM down to suppliers, and
  require documented, auditable secure-development processes across the lifecycle; insecurely
  handled production keys and credentials are a named risk area.⁸ ZeroTrace produces a
  hash-chained audit log of every decision and exception, which is the kind of evidence those
  audits ask for.
- **DPDP Act (India) / GDPR** make the PII half of this a legal exposure, not just hygiene —
  which is where the Aadhaar/PAN detection earns its place and where vaults contribute nothing.

## 10. Open research questions (for the team)

- Baseline our own false-positive rate on a real internal repo before claiming precision.
- Measure the AI tie-break against real Qwen (`zerotrace eval`) and publish the numbers.
- Ask two or three developers how they handle local credentials today; one honest quote from
  inside the company is worth more on stage than any vendor statistic.

---

### Sources

1. Study of 2,584 repositories with a secrets manager in CI/CD — 5.1% leak rate vs 4.6% average:
   [Help Net Security summary](https://www.helpnetsecurity.com/2025/03/20/leaked-secrets-threats-in-cybersecurity/),
   [GitGuardian State of Secrets Sprawl 2025](https://www.gitguardian.com/state-of-secrets-sprawl-report-2025)
2. [GitGuardian, The State of Secrets Sprawl 2026](https://blog.gitguardian.com/the-state-of-secrets-sprawl-2026/)
3. [GitGuardian, The State of Secrets Sprawl 2025](https://blog.gitguardian.com/the-state-of-secrets-sprawl-2025/)
4. [CyberArk, machine identities outnumber humans 82:1 (2025)](https://www.cyberark.com/press/machine-identities-outnumber-humans-by-more-than-80-to-1-new-report-exposes-the-exponential-threats-of-fragmented-identity-security/)
5. [GitGuardian, the secret zero problem](https://www.gitguardian.com/nhi-hub/the-secret-zero-problem-solutions-and-alternatives)
6. [Toyota key exposed for five years](https://blog.gitguardian.com/toyota-accidently-exposed-a-secret-key-publicly-on-github-for-five-years/),
   [Mercedes-Benz source exposed by a leaked token](https://www.securityweek.com/leaked-github-token-exposed-mercedes-source-code/)
7. [UiPath Orchestrator: about assets](https://docs.uipath.com/orchestrator/standalone/2023.4/user-guide/about-assets),
   [credential stores](https://docs.uipath.com/orchestrator/standalone/2023.4/user-guide/about-credential-stores)
8. [UNECE R155 and ISO/SAE 21434 supplier obligations](https://efs.consulting/en/insights/article/information-security/unece-r155/),
   [R155 software supply-chain requirements](https://safeguard.sh/resources/blog/unece-wp29-r155-software-supply-chain-requirements-for-automakers)

*Figures gathered September 2026. Re-check the headline numbers before the pitch; these reports
are annual and the 2026 edition is the current one.*
