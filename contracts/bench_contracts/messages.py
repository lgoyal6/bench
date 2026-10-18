"""Payloads of the messages Bench's Fetch agents exchange.

The market lane wraps these in uAgents models; the field names here are the contract.
"""

from typing import Literal

from pydantic import BaseModel, Field

from .run import Commit, Contestant, Metrics


class JobPosted(BaseModel):
    job_id: str
    task_id: str
    customer: str = Field(description="Agentverse or ASI:One user address")
    budget_usd: float = Field(gt=0)
    max_bond_usd: float = Field(gt=0, description="Bond every bidder posts for this job")


class Bid(BaseModel):
    job_id: str
    contestant: Contestant
    representative: str = Field(description="Agentverse address of the contestant's representative")
    price_usd: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1, description="Stated probability the code passes")


class Award(BaseModel):
    job_id: str
    contestant: Contestant
    bond_payment_id: str = Field(description="Payment Protocol transaction ID of the bond hold")


class Submission(BaseModel):
    job_id: str
    contestant: Contestant
    commit: Commit
    session_ref: str = Field(description="Devin session URL or other agent session ID")
    attempt: int = Field(ge=1)


class Verdict(BaseModel):
    job_id: str
    run_id: str
    verdict: Literal["pass", "fail", "error"]
    metrics: Metrics
    trace_uri: str


class Settlement(BaseModel):
    job_id: str
    run_id: str
    paid_to_agent_usd: float = Field(ge=0, description="Price on pass plus bond returned")
    paid_to_customer_usd: float = Field(ge=0, description="Bond share kept by the customer")
    payment_ids: list[str]
