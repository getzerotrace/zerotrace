from payments.gateway import Gateway

GATEWAY_API_KEY = "{{gen:base62:32}}"


def test_charge_uses_sandbox_gateway():
    assert Gateway(api_key=GATEWAY_API_KEY, sandbox=True).charge(100).ok
