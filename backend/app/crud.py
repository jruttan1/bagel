"""All database reads, writes, and transactions for Bagel."""

import hashlib
import json
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import (
    BrokerageAccount,
    ConnectionStatus,
    ConnectionToken,
    ConversationMessage,
    Holding,
    InvestmentThesis,
    MarketEvent,
    MessageDirection,
    MorningBrief,
    OnboardingAnswer,
    OnboardingStep,
    PortfolioSnapshot,
    Transaction,
    User,
    WealthsimpleConnection,
    WebhookDelivery,
)
from app.schemas import SyncResult


def _users():
    return select(User).options(selectinload(User.wealthsimple_connection), selectinload(User.theses))


async def ready() -> None:
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))


async def user_by_id(user_id: UUID) -> User | None:
    async with SessionLocal() as session:
        return (await session.execute(_users().where(User.id == user_id))).scalar_one_or_none()


async def get_or_create_user(phone: str, timezone: str = "America/Toronto") -> tuple[User, bool]:
    async with SessionLocal() as session:
        user = (await session.execute(_users().where(User.phone_number == phone))).scalar_one_or_none()
        if user:
            return user, False
        user = User(phone_number=phone, timezone=timezone)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user, True


async def register_delivery(delivery_id: str) -> bool:
    async with SessionLocal() as session:
        session.add(WebhookDelivery(delivery_id=delivery_id, event_name="message.received"))
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False


async def record_inbound(phone: str, content: str, provider_id: str | None, data: dict) -> User:
    async with SessionLocal() as session:
        user = (await session.execute(_users().where(User.phone_number == phone))).scalar_one_or_none()
        if user is None:
            user = User(phone_number=phone)
            session.add(user)
            await session.flush()
        session.add(
            ConversationMessage(
                user_id=user.id,
                provider_message_id=provider_id,
                direction=MessageDirection.inbound,
                content=content,
                provider_data=data,
            )
        )
        await session.commit()
        return user


async def record_outbound(user_id: UUID, content: str, provider_id: str | None, data: dict) -> None:
    async with SessionLocal() as session:
        session.add(
            ConversationMessage(
                user_id=user_id,
                provider_message_id=provider_id,
                direction=MessageDirection.outbound,
                content=content,
                provider_data=data,
            )
        )
        await session.commit()


async def recent_messages(user_id: UUID, limit: int = 12) -> list[ConversationMessage]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.user_id == user_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))


async def latest_snapshot(user_id: UUID) -> PortfolioSnapshot | None:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(PortfolioSnapshot)
                .where(PortfolioSnapshot.user_id == user_id)
                .options(selectinload(PortfolioSnapshot.holdings))
                .order_by(PortfolioSnapshot.captured_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def previous_snapshot(user_id: UUID, before: datetime) -> PortfolioSnapshot | None:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(PortfolioSnapshot)
                .where(PortfolioSnapshot.user_id == user_id, PortfolioSnapshot.captured_at < before)
                .options(selectinload(PortfolioSnapshot.holdings))
                .order_by(PortfolioSnapshot.captured_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def issue_token(user_id: UUID, ttl_minutes: int) -> str:
    token = secrets.token_urlsafe(32)
    async with SessionLocal() as session:
        await session.execute(
            update(ConnectionToken)
            .where(ConnectionToken.user_id == user_id, ConnectionToken.used_at.is_(None))
            .values(used_at=datetime.now(UTC))
        )
        session.add(
            ConnectionToken(
                user_id=user_id,
                token_hash=_token_hash(token),
                expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
            )
        )
        await session.commit()
    return token


async def resolve_token(token: str) -> tuple[ConnectionToken, User] | None:
    async with SessionLocal() as session:
        row = await session.scalar(
            select(ConnectionToken).where(ConnectionToken.token_hash == _token_hash(token))
        )
        if row is None or row.used_at is not None or _utc(row.expires_at) <= datetime.now(UTC):
            return None
        user = await session.get(User, row.user_id)
        return (row, user) if user and user.is_active else None


async def consume_token(token_id: UUID, user_id: UUID) -> None:
    async with SessionLocal() as session:
        token = await session.get(ConnectionToken, token_id)
        user = await session.get(User, user_id)
        if token and user:
            token.used_at = datetime.now(UTC)
            user.onboarding_step = OnboardingStep.financial_position
            await session.commit()


async def save_question(user_id: UUID, question: str) -> User:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise LookupError("User not found")
        profile = dict(user.profile_data)
        profile["last_onboarding_question"] = question
        user.profile_data = profile
        await session.commit()
        return user


async def start_onboarding(user_id: UUID) -> User:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise LookupError("User not found")
        user.onboarding_step = OnboardingStep.financial_position
        await session.commit()
        return user


async def save_answer(user_id: UUID, answer: str) -> User:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise LookupError("User not found")
        session.add(
            OnboardingAnswer(
                user_id=user.id,
                category=user.onboarding_step.value,
                question=str(user.profile_data.get("last_onboarding_question") or ""),
                answer=answer[:1000],
            )
        )
        user.onboarding_step = {
            OnboardingStep.financial_position: OnboardingStep.investing_style,
            OnboardingStep.investing_style: OnboardingStep.portfolio_context,
            OnboardingStep.portfolio_context: OnboardingStep.complete,
        }.get(user.onboarding_step, OnboardingStep.financial_position)
        await session.commit()
        return user


async def answers(user_id: UUID) -> list[dict[str, str]]:
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(OnboardingAnswer)
                    .where(OnboardingAnswer.user_id == user_id)
                    .order_by(OnboardingAnswer.created_at)
                )
            )
            .scalars()
            .all()
        )
    return [{"category": row.category, "question": row.question, "answer": row.answer} for row in rows]


async def save_profile(user_id: UUID, profile: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise LookupError("User not found")
        profile = dict(profile)
        profile.pop("last_onboarding_question", None)
        user.profile_data = profile
        user.profile_summary = str(profile.get("summary") or "")
        await session.commit()


async def upsert_thesis(user_id: UUID, ticker: str, values: dict) -> InvestmentThesis | None:
    async with SessionLocal() as session:
        if await session.get(User, user_id) is None:
            return None
        thesis = await session.scalar(
            select(InvestmentThesis).where(
                InvestmentThesis.user_id == user_id,
                InvestmentThesis.ticker == ticker,
                InvestmentThesis.is_active.is_(True),
            )
        )
        if thesis is None:
            thesis = InvestmentThesis(user_id=user_id, ticker=ticker, **values)
            session.add(thesis)
        else:
            for key, value in values.items():
                setattr(thesis, key, value)
        await session.commit()
        return thesis


async def connection(user_id: UUID) -> WealthsimpleConnection | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(WealthsimpleConnection).where(WealthsimpleConnection.user_id == user_id)
        )


async def save_connection(user_id: UUID, encrypted_session: str, encrypted_username: str) -> None:
    async with SessionLocal() as session:
        row = await session.scalar(
            select(WealthsimpleConnection).where(WealthsimpleConnection.user_id == user_id)
        )
        if row is None:
            row = WealthsimpleConnection(user_id=user_id, encrypted_session=encrypted_session)
            session.add(row)
        row.encrypted_session = encrypted_session
        row.encrypted_username = encrypted_username
        row.status = ConnectionStatus.connected
        row.last_error = None
        await session.commit()


async def mark_connection_error(user_id: UUID, reauth: bool) -> None:
    async with SessionLocal() as session:
        row = await session.scalar(
            select(WealthsimpleConnection).where(WealthsimpleConnection.user_id == user_id)
        )
        if row:
            row.status = ConnectionStatus.reauth_required if reauth else ConnectionStatus.error
            row.last_error = "Wealthsimple synchronization failed"
            await session.commit()


async def save_sync(
    user_id: UUID,
    encrypted_session: str,
    accounts: list[dict],
    positions: list[dict],
    activities: list[tuple[str, dict]],
    historical: list[dict],
) -> SyncResult:
    async with SessionLocal() as session:
        ws = await session.scalar(
            select(WealthsimpleConnection).where(WealthsimpleConnection.user_id == user_id)
        )
        if ws is None:
            raise LookupError("Wealthsimple connection not found")
        for account in accounts:
            provider_id = account.get("id")
            if not provider_id:
                continue
            row = await session.scalar(
                select(BrokerageAccount).where(
                    BrokerageAccount.user_id == user_id,
                    BrokerageAccount.provider_account_id == provider_id,
                )
            )
            values = {
                "account_number": account.get("number"),
                "account_type": account.get("unifiedAccountType") or account.get("description") or "unknown",
                "currency": account.get("currency") or "CAD",
                "display_name": account.get("description"),
                "provider_data": _redact_account(account),
                "is_active": True,
            }
            if row is None:
                session.add(BrokerageAccount(user_id=user_id, provider_account_id=provider_id, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
        await _historical(session, user_id, historical)
        holdings_total = sum((row["current_value"] for row in positions), Decimal("0"))
        account_total = sum((_account_value(row) for row in accounts), Decimal("0"))
        cash = max(Decimal("0"), account_total - holdings_total)
        total = account_total if account_total > 0 else holdings_total
        for row in positions:
            row["portfolio_weight"] = row["current_value"] / total if total else Decimal("0")
        source = [
            {"ticker": row["ticker"], "shares": str(row["shares"]), "value": str(row["current_value"])}
            for row in sorted(positions, key=lambda item: (item["ticker"], item.get("account_id") or ""))
        ]
        snapshot = PortfolioSnapshot(
            user_id=user_id,
            total_value=total,
            cash=cash,
            currency="CAD",
            allocation={row["ticker"]: float(row["portfolio_weight"]) for row in positions},
            source_hash=hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(),
        )
        session.add(snapshot)
        await session.flush()
        for row in positions:
            session.add(Holding(snapshot_id=snapshot.id, user_id=user_id, **row))
        transaction_count = 0
        for account_id, activity in activities:
            provider_id = str(activity.get("canonicalId") or activity.get("id") or "")
            if not provider_id or await session.scalar(
                select(Transaction.id).where(
                    Transaction.user_id == user_id,
                    Transaction.provider_transaction_id == provider_id,
                )
            ):
                continue
            session.add(
                Transaction(
                    user_id=user_id,
                    provider_transaction_id=provider_id,
                    account_id=account_id,
                    transaction_type=str(activity.get("type") or "unknown"),
                    occurred_at=_parse_datetime(activity.get("occurredAt")),
                    amount=_decimal(activity.get("amount")),
                    currency=str(activity.get("currency") or "CAD"),
                    description=activity.get("description"),
                    raw_data=activity,
                )
            )
            transaction_count += 1
        ws.encrypted_session = encrypted_session
        ws.status = ConnectionStatus.connected
        ws.last_successful_sync = datetime.now(UTC)
        ws.last_error = None
        await session.commit()
        return SyncResult(
            snapshot_id=snapshot.id,
            account_count=len({row.get("id") for row in accounts if row.get("id")}),
            holding_count=len(positions),
            transaction_count=transaction_count,
            total_value=float(total),
            cash=float(cash),
            captured_at=snapshot.captured_at,
        )


async def active_users() -> list[User]:
    async with SessionLocal() as session:
        return list((await session.execute(_users().where(User.is_active.is_(True)))).scalars())


async def brief_exists(user_id: UUID, brief_date: date) -> bool:
    async with SessionLocal() as session:
        return bool(
            await session.scalar(
                select(MorningBrief.id).where(
                    MorningBrief.user_id == user_id, MorningBrief.brief_date == brief_date
                )
            )
        )


async def record_brief(
    user_id: UUID,
    brief_date: date,
    snapshot_id: UUID,
    content: str,
    provider_id: str,
    evidence: dict | None = None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            MorningBrief(
                user_id=user_id,
                brief_date=brief_date,
                snapshot_id=snapshot_id,
                content=content,
                evidence_data=evidence or {},
                provider_outbox_id=provider_id,
            )
        )
        await session.commit()


async def held_tickers() -> set[str]:
    async with SessionLocal() as session:
        return set((await session.execute(select(Holding.ticker).distinct())).scalars())


async def add_events(rows: list[dict]) -> int:
    inserted = 0
    async with SessionLocal() as session:
        for row in rows:
            exists = await session.scalar(
                select(MarketEvent.id).where(
                    MarketEvent.ticker == row["ticker"],
                    MarketEvent.event_type == row["event_type"],
                    MarketEvent.occurred_at == row["occurred_at"],
                )
            )
            if not exists:
                session.add(MarketEvent(**row))
                inserted += 1
        await session.commit()
    return inserted


async def market_events(
    tickers: set[str], since: datetime, days: int = 14
) -> list[dict]:
    if not tickers:
        return []
    until = datetime.now(UTC) + timedelta(days=days)
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(MarketEvent)
                    .where(
                        MarketEvent.ticker.in_(tickers),
                        MarketEvent.occurred_at >= since,
                        MarketEvent.occurred_at <= until,
                    )
                    .order_by(MarketEvent.occurred_at)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "ticker": row.ticker,
            "event_type": row.event_type,
            "occurred_at": row.occurred_at.isoformat(),
            "headline": row.headline,
            "summary": row.summary,
        }
        for row in rows
    ]


async def set_brief_time(user_id: UUID, brief_time: str) -> User:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise LookupError("User not found")
        settings = dict(user.notification_settings)
        settings["brief_time"] = brief_time
        user.notification_settings = settings
        await session.commit()
        return user


async def _historical(session, user_id: UUID, history: list[dict]) -> None:
    existing = {
        value.date()
        for value in (
            await session.execute(
                select(PortfolioSnapshot.captured_at).where(PortfolioSnapshot.user_id == user_id)
            )
        ).scalars()
    }
    today = datetime.now(UTC).date()
    for edge in history:
        row = edge.get("node", edge)
        try:
            captured = datetime.fromisoformat(str(row.get("date")).replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            continue
        if captured >= today or captured in existing:
            continue
        total = _decimal(_dig(row, "netLiquidationValueV2", "amount"))
        session.add(
            PortfolioSnapshot(
                user_id=user_id,
                captured_at=datetime.combine(captured, datetime.min.time(), tzinfo=UTC),
                total_value=total,
                cash=0,
                currency=_dig(row, "netLiquidationValueV2", "currency") or "CAD",
                allocation={},
                source_hash=hashlib.sha256(f"historical:{captured}:{total}".encode()).hexdigest(),
            )
        )
        existing.add(captured)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _dig(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _account_value(account: dict) -> Decimal:
    return _decimal(
        _dig(account, "financials", "currentCombined", "netLiquidationValue", "amount")
        or _dig(account, "financials", "current", "netLiquidationValue", "amount")
    )


def _redact_account(account: dict) -> dict:
    clean = dict(account)
    if clean.get("number"):
        clean["number"] = f"***{str(clean['number'])[-4:]}"
    return clean


def _parse_datetime(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(UTC)
