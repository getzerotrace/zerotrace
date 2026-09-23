"""Layered config: defaults <- org policy <- user <- repo. Org policy can lock keys.

Fail closed: an unreadable layer is skipped with a warning, never treated as "disable".
"""
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

from . import gitutil

_LEGACY_SHA_PLACEHOLDER = "REPLACE_WITH_PINNED_MODEL_HASH"
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")


def zerotrace_home() -> str:
    """Per-user ZeroTrace dir: global hooks, install state, user config."""
    return os.environ.get("ZEROTRACE_HOME") or os.path.join(os.path.expanduser("~"), ".zerotrace")


def org_policy_paths() -> list[str]:
    env = os.environ.get("ZEROTRACE_POLICY")
    if env:
        return [env]
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return [os.path.join(base, "zerotrace", "policy.yml")]
    return ["/etc/zerotrace/policy.yml", "/Library/Application Support/zerotrace/policy.yml"]


def user_config_path() -> str:
    return os.path.join(zerotrace_home(), "config.yml")


@dataclass(frozen=True)
class Config:
    enabled: bool = True
    model_enabled: bool = True
    model_runtime: str = "ollama"          # ollama | openai | off
    model_name: str = "qwen2.5-coder:3b-instruct-q4_K_M"
    model_endpoint: str = field(default_factory=lambda: os.environ.get(
        "ZEROTRACE_MODEL_ENDPOINT", os.environ.get("ZEROTRACE_OLLAMA_HOST", "http://localhost:11434"),
    ))
    model_auth_env: str = "ZEROTRACE_MODEL_TOKEN"   # bearer token read from env, never from yml
    model_allow_remote: bool = False
    model_digest: str = ""                 # pinned model digest; "" = not pinned
    # Measured: qwen2.5-coder:3b on CPU-only Docker took ~106 s p50 per call, so the old 20 s
    # default failed 100% of calls closed to WARN on ordinary laptops (docs/AI_CLASSIFIER.md).
    # A GPU or hosted endpoint answers in low single-digit seconds and never reaches this bound.
    model_timeout_seconds: float = 120.0
    model_keep_alive: str = "30m"
    model_max_parallel: int = 4
    model_can_escalate: bool = True        # REAL_SECRET verdict may raise MEDIUM -> BLOCK
    model_escalate_threshold: float = 0.8
    model_allow_threshold: float = 0.6     # TEST_FIXTURE verdict may lower MEDIUM -> ALLOW
    max_context_lines: int = 6
    block_severity: tuple[str, ...] = ("critical", "high")
    warn_severity: tuple[str, ...] = ("medium",)
    pii_locales: tuple[str, ...] = ("en", "en_IN")
    # Employee/internal email domains. Empty by default and set by the organisation, because
    # a list of a company's domains is the company's own data, not a detail of this tool.
    pii_internal_domains: tuple[str, ...] = ()
    pii_engine: str = "regex"              # regex | presidio (needs the [pii-ner] extra)
    action_in_tests: str = "replace_synthetic"
    action_in_config: str = "env_reference"
    exceptions_ttl_days: int = 30
    rules_extra: tuple[str, ...] = ()
    vault_scheme: str = ""                 # e.g. "vault://secret/data/{repo}#{name}"
    repo_root: str = ""
    sources: tuple[str, ...] = ()
    locked: tuple[str, ...] = ()

    @property
    def model_is_remote(self) -> bool:
        from urllib.parse import urlparse
        host = (urlparse(self.model_endpoint).hostname or "").lower()
        return host not in _LOCAL_HOSTS


def _read_yaml(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"zerotrace: warning: ignoring unreadable config {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"zerotrace: warning: ignoring non-mapping config {path}", file=sys.stderr)
        return None
    return data


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _get(data: dict, dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _layers(repo_path: str | None) -> tuple[dict, list[str], list[str]]:
    sources: list[str] = []
    org: dict = {}
    for path in org_policy_paths():
        data = _read_yaml(path)
        if data is not None:
            org = data
            sources.append(f"org:{path}")
            break

    merged = _merge({}, org)
    user = _read_yaml(user_config_path())
    if user is not None:
        merged = _merge(merged, user)
        sources.append(f"user:{user_config_path()}")
    if repo_path:
        repo = _read_yaml(repo_path)
        if repo is not None:
            merged = _merge(merged, repo)
            sources.append(f"repo:{repo_path}")

    # Org-locked keys win over user/repo layers.
    locked = [str(k) for k in (org.get("locked") or [])]
    for key in locked:
        value = _get(org, key)
        if value is not None:
            _set(merged, key, value)
    return merged, sources, locked


def _block_severity(policy: dict) -> tuple[str, ...]:
    block = tuple(policy.get("block_severity", ["critical", "high"]))
    # A critical finding can never be configured to pass.
    return block if "critical" in block else ("critical", *block)


def _endpoint(model: dict, locked: list[str]) -> str:
    if "model.endpoint" in locked and model.get("endpoint"):
        return str(model["endpoint"])  # org-pinned endpoint: env can't redirect it
    return str(os.environ.get("ZEROTRACE_MODEL_ENDPOINT") or model.get("endpoint")
               or Config().model_endpoint)


def _digest(model: dict) -> str:
    digest = str(model.get("digest") or model.get("sha256") or "")
    return "" if digest == _LEGACY_SHA_PLACEHOLDER else digest


def _model_config(model: dict, locked: list[str]) -> dict:
    defaults = Config()
    runtime = str(model.get("runtime", "ollama"))
    return {
        "model_enabled": bool(model.get("enabled", True)) and runtime != "off",
        "model_runtime": runtime,
        "model_name": str(model.get("name", defaults.model_name)),
        "model_endpoint": _endpoint(model, locked),
        "model_auth_env": str(model.get("auth_env", defaults.model_auth_env)),
        "model_allow_remote": bool(model.get("allow_remote", False)),
        "model_digest": _digest(model),
        "model_timeout_seconds": float(model.get("timeout_seconds",
                                                 defaults.model_timeout_seconds)),
        "model_keep_alive": str(model.get("keep_alive", defaults.model_keep_alive)),
        "model_max_parallel": int(model.get("max_parallel", defaults.model_max_parallel)),
        "model_can_escalate": bool(model.get("can_escalate", True)),
        "model_escalate_threshold": float(model.get("escalate_threshold", 0.8)),
        "model_allow_threshold": float(model.get("allow_threshold", 0.6)),
        "max_context_lines": int(model.get("max_context_lines", 6)),
    }


def _policy_config(policy: dict) -> dict:
    pii = policy.get("pii") or {}
    return {
        "block_severity": _block_severity(policy),
        "warn_severity": tuple(policy.get("warn_severity", ["medium"])),
        "pii_locales": tuple(pii.get("locales", ["en", "en_IN"])),
        "pii_internal_domains": tuple(str(domain).strip().lower()
                                      for domain in pii.get("internal_domains", [])),
        "pii_engine": str(pii.get("engine", "regex")),
        "action_in_tests": str(pii.get("action_in_tests", "replace_synthetic")),
        "action_in_config": str(pii.get("action_in_config", "env_reference")),
    }


def load_config(path: str | None = None) -> "Config":
    root = gitutil.repo_root()
    raw, sources, locked = _layers(path or os.path.join(root, ".zerotrace.yml"))
    exceptions = raw.get("exceptions") or {}
    rules = raw.get("rules") or {}
    return Config(
        enabled=bool(raw.get("enabled", True)),
        **_model_config(raw.get("model") or {}, locked),
        **_policy_config(raw.get("policy") or {}),
        exceptions_ttl_days=int(exceptions.get("ttl_days", 30)),
        rules_extra=tuple(str(p) for p in (rules.get("extra") or [])),
        vault_scheme=str((raw.get("vault") or {}).get("scheme", "")),
        repo_root=root,
        sources=tuple(sources),
        locked=tuple(locked),
    )
