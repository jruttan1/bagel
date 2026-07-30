import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base


def json_dict_type():
    return MutableDict.as_mutable(JSON().with_variant(JSONB, "postgresql"))


def json_list_type():
    return MutableList.as_mutable(JSON().with_variant(JSONB, "postgresql"))


def utcnow() -> datetime:
    return datetime.now(UTC)


class ConnectionStatus(enum.StrEnum):
    pending = "pending"
    connected = "connected"
    reauth_required = "reauth_required"
    error = "error"
    disconnected = "disconnected"


class OnboardingStep(enum.StrEnum):
    awaiting_connection = "awaiting_connection"
    financial_position = "financial_position"
    investing_style = "investing_style"
    portfolio_context = "portfolio_context"
    complete = "complete"


class MessageDirection(enum.StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Toronto")
    preferences: Mapped[dict] = mapped_column(json_dict_type(), default=dict)
    notification_settings: Mapped[dict] = mapped_column(
        json_dict_type(), default=lambda: {"morning_brief": True, "event_alerts": True, "brief_time": "07:30"}
    )
    onboarding_step: Mapped[OnboardingStep] = mapped_column(
        Enum(OnboardingStep, native_enum=False), default=OnboardingStep.awaiting_connection
    )
    profile_summary: Mapped[str | None] = mapped_column(Text)
    profile_data: Mapped[dict] = mapped_column(json_dict_type(), default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    wealthsimple_connection: Mapped["WealthsimpleConnection | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    snapshots: Mapped[list["PortfolioSnapshot"]] = relationship(back_populates="user")
    theses: Mapped[list["InvestmentThesis"]] = relationship(back_populates="user")


class WealthsimpleConnection(Base):
    __tablename__ = "wealthsimple_connections"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    encrypted_session: Mapped[str] = mapped_column(Text)
    encrypted_username: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ConnectionStatus] = mapped_column(
        Enum(ConnectionStatus, native_enum=False), default=ConnectionStatus.pending
    )
    last_successful_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    user: Mapped[User] = relationship(back_populates="wealthsimple_connection")


class ConnectionToken(Base):
    __tablename__ = "connection_tokens"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrokerageAccount(Base):
    __tablename__ = "brokerage_accounts"
    __table_args__ = (UniqueConstraint("user_id", "provider_account_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_account_id: Mapped[str] = mapped_column(String(160))
    account_number: Mapped[str | None] = mapped_column(String(80))
    account_type: Mapped[str] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(8), default="CAD")
    display_name: Mapped[str | None] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    provider_data: Mapped[dict] = mapped_column(json_dict_type(), default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (Index("ix_snapshot_user_captured", "user_id", "captured_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CAD")
    allocation: Mapped[dict] = mapped_column(json_dict_type(), default=dict)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    user: Mapped[User] = relationship(back_populates="snapshots")
    holdings: Mapped[list["Holding"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (Index("ix_holding_snapshot_ticker", "snapshot_id", "ticker"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str | None] = mapped_column(String(160))
    security_id: Mapped[str | None] = mapped_column(String(160))
    ticker: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(200))
    shares: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=0)
    average_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    current_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CAD")
    portfolio_weight: Mapped[Decimal] = mapped_column(Numeric(9, 6), default=0)
    sector: Mapped[str | None] = mapped_column(String(120))
    industry: Mapped[str | None] = mapped_column(String(160))
    provider_data: Mapped[dict] = mapped_column(json_dict_type(), default=dict)
    snapshot: Mapped[PortfolioSnapshot] = relationship(back_populates="holdings")


class InvestmentThesis(Base):
    __tablename__ = "investment_theses"
    __table_args__ = (UniqueConstraint("user_id", "ticker", "is_active"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    why_owned: Mapped[str | None] = mapped_column(Text)
    bull_case: Mapped[str | None] = mapped_column(Text)
    bear_case: Mapped[str | None] = mapped_column(Text)
    important_metrics: Mapped[list] = mapped_column(json_list_type(), default=list)
    invalidation_conditions: Mapped[list] = mapped_column(json_list_type(), default=list)
    sophistication: Mapped[str] = mapped_column(String(32), default="unknown")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    source: Mapped[str] = mapped_column(String(32), default="conversation")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    user: Mapped[User] = relationship(back_populates="theses")


class OnboardingAnswer(Base):
    __tablename__ = "onboarding_answers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(160), unique=True)
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection, native_enum=False))
    content: Mapped[str] = mapped_column(Text)
    provider_data: Mapped[dict] = mapped_column(json_dict_type(), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    delivery_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    event_name: Mapped[str] = mapped_column(String(80))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketEvent(Base):
    __tablename__ = "market_events"
    __table_args__ = (Index("ix_market_event_ticker_occurred", "ticker", "occurred_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticker: Mapped[str | None] = mapped_column(String(32), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    headline: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    impact_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    sentiment: Mapped[str | None] = mapped_column(String(24))
    raw_data: Mapped[dict] = mapped_column(json_dict_type(), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchCache(Base):
    __tablename__ = "research_cache"
    cache_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    payload: Mapped[dict] = mapped_column(json_dict_type(), default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("user_id", "provider_transaction_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_transaction_id: Mapped[str] = mapped_column(String(200))
    account_id: Mapped[str | None] = mapped_column(String(160))
    transaction_type: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CAD")
    description: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict] = mapped_column(json_dict_type(), default=dict)


class MorningBrief(Base):
    __tablename__ = "morning_briefs"
    __table_args__ = (UniqueConstraint("user_id", "brief_date"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    brief_date: Mapped[date] = mapped_column(Date)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("portfolio_snapshots.id"))
    content: Mapped[str] = mapped_column(Text)
    provider_outbox_id: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
