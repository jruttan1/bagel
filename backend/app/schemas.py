from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator


class SignupRequest(BaseModel):
    phone_number: str = Field(min_length=7, max_length=32)
    timezone: str = "America/Toronto"


class SignupResponse(BaseModel):
    user_id: UUID
    status: Literal["message_queued", "already_registered", "needs_first_message"]
    line_handle: str | None = None


class WealthsimpleConnectRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    username: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)
    otp: str | None = Field(default=None, min_length=4, max_length=12)


class WealthsimpleConnectResponse(BaseModel):
    status: Literal["connected", "otp_required"]


class ConnectionTokenStatus(BaseModel):
    valid: bool
    phone_hint: str | None = None


class ThesisUpsertRequest(BaseModel):
    ticker: str
    why_owned: str | None = None
    bull_case: str | None = None
    bear_case: str | None = None
    important_metrics: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    sophistication: Literal["unknown", "basic", "developing", "advanced"] = "unknown"

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()


class SpectrumInboundMessage(BaseModel):
    delivery_id: str
    provider_message_id: str
    sender: str
    text: str = Field(min_length=1, max_length=10000)
    timestamp: datetime | None = None


class MessageSendResult(BaseModel):
    id: str
    status: str
    request_id: str | None = None


class SyncResult(BaseModel):
    snapshot_id: UUID
    account_count: int
    holding_count: int
    transaction_count: int
    total_value: float
    cash: float
    captured_at: datetime


class HealthResponse(BaseModel):
    status: str
    database: str | None = None


class MarketSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str
    name: str | None = None
    portfolio_weight: float = 0
    regular_price: float | None = None
    regular_change_percent: float | None = None
    extended_price: float | None = None
    extended_change_percent: float | None = None
    estimated_portfolio_effect: float = 0
    session: Literal["regular", "premarket", "afterhours", "closed"]
    observed_at: datetime
    is_fresh: bool
    rank_score: float = 0


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    kind: Literal["price", "business", "event", "market_narrative"]
    tickers: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]


class ResearchAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_change: bool
    lead_tickers: list[str] = Field(default_factory=list)
    facts: list[EvidenceClaim] = Field(default_factory=list)
    interpretations: list[EvidenceClaim] = Field(default_factory=list)
    thesis_effect: str | None = None
    next_evidence: str | None = None
    avoid_claims: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class MessageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1800)
    emphasis_phrase: str | None = Field(default=None, max_length=100)
    _evidence: dict = PrivateAttr(default_factory=dict)


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    needs_current_research: bool
    tickers: list[str] = Field(default_factory=list)
    question: str | None = None
