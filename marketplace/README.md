# Contributing ZeroTrace to the BMW skills marketplace

ZeroTrace is published as a **skill**: a CLI other tooling (an agent, an MCP host, a CI job, a
developer) can call. It is deliberately vendor-neutral — nothing here is specific to one AI
client.

```
skill/
  SKILL.md        what the skill does, when to use it, and the rules it must follow
  skill.json      machine-readable manifest: entrypoint, commands, capabilities, requirements
  scripts/run.sh  thin wrapper around the installed `zerotrace` CLI
marketplace/
  submission.json the catalogue entry that points at skill/
  README.md       this file
```

## Before submitting — confirm the house standard

⚠️ **The manifests here are a reasonable, generic shape, not the BMW marketplace schema.** We do
not have that specification in this repository, so before submitting:

1. Get the marketplace's contribution guide and schema (field names, required fields, versioning
   and review process).
2. Reconcile `skill/skill.json` and `marketplace/submission.json` with it — names and nesting will
   very likely differ.
3. Confirm the ownership and licence position with the team (see `CONTRIBUTING.md` → IP &
   Licensing). ZeroTrace is Apache-2.0 in this repository; an internal catalogue may require a
   different licence or an internal-only marking.

## Three ways this can be consumed

| Form | What it means | Status |
|---|---|---|
| **Skill** (this) | A documented CLI an agent or person invokes: `zerotrace scan`, `zerotrace gateway`, `zerotrace doctor` | ready |
| **MCP server** | The same commands exposed as MCP tools, so an MCP-speaking client can call them directly | not built; `zerotrace gateway`/`scan --format json` already give the two tool surfaces it would need |
| **Agent** | A longer-running assistant that scans, explains and proposes fixes autonomously | not built; policy is deliberately human-approved, so an agent must still ask before applying a fix |

If the marketplace prefers MCP, the adapter is small: both commands already read stdin/argv and
return JSON, and `zerotrace scan --format json` emits fingerprints only, never values.

## Verifying a submission

```bash
pipx install zerotrace && zerotrace install --global   # or the one-line installer in README.md
zerotrace doctor                                       # install, config layers, model endpoint
zerotrace scan --staged --format json                  # findings as fingerprints
echo 'api_key = "..."' | zerotrace gateway             # sanitised payload + verdict
```

The skill must keep its two guarantees on any host: **no raw secret or PII value is ever
returned** (findings are fingerprints, payloads come back masked), and **nothing is changed
without explicit human approval**.
