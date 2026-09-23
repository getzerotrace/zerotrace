"""Value-shape features. Used by detectors (scoring) and the classifier (redacted features)."""
import math
import re
from collections import Counter

_HEX = set("0123456789abcdefABCDEF")
_B64 = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-")

# Words that make a value read like prose/placeholder rather than random key material.
# Only the RATIO of covered characters is ever exported, never the words themselves.
_WORDS = sorted({
    "your", "my", "our", "the", "a", "an", "here", "there", "insert", "replace", "put", "enter",
    "change", "me", "changeme", "set", "value", "values", "api", "key", "keys", "secret",
    "secrets", "token", "tokens", "password", "pass", "passwd", "pwd", "auth", "access",
    "private", "public", "client", "server", "user", "username", "admin", "root", "test",
    "testing", "tests", "example", "examples", "sample", "demo", "dummy", "fake", "mock",
    "placeholder", "todo", "fixme", "xxx", "foo", "bar", "baz", "qux", "abc", "default",
    "local", "localhost", "dev", "development", "staging", "prod", "production", "none",
    "null", "empty", "blank", "string", "str", "some", "any", "random", "generated", "new",
    "old", "temp", "tmp", "hello", "world", "stub", "sandbox", "fixture", "data", "db",
    "database", "service", "account", "app", "application", "name", "id", "own", "real",
    "live", "not", "use", "this", "that", "for", "only", "do", "dont", "commit", "safe",
    "secure", "strong", "super", "supersecret", "letmein", "welcome", "qwerty", "hunter",
    "ignore", "redacted", "hidden", "masked", "config", "env", "var", "variable",
}, key=len, reverse=True)


def shannon(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def charset(value: str) -> str:
    chars = set(value)
    if chars and chars <= _HEX:
        return "hex"
    has_lower = any(c.islower() for c in value)
    has_upper = any(c.isupper() for c in value)
    has_digit = any(c.isdigit() for c in value)
    if chars <= _B64:
        if has_lower and has_upper and has_digit:
            return "base62/64"
        return "alnum"
    return "mixed-symbols"


def char_classes(value: str) -> int:
    return sum([
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(not c.isalnum() for c in value),
    ])


def skeleton(value: str, limit: int = 40) -> str:
    """Structure only: letters->a/A, digits->9, separators kept. 'sk-ab12' -> 'aa-aa99'."""
    out = []
    for c in value[:limit]:
        if c.islower():
            out.append("a")
        elif c.isupper():
            out.append("A")
        elif c.isdigit():
            out.append("9")
        elif c in "-_./:+=@":
            out.append(c)
        else:
            out.append("*")
    return "".join(out) + ("…" if len(value) > limit else "")


def dictionary_ratio(value: str) -> float:
    """Fraction of alphabetic characters covered by common prose/placeholder words."""
    alpha_runs = re.findall(r"[a-z]+", value.lower())
    total = sum(len(r) for r in alpha_runs)
    if total == 0:
        return 0.0
    covered = 0
    for run in alpha_runs:
        i = 0
        while i < len(run):
            for word in _WORDS:
                if len(word) >= 2 and run.startswith(word, i):
                    covered += len(word)
                    i += len(word)
                    break
            else:
                i += 1
    return covered / total


def repeated_ratio(value: str) -> float:
    """Share of the value that is its single most common character."""
    if not value:
        return 0.0
    return Counter(value).most_common(1)[0][1] / len(value)


def shape(value: str) -> dict:
    return {
        "length": len(value),
        "charset": charset(value),
        "char_classes": char_classes(value),
        "entropy_bits_per_char": round(shannon(value), 2),
        "skeleton": skeleton(value),
        "dictionary_word_ratio": round(dictionary_ratio(value), 2),
        "most_common_char_ratio": round(repeated_ratio(value), 2),
    }
