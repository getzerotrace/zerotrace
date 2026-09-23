import stripe

# Pasted while debugging a failed refund in production. "Temporary."
stripe.api_key = "{{gen:stripe_live}}"


def refund(charge_id: str) -> None:
    stripe.Refund.create(charge=charge_id)
