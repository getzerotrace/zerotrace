# SonarQube: CI gate and the IDE extension

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

## Coverage

The gate's default is **80% coverage on new code**, and coverage only exists if a report is
uploaded. Generate one locally exactly as CI does:

```bash
pytest -q --cov=zerotrace --cov-report=xml --cov-report=term-missing
# writes coverage.xml, which sonar.python.coverage.reportPaths points at
```

Automatic Analysis cannot run our tests, so it never sees coverage. To send it, the project has
to switch to CI-based analysis:

1. SonarQube Cloud → project → **Administration → Analysis Method** → turn **Automatic Analysis**
   off.
2. GitHub repo → **Settings → Secrets and variables → Actions** → add `SONAR_TOKEN`
   (SonarQube Cloud → **My Account → Security → Generate Token**).
3. Uncomment `sonar.projectKey` / `sonar.organization` in `sonar-project.properties` with the
   exact values from SonarQube Cloud → **Project Information**.
4. Enable `.github/workflows/sonar.yml` (already committed; it runs the tests, produces
   `coverage.xml` and calls the official scan action).

Keep Automatic Analysis **or** the workflow, never both: two analyses of the same branch
overwrite each other.

## Which configuration file applies

| Analysis | Reads | Notes |
|---|---|---|
| Automatic Analysis (the default today) | `.sonarcloud.properties` | ignores `sonar-project.properties` entirely |
| CI scanner (`.github/workflows/sonar.yml`) | `sonar-project.properties` | also uploads `coverage.xml` |

Both declare the same sources, `sonar.tests=tests`, the Python versions and the exclusions;
change them together. Before `.sonarcloud.properties` existed, Automatic Analysis analysed
`tests/` and `demo/fixtures/` as production code and warned that `sonar.tests` was not set.
`.github` is listed in `sonar.sources` on purpose: once sources are set explicitly, the
workflows are only analysed if they are named.

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
