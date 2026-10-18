"""Bond settlement rule.

Every bidder posts the same bond (JobPosted.max_bond_usd) and states a confidence.
The agent gets back bond * brier_score(confidence, passed); the customer keeps the rest.
The Brier score is a proper scoring rule, so an agent expects the most money back by
stating its true probability of passing. The price is paid only on a pass.
"""


def brier_score(confidence: float, passed: bool) -> float:
    """1 minus squared error between stated confidence and the outcome; in [0, 1]."""
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be in [0, 1]")
    outcome = 1.0 if passed else 0.0
    return 1.0 - (outcome - confidence) ** 2


def settle(price_usd: float, bond_usd: float, confidence: float, passed: bool) -> tuple[float, float]:
    """Return (paid_to_agent_usd, paid_to_customer_usd), rounded to cents."""
    returned = bond_usd * brier_score(confidence, passed)
    to_agent = (price_usd if passed else 0.0) + returned
    to_customer = bond_usd - returned
    return round(to_agent, 2), round(to_customer, 2)
