from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Holding, MarketEvent, MorningBrief, User
from app.repositories import get_user, latest_snapshot, previous_snapshot
from app.services.intelligence import IntelligenceService, IntelligenceUnavailable
from app.services.market_data import MarketDataClient
from app.services.messages import MessagesDevClient
from app.services.wealthsimple import WealthsimpleIntegrationError, WealthsimpleService


class BriefService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        wealthsimple: WealthsimpleService,
        intelligence: IntelligenceService,
        market_data: MarketDataClient,
        messages: MessagesDevClient,
    ):
        self.session_factory = session_factory
        self.wealthsimple = wealthsimple
        self.intelligence = intelligence
        self.market_data = market_data
        self.messages = messages

    async def send_for_user(self, user_id) -> bool:
        async with self.session_factory() as session:
            user = await get_user(session, user_id)
            if user is None or not user.is_active or user.onboarding_step.value != "complete":
                return False
            local_date = _local_now(user.timezone).date()
            exists = await session.scalar(
                select(MorningBrief.id).where(
                    MorningBrief.user_id == user.id, MorningBrief.brief_date == local_date
                )
            )
            if exists:
                return False
            try:
                await self.wealthsimple.sync_user(session, user.id)
            except WealthsimpleIntegrationError:
                return False
            snapshot = await latest_snapshot(session, user.id)
            if snapshot is None:
                return False
            prior = await previous_snapshot(session, user.id, snapshot.captured_at)
            earnings = await self._earnings_for_snapshot(snapshot)
            try:
                content = await self.intelligence.morning_brief(
                    user, snapshot, prior, list(user.theses), earnings
                )
            except IntelligenceUnavailable:
                return False
            result = await self.messages.send_message(user.phone_number, content)
            session.add(
                MorningBrief(
                    user_id=user.id,
                    brief_date=local_date,
                    snapshot_id=snapshot.id,
                    content=content,
                    provider_outbox_id=result.id,
                )
            )
            await session.commit()
            return True

    async def run_due(self) -> int:
        async with self.session_factory() as session:
            users = (await session.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
        sent = 0
        for user in users:
            if not user.notification_settings.get("morning_brief", True):
                continue
            brief_time = str(user.notification_settings.get("brief_time") or "07:30")
            if _is_due(user.timezone, brief_time) and await self.send_for_user(user.id):
                sent += 1
        return sent

    async def refresh_earnings_calendar(self) -> int:
        today = datetime.now(UTC).date()
        rows = await self.market_data.earnings_calendar(today, today + timedelta(days=14))
        async with self.session_factory() as session:
            held = set((await session.execute(select(Holding.ticker).distinct())).scalars())
            inserted = 0
            for row in rows:
                ticker = str(row.get("symbol") or "").upper()
                event_date = row.get("date")
                if ticker not in held or not event_date:
                    continue
                occurred_at = datetime.fromisoformat(str(event_date)).replace(tzinfo=UTC)
                exists = await session.scalar(
                    select(MarketEvent.id).where(
                        MarketEvent.ticker == ticker,
                        MarketEvent.event_type == "earnings",
                        MarketEvent.occurred_at == occurred_at,
                    )
                )
                if exists:
                    continue
                session.add(
                    MarketEvent(
                        ticker=ticker,
                        event_type="earnings",
                        occurred_at=occurred_at,
                        headline=f"{ticker} earnings",
                        raw_data=row,
                    )
                )
                inserted += 1
            await session.commit()
            return inserted

    async def _earnings_for_snapshot(self, snapshot) -> list[dict]:
        tickers = {holding.ticker for holding in snapshot.holdings}
        if not tickers:
            return []
        start = datetime.now(UTC).date()
        rows = await self.market_data.earnings_calendar(start, start + timedelta(days=7))
        return [row for row in rows if str(row.get("symbol") or "").upper() in tickers]


def _local_now(timezone_name: str) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    return datetime.now(UTC).astimezone(zone)


def _is_due(timezone_name: str, brief_time: str) -> bool:
    try:
        hour, minute = (int(part) for part in brief_time.split(":", 1))
    except (ValueError, TypeError):
        hour, minute = 7, 30
    local = _local_now(timezone_name)
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return scheduled <= local < scheduled + timedelta(minutes=15)
