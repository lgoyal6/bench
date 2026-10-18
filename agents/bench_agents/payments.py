"""Explicit virtual bonds. This module never calls Visa or moves money."""

from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import uuid4

from bench_contracts.bonds import settle
from bench_contracts.messages import Bid, JobPosted, Submission
from bench_contracts.run import RunResult
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .market import MarketError
from .scoring import score_result

DISCLOSURE = "Demo payment simulation. No funds moved."


class BondTerms(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)

    bond_usd: float = Field(gt=0)
    price_usd: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)

    @field_validator("bond_usd", "price_usd", mode="before")
    @classmethod
    def cents_only(cls, value):
        try:
            amount = Decimal(str(value))
            if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
                raise ValueError("Amounts must be finite whole cents")
        except InvalidOperation as exc:
            raise ValueError("Amounts must be finite whole cents") from exc
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def numeric_confidence(cls, value):
        if isinstance(value, bool):
            raise ValueError("Confidence must be numeric")
        return value


class Allocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Literal["pass", "fail"]
    bond_returned_usd: float
    bond_captured_usd: float
    price_paid_usd: float
    total_to_agent_usd: float


def allocation(terms: BondTerms, passed: bool) -> Allocation:
    """Use the shared scoring rule and fail if float rounding loses a cent."""
    if not isinstance(passed, bool):
        raise MarketError("An allocation requires an explicit pass or fail")
    to_agent, to_customer = settle(terms.price_usd, terms.bond_usd, terms.confidence, passed)
    price = Decimal(str(terms.price_usd)) if passed else Decimal("0")
    returned = Decimal(str(to_agent)) - price
    captured = Decimal(str(to_customer))
    if returned < 0 or captured < 0 or returned + captured != Decimal(str(terms.bond_usd)):
        raise MarketError("Shared settlement rounding does not conserve this bond; review required")
    return Allocation(
        outcome="pass" if passed else "fail",
        bond_returned_usd=float(returned),
        bond_captured_usd=float(captured),
        price_paid_usd=float(price),
        total_to_agent_usd=to_agent,
    )


class SimulatedBond(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["simulation"] = "simulation"
    payment_id: str = Field(pattern=r"^sim_[0-9a-f]{32}$")
    terms: BondTerms
    status: Literal["requested", "reserved", "released", "settled"] = "requested"
    job: JobPosted | None = None
    bid: Bid | None = None
    run_id: str | None = None
    result: Allocation | None = None
    hypothetical: bool = False


def request_bond(terms: BondTerms) -> SimulatedBond:
    # Validate both possible payouts before asking the user to approve a reservation.
    allocation(terms, True)
    allocation(terms, False)
    return SimulatedBond(payment_id=f"sim_{uuid4().hex}", terms=terms)


def request_for_bid(job: JobPosted, bid: Bid) -> SimulatedBond:
    if bid.job_id != job.job_id or bid.price_usd > job.budget_usd:
        raise MarketError("Bid must belong to the job and fit its budget")
    bond = request_bond(
        BondTerms(bond_usd=job.max_bond_usd, price_usd=bid.price_usd, confidence=bid.confidence)
    )
    return bond.model_copy(
        update={"job": job.model_copy(deep=True), "bid": bid.model_copy(deep=True)}
    )


def approve_bond(bond: SimulatedBond) -> SimulatedBond:
    if bond.status == "reserved":
        return bond
    if bond.status != "requested":
        raise MarketError("Only a requested virtual bond can be approved")
    return bond.model_copy(update={"status": "reserved"})


def cancel_bond(bond: SimulatedBond) -> SimulatedBond:
    if bond.status == "released":
        return bond
    if bond.status not in {"requested", "reserved"}:
        raise MarketError("A settled virtual bond cannot be canceled")
    return bond.model_copy(update={"status": "released"})


def settle_demo(bond: SimulatedBond, outcome: Literal["pass", "fail"]) -> SimulatedBond:
    """Explicit virtual ledger exercise, never a hardware result or shared Settlement."""
    if outcome not in {"pass", "fail"} or bond.job is not None or bond.bid is not None:
        raise MarketError("Hypothetical settlement is only for standalone payment demos")
    payout = allocation(bond.terms, outcome == "pass")
    if bond.status == "settled" and bond.hypothetical and bond.result == payout:
        return bond
    if bond.status != "reserved":
        raise MarketError("Only a reserved virtual demo bond can settle hypothetically")
    return bond.model_copy(update={"status": "settled", "result": payout, "hypothetical": True})


def settle_result(
    bond: SimulatedBond, submission: Submission, result: RunResult, *, seed: int
) -> SimulatedBond:
    """Caller supplies a trusted rig result. Never settle an interactive preview.

    This produces a simulation receipt, never a shared Settlement or Award that
    could be mistaken for evidence of a real provider-backed payment.
    """
    if bond.job is None or bond.bid is None:
        raise MarketError("A standalone payment demo has no job or rig result to settle")
    verdict = score_result(bond.job, bond.bid, submission, result, seed=seed)
    if verdict.verdict == "error":
        raise MarketError("Rig errors require review; they do not forfeit the virtual bond")
    expected_terms = BondTerms(
        bond_usd=bond.job.max_bond_usd,
        price_usd=bond.bid.price_usd,
        confidence=bond.bid.confidence,
    )
    if bond.terms != expected_terms:
        raise MarketError("Virtual bond terms no longer match the approved bid")
    payout = allocation(bond.terms, verdict.verdict == "pass")
    if bond.status == "settled":
        if bond.run_id != verdict.run_id or bond.result != payout:
            raise MarketError("Virtual bond was already settled against a different result")
        return bond
    if bond.status != "reserved":
        raise MarketError("Only an approved virtual bond can be settled")
    return bond.model_copy(update={"status": "settled", "run_id": verdict.run_id, "result": payout})


def check_live() -> list[str]:
    """A simulation can never satisfy a live payment health check."""
    return ["M2 payments: simulation only; no Visa API connected and no funds moved"]
