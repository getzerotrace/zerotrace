"""Deterministic false-positive filters shared by every detector."""
import os
import re

from .entropy import dictionary_ratio, repeated_ratio

_PLACEHOLDER_EXACT = {
    "changeme", "change_me", "change-me", "password", "passwd", "secret", "token", "apikey",
    "api_key", "your_api_key", "xxx", "todo", "tbd", "none", "null", "nil", "undefined",
    "example", "sample", "dummy", "fake", "test", "testing", "placeholder", "redacted",
    "letmein", "admin", "root", "default", "foobar", "hunter2", "notasecret", "not-a-secret",
    "pass", "pwd", "user", "username", "secret123", "password123", "p@ssw0rd", "passw0rd",
}
_PLACEHOLDER_RE = re.compile(
    r"""^(
        \$\{[^}]*\}            |   # ${VAR}
        \$\([^)]*\)            |   # $(cmd)
        \$[A-Za-z_][A-Za-z0-9_]* | # $VAR
        \{\{[^}]*\}\}          |   # {{ template }}
        \{[^{}\s]{1,40}\}      |   # {name} / {rand(14)}: format-string placeholder
        <[^<>]{1,64}>          |   # <placeholder>
        %\(?[A-Za-z_]*\)?s     |   # %s / %(name)s
        \[[A-Z_ ]{2,}\]        |   # [REDACTED]
        [*xX•._\-#]{3,}            # ***** / xxxxx / .....
    )$""",
    re.X,
)
_PLACEHOLDER_WORDS_RE = re.compile(
    r"(example|sample|dummy|fake|placeholder|changeme|change[_-]?me|your[_-]|redacted|"
    r"insert[_-]|replace[_-]?me|todo|xxxx|mock|not[_-]?a[_-]?real)", re.I,
)
_ENV_LOOKUP_RE = re.compile(
    r"(os\.environ|os\.getenv|getenv\(|process\.env|System\.getenv|Environment\."
    r"GetEnvironmentVariable|ENV\[|env\(|std::env::var|config\.get\(|secrets\.|vault|"
    r"ssm:|secretsmanager|keyvault|\{\{\s*secrets\.)", re.I,
)
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HASH_CONTEXT_RE = re.compile(
    r"(integrity|sha1|sha256|sha512|checksum|digest|hash|commit|revision|\brev\b|etag|nonce|"
    r"\btree\b|\boid\b|object id|fingerprint)",
    re.I,
)
# Hex digests of the usual sizes: md5, sha1, sha224/256, sha384/512.
_HEX_DIGEST_RE = re.compile(r"\A(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{56}|[0-9a-f]{64}"
                            r"|[0-9a-f]{96}|[0-9a-f]{128})\Z", re.I)
_LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock",
    "go.sum", "cargo.lock", "composer.lock", "gemfile.lock", "uv.lock", "packages.lock.json",
}
_SEQUENTIAL = "abcdefghijklmnopqrstuvwxyz0123456789"


def is_placeholder(value: str) -> bool:
    v = value.strip().strip("'\"`")
    if not v:
        return True
    if v.lower() in _PLACEHOLDER_EXACT or _PLACEHOLDER_RE.match(v):
        return True
    if _PLACEHOLDER_WORDS_RE.search(v):
        return True
    if len(v) >= 6 and repeated_ratio(v) >= 0.6:
        return True
    lower = v.lower()
    return len(v) >= 6 and (lower in _SEQUENTIAL or lower in _SEQUENTIAL[::-1])


def looks_like_prose(value: str) -> bool:
    """UI strings, messages and sentences are not credentials."""
    v = value.strip()
    return v.count(" ") >= 2 or (" " in v and dictionary_ratio(v) >= 0.6)


def is_env_reference(text: str) -> bool:
    return bool(_ENV_LOOKUP_RE.search(text))


def is_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value.strip()))


def is_hash_context(line: str) -> bool:
    return bool(_HASH_CONTEXT_RE.search(line))


def is_digest(value: str) -> bool:
    """A hex digest. Storing one is not a credential leak (that is the point of hashing)."""
    return bool(_HEX_DIGEST_RE.match(value.strip()))


def is_lockfile(path: str) -> bool:
    return os.path.basename(path).lower() in _LOCKFILES
