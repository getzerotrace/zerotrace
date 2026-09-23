"""Stable, non-reversible fingerprints. Salted HMAC for PII."""
import hmac
import hashlib

def finding_id(rule_id: str, path: str, line_text_hash: str) -> str:
    """Scope an exception to a specific line; invalidated if the line changes."""
    h = hashlib.sha256()
    h.update(f"{rule_id}|{path}|{line_text_hash}".encode())
    return h.hexdigest()

def pii_fingerprint(value: str, repo_key: bytes) -> str:
    """HMAC (not a bare hash) so an email can't be enumerated back."""
    return hmac.new(repo_key, value.encode(), hashlib.sha256).hexdigest()

def of_finding(finding) -> str:
    """Convenience: the fingerprint used to key an exception/audit entry."""
    line_hash = hashlib.sha256((finding.line_text or "").encode("utf-8")).hexdigest()
    return finding_id(finding.rule_id, finding.path, line_hash)
