"""Proposals per language, and the terminal rendering that must never print a raw value."""
import pytest

from zerotrace.config import Config
from zerotrace.detectors import Finding
from zerotrace.policy.engine import Decision
from zerotrace.remediation import proposer
from zerotrace.ui import terminal

from .conftest import Fake, rand


def _decision(path: str, line: str, value: str, kind: str = "hardcoded_api_key",
              identifier: str = "api_key", line_no: int = 1, file_class: str = "code",
              rule_id: str = "hardcoded-api-key", severity: str = "high") -> Decision:
    finding = Finding(rule_id=rule_id, kind=kind, severity=severity, confidence=0.9, path=path,
                      line_no=line_no, file_class=file_class, line_text=line,
                      context_snippet=line, identifier=identifier, source="code_assign",
                      explanation="hardcoded credential", matched_value=value)
    return Decision("block", severity, "matched", finding)


@pytest.mark.parametrize("path, line, expected", [
    ("app.py", 'API_KEY = "{v}"', 'API_KEY = os.environ["API_KEY"]'),
    ("src/a.js", 'const apiKey = "{v}";', "const apiKey = process.env.API_KEY;"),
    ("main.go", '\tapiKey := "{v}"', '\tapiKey := os.Getenv("API_KEY")'),
    ("A.java", 'String apiKey = "{v}";', 'String apiKey = System.getenv("API_KEY");'),
    ("P.cs", 'var apiKey = "{v}";', 'var apiKey = Environment.GetEnvironmentVariable("API_KEY");'),
    ("s.rb", "  apiKey = '{v}'", '  apiKey = ENV["API_KEY"]'),
    ("s.php", "$apiKey = '{v}';", "$apiKey = getenv('API_KEY');"),
    ("config.yml", '  api_key: "{v}"', '  api_key: "${API_KEY}"'),
    ("main.tf", '  api_key = "{v}"', "  api_key = var.api_key"),
])
def test_env_reference_per_language(path, line, expected):
    value = rand(28)
    proposal = proposer.propose(_decision(path, line.format(v=value), value), "reference", Config())
    assert proposal.mode == "reference"
    assert proposal.new_line == expected.replace("{v}", value)
    assert value not in (proposal.new_line or "")


def test_dockerfile_env_is_dropped_not_rewritten():
    value = rand(24)
    decision = _decision("Dockerfile", f"ENV API_TOKEN={value}", value, identifier="API_TOKEN")
    proposal = proposer.propose(decision, "reference", Config())
    assert proposal.new_line == "ENV API_TOKEN"
    assert "BuildKit" in proposal.note


def test_vault_scheme_is_used_for_config_files():
    value = rand(20)
    cfg = Config(vault_scheme="vault://secret/data/{repo}#{name}", repo_root="/tmp/payments")
    proposal = proposer.propose(_decision("config/app.yml", f'api_key: "{value}"', value),
                                "reference", cfg)
    assert proposal.new_line == 'api_key: "vault://secret/data/payments#API_KEY"'


def test_value_inside_a_larger_string_keeps_the_file_valid():
    value = Fake.github()
    line = f'headers = {{"Authorization": "token {value}"}}'
    proposal = proposer.propose(_decision("app.py", line, value), "reference", Config())
    assert value not in proposal.new_line
    assert proposal.new_line.count('"') == line.count('"')   # still syntactically balanced
    assert "by hand" in proposal.note


def test_placeholder_mode_and_pii_synthetics():
    value = rand(20)
    ph = proposer.propose(_decision("app.py", f'api_key = "{value}"', value), "placeholder",
                          Config())
    assert ph.new_line == 'api_key = "<API_KEY>"'

    email = "jane.doe" + "@" + "acme.example"
    pii = _decision("tests/fixtures/u.json", f'"email": "{email}"', email, kind="pii_email",
                    identifier="", rule_id="pii_email", file_class="test")
    assert proposer.propose(pii, "placeholder", Config()).new_line == '"email": "user@example.test"'


def test_sensitive_file_proposes_unstage():
    decision = _decision(".env", "", "", kind="sensitive_file", line_no=0,
                         rule_id="sensitive-file", identifier="")
    proposal = proposer.propose(decision, "reference", Config())
    assert proposal.mode == "unstage"
    assert ".env.example" in proposal.note


# --- terminal rendering ---------------------------------------------------------------

def test_headless_report_never_prints_a_value(capsys):
    value, other = Fake.stripe_live(), Fake.github()
    decisions = [
        _decision("src/pay.py", f'KEY = "{value}"', value, rule_id="stripe-live-key",
                  severity="critical"),
        _decision("src/gh.py", f'TOKEN = "{other}"', other, rule_id="github-token",
                  severity="critical"),
    ]
    terminal.headless_report(decisions, Config())
    out = capsys.readouterr().out
    assert value not in out and other not in out
    assert "stripe-live-key" in out and "github-token" in out
    assert "len=" in out                      # masked as a typed, length-hinted token


def test_two_findings_on_one_line_are_both_masked(capsys):
    a, b = Fake.github(), "jane.doe" + "@" + "bmwtechworks.in"
    line = f'cfg = {{"token": "{a}", "owner": "{b}"}}'
    decisions = [
        _decision("src/cfg.py", line, a, rule_id="github-token", severity="critical"),
        _decision("src/cfg.py", line, b, kind="pii_email_internal", rule_id="pii_email_internal"),
    ]
    terminal.headless_report(decisions, Config())
    out = capsys.readouterr().out
    assert a not in out and b not in out


def test_non_interactive_present_returns_blocked(capsys):
    value = Fake.stripe_live()
    decisions = [_decision("src/pay.py", f'KEY = "{value}"', value, rule_id="stripe-live-key")]
    assert terminal.present(decisions, Config(), interactive=False) == 1
    assert value not in capsys.readouterr().out
