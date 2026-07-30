"""Morning portfolio briefs and their schedule-facing functions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app import crud, messages
from app.config import Settings
from app.intelligence import IntelligenceService, IntelligenceUnavailable
from app.market import MarketDataClient
from app.wealthsimple import WealthsimpleIntegrationError, WealthsimpleService


async def send_for_user(
    settings: Settings,
    wealthsimple: WealthsimpleService,
    intelligence: IntelligenceService,
    market: MarketDataClient,
    user_id: UUID,
    *,
    http: httpx.AsyncClient | None = None,
) -> bool:
    user = await crud.user_by_id(user_id)
    if user is None or not user.is_active or user.onboarding_step.value != "complete":
        return False
    local_date = _local_now(user.timezone).date()
    if await crud.brief_exists(user.id, local_date):
        return False
    try:
        await wealthsimple.sync_user(user.id)
    except WealthsimpleIntegrationError:
        return False
    snapshot = await crud.latest_snapshot(user.id)
    if snapshot is None:
        return False
    prior = await crud.previous_snapshot(user.id, snapshot.captured_at)
    earnings = await earnings_for_snapshot(market, snapshot)
    try:
        draft = await intelligence.morning_brief(user, snapshot, prior, list(user.theses), earnings)
    except IntelligenceUnavailable:
        return False
    result = await messages.send(settings, user.phone_number, draft, client=http)
    await crud.record_brief(
        user.id,
        local_date,
        snapshot.id,
        draft.text,
        result.id,
        evidence=draft._evidence,
    )
    return True


async def run_due(
    settings: Settings,
    wealthsimple: WealthsimpleService,
    intelligence: IntelligenceService,
    market: MarketDataClient,
    *,
    http: httpx.AsyncClient | None = None,
) -> int:
    sent = 0
    for user in await crud.active_users():
        if not user.notification_settings.get("morning_brief", True):
            continue
        brief_time = str(user.notification_settings.get("brief_time") or "07:30")
        if _is_due(user.timezone, brief_time) and await send_for_user(
            settings, wealthsimple, intelligence, market, user.id, http=http
        ):
            sent += 1
    return sent


async def refresh_earnings_calendar(market: MarketDataClient) -> int:
    today = datetime.now(UTC).date()
    held = await crud.held_tickers()
    rows = await market.earnings_calendar(today, today + timedelta(days=14))
    events = []
    for row in rows:
        ticker = str(row.get("symbol") or "").upper()
        event_date = row.get("date")
        if ticker not in held or not event_date:
            continue
        events.append(
            {
                "ticker": ticker,
                "event_type": "earnings",
                "occurred_at": datetime.fromisoformat(str(event_date)).replace(tzinfo=UTC),
                "headline": f"{ticker} earnings",
                "raw_data": row,
            }
        )
    return await crud.add_events(events)


async def earnings_for_snapshot(market: MarketDataClient, snapshot) -> list[dict]:
    tickers = {holding.ticker for holding in snapshot.holdings}
    if not tickers:
        return []
    start = datetime.now(UTC).date()
    rows = await market.earnings_calendar(start, start + timedelta(days=7))
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
