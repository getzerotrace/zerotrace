"""Files that should never be committed, whatever their individual lines look like."""
import fnmatch
import os
import re

from . import Finding
from .. import gitutil
from ..collectors.staged_diff import Changeset, classify_file

_ENV_SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".tpl")

_SSH_KEY = "SSH private key"
_SSH_KEY_WHY = "An SSH private key authenticates as you to every server that trusts it."
_KEYSTORE_WHY = ("Keystores bundle private keys with certificates, and their passwords are "
                 "often weak.")
_NETRC_WHY = "netrc stores machine logins and passwords in plain text."
_TFSTATE_WHY = ("Terraform state stores every resource attribute, including generated passwords "
                "and keys, in plain text.")
_KUBECONFIG = "Kubernetes config"
_KUBECONFIG_WHY = "The kubeconfig embeds cluster credentials."
_KUBECONFIG_PATTERN = r"client-key-data|token:|password:"

# (glob on basename, severity, title, content regex required (None = always), explanation)
_RULES: list[tuple[str, str, str, str | None, str]] = [
    ("id_rsa", "critical", _SSH_KEY, None, _SSH_KEY_WHY),
    ("id_dsa", "critical", _SSH_KEY, None, _SSH_KEY_WHY),
    ("id_ecdsa", "critical", _SSH_KEY, None, _SSH_KEY_WHY),
    ("id_ed25519", "critical", _SSH_KEY, None, _SSH_KEY_WHY),
    ("*.pem", "critical", "PEM private key", r"PRIVATE KEY", "The PEM file contains a private key, not just a certificate."),
    ("*.key", "critical", "Private key file", r"(?:PRIVATE KEY)|(?:^[A-Za-z0-9+/=\s]{200,}$)", "The .key file holds private key material."),
    ("*.p12", "critical", "PKCS#12 keystore", None, _KEYSTORE_WHY),
    ("*.pfx", "critical", "PKCS#12 keystore", None, _KEYSTORE_WHY),
    ("*.jks", "critical", "Java keystore", None, "Java keystores bundle private keys and trusted certificates."),
    ("*.keystore", "critical", "Keystore", None, "Keystores (for example Android signing keys) must never be in source control."),
    ("*.kdbx", "critical", "KeePass database", None, "A password-manager vault should never be committed."),
    (".git-credentials", "critical", "Git credential store", None, "The file stores git passwords and tokens in plain text."),
    (".netrc", "critical", "netrc credentials", None, _NETRC_WHY),
    ("_netrc", "critical", "netrc credentials", None, _NETRC_WHY),
    (".pgpass", "critical", "PostgreSQL password file", None, ".pgpass stores database passwords in plain text."),
    ("*.tfstate", "critical", "Terraform state", None, _TFSTATE_WHY),
    ("*.tfstate.backup", "critical", "Terraform state backup", None, _TFSTATE_WHY),
    ("credentials", "critical", "Cloud credentials file", r"aws_secret_access_key|aws_access_key_id|\[default\]", "This looks like an ~/.aws/credentials file."),
    ("*.kubeconfig", "critical", _KUBECONFIG, _KUBECONFIG_PATTERN, _KUBECONFIG_WHY),
    ("kubeconfig", "critical", _KUBECONFIG, _KUBECONFIG_PATTERN, _KUBECONFIG_WHY),
    ("config", "critical", _KUBECONFIG, r"client-key-data|^\s*token:\s*\S{20,}", _KUBECONFIG_WHY),
    (".npmrc", "high", "npm credentials", r"_authToken\s*=\s*[^$\s]|_password\s*=", "The .npmrc contains a registry auth token."),
    (".pypirc", "high", "PyPI credentials", r"password\s*[:=]\s*\S", "The .pypirc contains a package index password."),
    ("*.tfvars", "medium", "Terraform variables", r"(?i)(password|secret|token|key)\s*=", "The .tfvars file often carries real secrets. Keep it local or use a secret backend."),
    ("*.ovpn", "high", "VPN profile", r"<key>|auth-user-pass", "The VPN profile embeds keys or credentials."),
]


def _env_rule(basename: str) -> tuple[str, str, str | None, str] | None:
    if basename == ".env" or (basename.startswith(".env.") and not basename.endswith(_ENV_SAFE_SUFFIXES)):
        return ("high", "Environment file", None,
                "Environment files hold local and production secrets. Commit a keys-only "
                "`.env.example` instead and keep the real file gitignored.")
    return None


def match(path: str, content: str | None) -> tuple[str, str, str] | None:
    """(severity, title, explanation) if `path` is a sensitive file, else None."""
    base = os.path.basename(path)
    if base.endswith(".pub"):
        return None
    env = _env_rule(base)
    if env:
        return env[0], env[1], env[3]
    for pattern, severity, title, content_re, explain in _RULES:
        if not fnmatch.fnmatch(base, pattern):
            continue
        if pattern == "config" and ".kube" not in path.replace("\\", "/").split("/"):
            continue
        if content_re is not None and (content is None or not re.search(content_re, content, re.M)):
            continue
        return severity, title, explain
    return None


def scan(changeset: Changeset, cfg) -> list[Finding]:
    findings: list[Finding] = []
    for path in changeset.paths:
        content = gitutil.blob_text(changeset.rev, path)
        hit = match(path, content)
        if not hit:
            continue
        severity, title, explain = hit
        findings.append(Finding(
            rule_id="sensitive-file", kind="sensitive_file", severity=severity,
            confidence=0.9, path=path, line_no=0, file_class=classify_file(path),
            line_text="", context_snippet="", source="sensitive_files",
            explanation=f"{title}: {explain}",
        ))
    return findings
