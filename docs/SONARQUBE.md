# SonarQube: quality gate and the IDE extension

## In the editor (SonarQube for IDE, VS Code / VS Codium)

1. Install **SonarQube for IDE** (formerly SonarLint).
2. `Ctrl/Cmd+Shift+P` → **SonarQube: Add SonarQube Cloud Connection**. Choose *SonarQube Cloud*,
   then **Generate Token**, log in, and paste it back. The token lives in the editor's secure
   storage, never in the repo.
3. `Ctrl/Cmd+Shift+P` → **SonarQube: Bind to SonarQube Project** → pick this project.
   That writes `.sonarlint/connectedMode.json` (gitignored) and pulls the same rules and quality
   profile CI uses, so what you see locally is what the gate will say.
4. Findings appear in the **Problems** panel as you edit, and the **SonarQube** view lists them
   per file. Right-click an issue → *Open description* explains the rule.

Useful commands: **SonarQube: Update all project bindings** after a profile change, and
**SonarQube: Analyze all files in the workspace** for a full pass.

### What the extension will *not* tell you
Quality-gate conditions that are computed server-side: coverage on new code, duplicated lines,
and the security-hotspot review ratio. Check those on SonarQube Cloud after a push.

## How this project is analysed

This project uses SonarQube Cloud's **Automatic Analysis**. Every push to `main` (and every pull
request) is analysed automatically by SonarQube Cloud — there is no scanner in GitHub Actions and
no `SONAR_TOKEN` to manage. Results appear on the project's SonarQube Cloud dashboard; the quality
gate status is what you show when someone asks whether Sonar is passing.

Automatic Analysis reads **`.sonarcloud.properties`** and ignores everything else. That file
declares the sources, `sonar.tests=tests`, the Python versions and the exclusions, so the
format-valid fake credentials in `tests/` and `demo/fixtures/` are not reported as leaks.

### Coverage

Automatic Analysis cannot run the test suite, so it never uploads coverage — the coverage
condition on the quality gate simply has no data (it is not a failure). Coverage still runs in
`ci.yml` for local visibility; generate the same report yourself with:

```bash
pytest -q --cov=zerotrace --cov-report=xml --cov-report=term-missing
```

> **If you later need coverage on the SonarQube dashboard**, switch to CI-based analysis instead:
> turn **Automatic Analysis** off (SonarQube Cloud → **Administration → Analysis Method**), add a
> `SONAR_TOKEN` secret, and add a workflow that runs the tests and calls
> `SonarSource/sonarqube-scan-action` with a `sonar-project.properties` carrying `projectKey` /
> `organization` and `sonar.python.coverage.reportPaths=coverage.xml`. Run **exactly one** method:
> a CI scan while Automatic Analysis is on fails with *"You are running CI analysis while Automatic
> Analysis is enabled."*

## Supply-chain rules (S8541, S8544)

Every `pip install` in the workflows and bootstrap scripts installs from a hash-pinned lock
with `--require-hashes --only-binary :all:`, then the project itself with `--no-deps
--no-build-isolation --no-index`. See CONTRIBUTING.md → Dependencies for regenerating the
locks.

## Rules we deliberately do not follow blindly

- `src/zerotrace/detectors/` is full of credential-shaped regexes and keyword lists. That is the
  product, not a leak. Review anything Sonar reports there before excluding it.
- `tests/` and `demo/fixtures/` hold format-valid **fake** credentials, generated at run time.
  Both configuration files mark the former as tests and exclude the latter.
