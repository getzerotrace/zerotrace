"""PII scan: regex + checksum validators (Luhn, Verhoeff, IBAN mod-97). Presidio is opt-in.

Presidio/spaCy is NOT imported unless `policy.pii.engine: presidio` is configured: importing
it costs seconds on every commit.
"""
import re

from . import Finding
from ..collectors.staged_diff import Unit

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])"   # NNN-NNN-NNNN
    r"|(?<![\w.])\+91[ -]?[6-9]\d{4}[ -]?\d{5}(?![\w.])"                       # +91 NNNNN NNNNN
)
_PHONE_BARE_RE = re.compile(r"(?<![\w.])[6-9]\d{9}(?![\w.])")
_PHONE_CONTEXT_RE = re.compile(r"(?i)(phone|mobile|tel\b|contact|whatsapp|cell)")
# Internal company-issued identifier: "QX" followed by 5 upper-case alphanumerics.
_QXID_RE = re.compile(r"\bQX[A-Z0-9]{5}\b")
_PAN_RE = re.compile(r"\b[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]\b")
# No word character on either side, as for phones: a checksum-valid run of digits inside a hex
# digest (a lock file's `--hash=sha256:…`, a git object id) belongs to that token, not a person.
_AADHAAR_RE = re.compile(r"(?<![\w-])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![\w-])")
_CARD_RE = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")

# Internal/employee email domains -> higher severity than a generic email. Which domains
# those are is set by the organisation (`policy.pii.internal_domains`, see
# deploy/policy.example.yml); a company's domain list does not belong in this tool's source.
# RFC 2606 reserved domains -> our own synthetic replacements land here; don't
# re-flag them as a fresh finding on the next scan.
_RESERVED_DOMAINS = {"example.com", "example.org", "example.net", "example.test", "localhost"}
_NON_PERSON_LOCALS = {"git", "noreply", "no-reply", "donotreply", "mailer-daemon", "postmaster"}
_KNOWN_TEST_CARDS = {"4111111111111111", "4242424242424242", "5555555555554444",
                     "378282246310005", "6011111111111117", "4000056655665556"}

# Verhoeff tables (Aadhaar check digit)
_V_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
        [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
        [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
        [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
        [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_V_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
        [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
        [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
        [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _V_D[c][_V_P[i % 8][int(ch)]]
    return c == 0


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def iban_valid(iban: str) -> bool:
    s = iban.replace(" ", "")
    if not (15 <= len(s) <= 34):
        return False
    rearranged = s[4:] + s[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1


def _card_brand_plausible(d: str) -> bool:
    return (d[0] == "4" and len(d) in (13, 16, 19)) or \
        (d[:2] in {"51", "52", "53", "54", "55"} and len(d) == 16) or \
        (d[:2] in {"22", "23", "24", "25", "26", "27"} and len(d) == 16) or \
        (d[:2] in {"34", "37"} and len(d) == 15) or \
        (d[0] == "6" and len(d) in (16, 19))


def _mk(unit: Unit, rule_id: str, severity: str, confidence: float, value: str,
        explanation: str) -> Finding:
    return Finding(
        rule_id=rule_id, kind=rule_id, severity=severity, confidence=confidence,
        path=unit.path, line_no=unit.line_no, file_class=unit.file_class,
        line_text=unit.text, context_snippet=unit.window, source="pii",
        explanation=explanation, matched_value=value,
    )


_URI_USERINFO_RE = re.compile(r"://[^\s/@]*$")


def _email_finding(unit: Unit, match: re.Match,
                   internal_domains: frozenset[str]) -> Finding | None:
    if _URI_USERINFO_RE.search(unit.text[:match.start()]):
        return None  # userinfo in a URI is a credential; the rule pack handles it
    local, domain = match.group(0).rsplit("@", 1)
    domain = domain.lower()
    if local.lower() in _NON_PERSON_LOCALS:
        return None
    internal = domain in internal_domains
    reserved = domain in _RESERVED_DOMAINS or domain.endswith((".example", ".test", ".invalid"))
    severity = "low" if reserved else ("high" if internal else "medium")
    confidence = 0.2 if reserved else (0.9 if internal else 0.6)
    rule = "pii_email_internal" if internal else "pii_email"
    explain = ("An internal employee email address. Staff identities in source enable "
               "phishing and violate data-minimisation rules." if internal else
               "An email address. If it belongs to a real customer or person, it is "
               "personal data under GDPR/DPDP and should be replaced with synthetic data.")
    return _mk(unit, rule, severity, confidence, match.group(0), explain)


def _emails(unit: Unit, internal_domains: frozenset[str] = frozenset()) -> list[Finding]:
    found = [_email_finding(unit, m, internal_domains) for m in _EMAIL_RE.finditer(unit.text)]
    return [f for f in found if f is not None]


def _phones(unit: Unit) -> list[Finding]:
    out = [_mk(unit, "pii_phone", "medium", 0.55, m.group(0),
               "A telephone number. Real numbers are personal data; use +1-555-0100-style fakes.")
           for m in _PHONE_RE.finditer(unit.text)]
    if _PHONE_CONTEXT_RE.search(unit.text):
        out += [_mk(unit, "pii_phone", "medium", 0.55, m.group(0),
                    "A mobile number next to a phone/contact field.")
                for m in _PHONE_BARE_RE.finditer(unit.text)]
    return out


def _simple_matches(unit: Unit) -> list[Finding]:
    """Patterns that need no checksum: internal ids and PAN."""
    out = []
    for regex, rule, severity, confidence, why in (
        (_QXID_RE, "qxid_internal_id", "high", 0.85,
         "An internal employee identifier (QX-ID). It links code to a real staff member."),
        (_PAN_RE, "pii_pan_india", "high", 0.8,
         "An Indian PAN (tax ID) number. This is regulated personal data under the DPDP Act."),
    ):
        out += [_mk(unit, rule, severity, confidence, m.group(0), why)
                for m in regex.finditer(unit.text)]
    return out


def _aadhaar(unit: Unit) -> list[Finding]:
    out = []
    for m in _AADHAAR_RE.finditer(unit.text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 12 and verhoeff_valid(digits):
            out.append(_mk(unit, "pii_aadhaar", "high", 0.9, m.group(0),
                           "A checksum-valid Aadhaar number. This is regulated national-ID data "
                           "(UIDAI/DPDP)."))
    return out


def _cards(unit: Unit) -> list[Finding]:
    out = []
    for m in _CARD_RE.finditer(unit.text):
        digits = re.sub(r"\D", "", m.group(0))
        if not (13 <= len(digits) <= 19 and _card_brand_plausible(digits) and luhn_valid(digits)):
            continue
        known_test = digits in _KNOWN_TEST_CARDS
        out.append(_mk(
            unit, "pii_payment_card", "low" if known_test else "high",
            0.3 if known_test else 0.9, m.group(0),
            "A well-known processor test card number." if known_test else
            "A Luhn-valid payment card number. PCI-DSS forbids storing it in source.",
        ))
    return out


def _ibans(unit: Unit) -> list[Finding]:
    return [_mk(unit, "pii_iban", "high", 0.85, m.group(0),
                "A checksum-valid IBAN (bank account number).")
            for m in _IBAN_RE.finditer(unit.text) if iban_valid(m.group(0))]


# Everything but the email scanner is decided by the value alone; the email one also needs to
# know which domains this organisation calls its own.
_SCANNERS = (_phones, _simple_matches, _aadhaar, _cards, _ibans)


def _regex_scan(units: list[Unit], internal_domains: frozenset[str] = frozenset()) -> list[Finding]:
    findings: list[Finding] = []
    for unit in units:
        if unit.file_class == "generated":
            continue
        findings += _emails(unit, internal_domains)
        for scanner in _SCANNERS:
            findings += scanner(unit)
    return findings


def _presidio_scan(units: list[Unit]) -> list[Finding]:
    try:
        from presidio_analyzer import AnalyzerEngine
    except ImportError:
        return []
    analyzer = AnalyzerEngine()
    findings: list[Finding] = []
    for unit in units:
        if unit.file_class == "generated":
            continue
        for res in analyzer.analyze(text=unit.text, language="en",
                                    entities=["PERSON", "LOCATION", "US_SSN", "UK_NHS"]):
            if res.score < 0.6:
                continue
            value = unit.text[res.start:res.end]
            findings.append(_mk(unit, f"pii_{res.entity_type.lower()}", "medium", res.score,
                                value, f"Presidio NER detected {res.entity_type}."))
    return findings


def scan(units: list[Unit], cfg) -> list[Finding]:
    internal = frozenset(getattr(cfg, "pii_internal_domains", ()) or ())
    findings = _regex_scan(units, internal)
    if getattr(cfg, "pii_engine", "regex") == "presidio":
        findings += _presidio_scan(units)
    return findings
