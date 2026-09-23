# PII detection & redaction

Two *different* redactions live in this project — don't conflate them:

1. **Input redaction (privacy of the pipeline).** Before any candidate reaches a
   log or the LLM, replace the real value with a typed, length-hinted token.
   `redact("AKIA...40chars") -> "<AWS_KEY len=40 entropy=hi>"`. This is what keeps
   secrets on-device even when the local model is used.

2. **Output redaction (the remediation).** The fix we propose to the developer,
   chosen by *where* the PII lives:
   - **test fixture** -> synthetic replacement via Faker (`customer@example.test`,
     `+1-555-0100`). Deterministic seeding keeps fixtures stable across runs.
   - **example/config file** -> placeholder / env reference (`${DB_PASSWORD}`).
   - **application code** -> flag for human review; don't auto-rewrite logic.

## Presidio setup
`AnalyzerEngine` (spaCy `en_core_web_lg` recommended over `sm` for recall) +
custom `PatternRecognizer`s. For India, add Aadhaar (12-digit, Verhoeff checksum)
and PAN (`[A-Z]{5}[0-9]{4}[A-Z]`) recognizers with context words ("aadhaar",
"pan", "customer"). `AnonymizerEngine` performs replace/mask/hash/synthetic.

## Context-awareness beats raw pattern matching
An email in `tests/fixtures/` is lower-risk than the same email in
`src/mailer.py`. The policy engine weights the finding by file class
(code / config / test / docs / generated) — see `docs/POLICY.md`.

## PII fingerprints must be salted
A plain SHA of an email is reversible by enumeration. Exception/baseline
fingerprints for PII use HMAC-SHA256 with a per-repo local key, never a bare hash.
