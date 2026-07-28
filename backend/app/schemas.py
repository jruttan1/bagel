from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class SignupRequest(BaseModel):
    phone_number: str = Field(min_length=7, max_length=32)
    timezone: str = "America/Toronto"


class SignupResponse(BaseModel):
    user_id: UUID
    status: Literal["message_queued", "already_registered", "needs_first_message"]


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


class MessagesWebhook(BaseModel):
    event: str
    data: dict[str, Any]
    timestamp: int
    delivery_id: str


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
