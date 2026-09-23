from zerotrace.classifier.redact import features, redact, scrub_window
from zerotrace.detectors import Finding

from .conftest import rand


def test_redact_never_returns_raw():
    assert "AKIA" not in redact("AKIAEXAMPLE1234567890", "aws_key")


def test_scrub_masks_literals_tokens_and_unquoted_values():
    secret = rand(28)
    window = f'# rotate {secret}\npassword = "{rand(10)}"\nAPI_TOKEN={rand(20)}\nport = 5432'
    out = scrub_window(window)
    assert secret not in out
    assert '"<STR len=10>"' in out and "API_TOKEN=<VAL len=20>" in out
    assert "port = 5432" in out  # structure (identifiers, numbers) stays readable


def test_features_expose_shape_not_value():
    value = "sk_test_" + rand(24)
    f = Finding(rule_id="stripe-test-key", kind="stripe_test_key", severity="medium",
                confidence=0.6, path="src/pay.py", line_no=1, file_class="code",
                line_text=f'stripe.api_key = "{value}"', matched_value=value)
    feats = features(f)
    assert feats["known_public_prefix"] == "sk_test_"
    assert feats["identifier"] == "stripe.api_key"
    assert feats["value_shape"]["length"] == 24
    assert value not in str(feats) and value[8:] not in str(feats)


def test_scrub_keeps_identifiers_readable():
    out = scrub_window(f'from payments.gateway import Gateway\nWEBHOOK_SIGNING_SECRET = "{rand(12)}"\n'
                       f"# rotated {rand(10)}9{rand(10)}")
    assert "payments.gateway" in out and "WEBHOOK_SIGNING_SECRET" in out
    assert "<TOKEN len=21>" in out and '"<STR len=12>"' in out
