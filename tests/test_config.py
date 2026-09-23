"""Layered config and org-locked keys."""
from zerotrace.config import load_config

from .conftest import write


def test_layers_merge_in_order(repo, git_env, monkeypatch):
    org = git_env / "org.yml"
    write(org, "model:\n  timeout_seconds: 7\n  name: org-model\n")
    monkeypatch.setenv("ZEROTRACE_POLICY", str(org))
    write(git_env / ".zerotrace" / "config.yml", "model:\n  timeout_seconds: 9\n")
    write(repo / ".zerotrace.yml", "model:\n  name: repo-model\n")
    cfg = load_config()
    assert cfg.model_timeout_seconds == 9          # user overrides org
    assert cfg.model_name == "repo-model"          # repo overrides everything unlocked
    assert [s.split(":")[0] for s in cfg.sources] == ["org", "user", "repo"]


def test_org_locked_keys_win(repo, git_env, monkeypatch):
    org = git_env / "org.yml"
    write(org, "locked: [enabled, policy.block_severity, model.endpoint]\n"
               "enabled: true\npolicy:\n  block_severity: [critical, high, medium]\n"
               "model:\n  endpoint: https://inference.corp.example\n  allow_remote: true\n")
    monkeypatch.setenv("ZEROTRACE_POLICY", str(org))
    monkeypatch.setenv("ZEROTRACE_MODEL_ENDPOINT", "http://evil.example")
    write(repo / ".zerotrace.yml", "enabled: false\npolicy:\n  block_severity: []\n"
                                   "model:\n  endpoint: http://localhost:1\n")
    cfg = load_config()
    assert cfg.enabled is True
    assert cfg.block_severity == ("critical", "high", "medium")
    assert cfg.model_endpoint == "https://inference.corp.example"


def test_critical_can_never_be_unblocked(repo):
    write(repo / ".zerotrace.yml", "policy:\n  block_severity: [high]\n")
    assert "critical" in load_config().block_severity


def test_invalid_repo_config_falls_back_safely(repo):
    write(repo / ".zerotrace.yml", "model: [unclosed\n")
    cfg = load_config()
    assert cfg.enabled and "critical" in cfg.block_severity
