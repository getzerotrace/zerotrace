"""Hardcoded credentials in code/config: `<sensitive identifier> <assign> "<literal>"`.

Language-agnostic: Python, JS/TS, Go, Java/Kotlin, C#, Ruby, PHP, Rust, YAML/JSON, .env,
.properties, Dockerfile ENV/ARG, HCL. The identifier decides *whether* it's a credential slot;
the value's shape decides *how sure* we are.
"""
import os
import re

from . import Finding
from .entropy import char_classes, dictionary_ratio, shannon
from .filters import is_env_reference, is_placeholder, is_uuid, looks_like_prose
from ..collectors.staged_diff import Unit

_MAX_LINE = 4000

_QUOTED_ASSIGN = re.compile(r"""
    (?P<ident>[A-Za-z_$@][\w$.\-]*)          # identifier or key
    ['"]?\]?                                  # closing quote of a quoted key / ["key"]
    \s*
    (?::\s*[\w\[\]<>,.|&'?\s]*?\s*(?==))?     # optional type annotation before '='
    (?P<op>:=|===?|=>|=|:)
    \s*
    (?:[rRbBuUf]{1,2}|@)?                     # string prefixes r"" b"" f"" @""
    (?P<q>["'`])
    (?P<val>(?:\\.|(?!(?P=q)).){1,500}?)
    (?P=q)
""", re.X)

_UNQUOTED_ASSIGN = re.compile(r"""
    ^\s*(?:export\s+|ENV\s+|ARG\s+|set\s+|-\s+)?
    (?P<ident>[A-Za-z_][\w.\-]*)
    \s*(?P<op>=|:)\s*
    (?P<val>[^\s'"\#!&*|>{\[][^\s\#]*)
    \s*(?:\#.*)?$
""", re.X)

_DOCKER_ENV_SPACE = re.compile(r"^\s*ENV\s+(?P<ident>[A-Za-z_]\w*)\s+(?P<val>[^\s=][^\s]*)\s*$")

_UNQUOTED_FILES_EXT = (".env", ".properties", ".ini", ".cfg", ".conf", ".yml", ".yaml",
                       ".toml", ".sh", ".bash", ".zsh", ".npmrc", ".pypirc", ".tfvars")

_PASSWORDISH = {"password", "passwd", "pwd", "pass", "passphrase", "passcode", "contrasena"}
_STRONG_WORDS = _PASSWORDISH | {
    "secret", "secrets", "token", "credential", "credentials", "apikey", "privatekey",
    "accesskey", "secretkey", "bearer", "dsn", "sas", "cred", "creds",
}
_STRONG_BIGRAMS = {
    ("api", "key"), ("access", "key"), ("private", "key"), ("signing", "key"),
    ("encryption", "key"), ("master", "key"), ("account", "key"), ("shared", "key"),
    ("service", "key"), ("auth", "key"), ("client", "key"), ("app", "key"),
    ("connection", "string"), ("conn", "str"), ("conn", "string"), ("license", "key"),
}
_WEAK_WORDS = {"key", "auth", "authorization", "signature", "salt", "hmac"}
_EXCLUDE_LAST = {
    "url", "uri", "endpoint", "path", "file", "filename", "dir", "name", "label", "field",
    "type", "id", "ids", "length", "len", "min", "max", "regex", "pattern", "hint", "prompt",
    "placeholder", "header", "env", "var", "message", "msg", "error", "errors", "count",
    "policy", "format", "expiry", "expires", "expiration", "ttl", "enabled", "required",
    "reset", "hash", "hashed", "prefix", "suffix", "size", "mode", "version", "algorithm",
    "alg", "method", "provider", "strategy", "validator", "validation", "input", "button",
    "text", "title", "description", "desc", "template", "usage", "scope", "scopes", "param",
    "column", "attr", "attribute", "class", "style", "icon", "visible", "strength",
    "confirm", "confirmation", "changed", "updated", "created", "at", "timeout", "retry",
    "limit", "lifetime", "location", "store", "manager", "factory", "generator", "cache",
    "handler", "callback", "valid", "invalid", "check", "rotation", "ref",
    "reference", "arn", "region", "bucket", "owner", "sample", "example", "file_path",
}
_EXCLUDE_FIRST = {"is", "has", "show", "hide", "toggle", "validate", "check", "get", "set",
                  "on", "handle", "use", "min", "max", "num", "no", "project", "public",
                  "sort", "primary", "foreign", "partition", "cache", "map", "index"}
# Word-like identifiers ("getzerotrace_zerotrace", "acme-prod-cluster"): human-written
# names, not key material. Only used to reject WEAK matches such as a bare "key" in the name.
_NAME_LIKE_RE = re.compile(r"\A[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+){1,}\Z")
_BOOLISH = {"true", "false", "yes", "no", "on", "off", "null", "none", "nil", "undefined"}
_I18N_KEY = re.compile(r"^[a-z][a-zA-Z_]*(\.[a-zA-Z_]+)+$")
_TEMPLATED = re.compile(r"(\$\{|\{\{|#\{|%\(|\{[A-Za-z_][\w.]*\}|\$[A-Z_]{2,})")
# Regex/validation patterns bound to credential-ish names ("_PASSWORD_RE", "secretPattern")
# are rules, not credentials.
_REGEXISH = re.compile(r"(\\[dwsbAZ]|\.\*|\.\+|\[\^|\(\?|\{\d+,|\|\^|\^\(|\)\$)")
_PATHISH = re.compile(
    r"(?:^(?:/|\./|\.\./|~/|[A-Za-z]:\\))"                 # absolute/relative path prefix
    r"|(?:\.(?:pem|key|crt|json|ya?ml|p12|pfx|txt)$)",     # or a filename suffix
)

_CATEGORY_EXPLAIN = {
    "password": "A password is hardcoded. It is readable by everyone with repo access "
                "and stays in git history even after the line is deleted.",
    "api-key": "An API key is hardcoded. Keys in source travel into forks, CI logs, "
               "containers and backups; load it from the environment or a secret manager.",
    "token": "An access token is hardcoded. Anyone who can read the repo can act with "
             "its permissions until it is revoked.",
    "secret": "A secret value is hardcoded in source. Treat it as compromised once pushed "
              "and load it at runtime instead.",
    "private-key": "Private key material is hardcoded. It impersonates its owner and "
                   "must be rotated if pushed.",
    "connection-string": "A connection string with credentials is hardcoded.",
    "generic": "A high-entropy value is bound to a credential-like name.",
}

# Cyrillic/Greek letters that render identically to a Latin letter, used to smuggle a
# credential-like identifier (e.g. "p\u0430ssword") past the ASCII keyword vocabulary below.
_CONFUSABLES = str.maketrans({
    "\u0430": "a", "\u0410": "A", "\u0435": "e", "\u0415": "E", "\u043e": "o", "\u041e": "O",
    "\u0440": "p", "\u0420": "P", "\u0441": "c", "\u0421": "C", "\u0443": "y", "\u0423": "Y",
    "\u0445": "x", "\u0425": "X", "\u043a": "k", "\u041a": "K", "\u043c": "m", "\u041c": "M",
    "\u043d": "h", "\u041d": "H", "\u0442": "t", "\u0422": "T", "\u0432": "b", "\u0412": "B",
    "\u03bf": "o", "\u039f": "O", "\u03b1": "a", "\u0391": "A",
})


def _deconfuse(ident: str) -> str:
    return ident.translate(_CONFUSABLES)


def words_of(ident: str) -> list[str]:
    ident = _deconfuse(ident).lstrip("$@")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", ident)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [w.lower() for w in re.split(r"[\s_.\-]+", s) if w]


def env_name_of(ident: str) -> str:
    words = words_of(ident.split(".")[-1] if "." in ident else ident)
    name = "_".join(words).upper()
    return re.sub(r"[^A-Z0-9_]", "", name) or "SECRET_VALUE"


def _vocab(words: list[str]) -> tuple[str, str] | None:
    """Return (strength, category) for a credential-like identifier, else None."""
    if not words:
        return None
    if words[-1] in _EXCLUDE_LAST or words[0] in _EXCLUDE_FIRST:
        return None
    wset = set(words)
    bigrams = set(zip(words, words[1:], strict=False))
    if wset & _PASSWORDISH:
        return "strong", "password"
    if ("private", "key") in bigrams or "privatekey" in wset:
        return "strong", "private-key"
    if bigrams & {("connection", "string"), ("conn", "str"), ("conn", "string")} or "dsn" in wset:
        return "strong", "connection-string"
    if ("api", "key") in bigrams or "apikey" in wset:
        return "strong", "api-key"
    if wset & {"token", "bearer", "sas"}:
        return "strong", "token"
    if wset & _STRONG_WORDS or bigrams & _STRONG_BIGRAMS:
        return "strong", "secret"
    if wset & _WEAK_WORDS:
        return "weak", "generic"
    return None


def _score_password(length: int, ent: float, classes: int) -> tuple[str, float] | None:
    if length < 4:
        return None
    if (length >= 8 and classes >= 3) or (length >= 12 and ent >= 3.3):
        return "high", 0.85
    return "medium", 0.6


def _score_secret(length: int, ent: float) -> tuple[str, float] | None:
    if length < 8:
        return None
    if (length >= 16 and ent >= 3.5) or (length >= 32 and ent >= 3.0):
        return "high", 0.85
    return ("medium", 0.6) if ent >= 3.0 else ("low", 0.3)


def _score(value: str, strength: str, category: str) -> tuple[str, float] | None:
    length, ent, classes = len(value), shannon(value), char_classes(value)
    if strength == "strong":
        if category == "password":
            return _score_password(length, ent, classes)
        return _score_secret(length, ent)
    # A weak name ("...Key") needs a clearly random value to count for anything.
    if length >= 20 and ent >= 4.0 and not _is_name_like(value):
        return "medium", 0.55
    return None


def _is_name_like(value: str) -> bool:
    """Separator-joined words, e.g. a project key or a cluster name."""
    match = _NAME_LIKE_RE.match(value)
    if not match:
        return False
    words = [w for w in re.split(r"[-_.]", value) if w]
    return sum(1 for w in words if w.isalpha() and len(w) >= 3) >= 2


def _value_is_benign(value: str) -> bool:
    v = value.strip()
    if not v or v.lower() in _BOOLISH or v.isdigit():
        return True
    if is_placeholder(v) or is_uuid(v) or looks_like_prose(v):
        return True
    if _TEMPLATED.search(v) or _I18N_KEY.match(v) or _PATHISH.search(v):
        return True
    if _REGEXISH.search(v) or (v.count("|") >= 2 and " " not in v):
        return True  # a pattern such as "client-key-data|token:|password:"
    if v.lower().startswith(("http://", "https://")) and "@" not in v:
        return True
    # e.g. "password_reset", "auth.token"
    return bool(re.fullmatch(r"[A-Za-z_.]+", v)) and dictionary_ratio(v) >= 0.7


def _unquoted_allowed(path: str) -> bool:
    base = os.path.basename(path).lower()
    return base.startswith(".env") or base.startswith("dockerfile") \
        or base.endswith(_UNQUOTED_FILES_EXT)


def candidates(text: str, path: str):
    """Yield (identifier, value) pairs on a line."""
    if len(text) > _MAX_LINE:
        return
    seen: set[tuple[str, str]] = set()
    for m in _QUOTED_ASSIGN.finditer(text):
        pair = (m.group("ident"), m.group("val"))
        if pair not in seen:
            seen.add(pair)
            yield pair
    if _unquoted_allowed(path):
        for regex in (_UNQUOTED_ASSIGN, _DOCKER_ENV_SPACE):
            um = regex.match(text)
            if um:
                pair = (um.group("ident"), um.group("val"))
                if pair not in seen:
                    seen.add(pair)
                    yield pair


def scan(units: list[Unit], cfg) -> list[Finding]:
    findings: list[Finding] = []
    for unit in units:
        if unit.file_class == "generated":
            continue
        for ident, value in candidates(unit.text, unit.path):
            vocab = _vocab(words_of(ident))
            if vocab is None or _value_is_benign(value):
                continue
            # `password = os.environ["X"] or "fallback"` still hardcodes the fallback, so only
            # skip when the value itself is a lookup, which _value_is_benign already handles.
            strength, category = vocab
            scored = _score(value, strength, category)
            if scored is None:
                continue
            severity, confidence = scored
            findings.append(Finding(
                rule_id=f"hardcoded-{category}", kind=f"hardcoded_{category.replace('-', '_')}",
                severity=severity, confidence=confidence, path=unit.path,
                line_no=unit.line_no, file_class=unit.file_class, line_text=unit.text,
                context_snippet=unit.window, identifier=ident, source="code_assign",
                explanation=_CATEGORY_EXPLAIN[category], env_name=env_name_of(ident),
                matched_value=value,
            ))
    return findings


__all__ = ["scan", "candidates", "words_of", "env_name_of", "is_env_reference"]
