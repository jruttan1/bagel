"""Deterministic market data and portfolio-mover ranking."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.models import PortfolioSnapshot
from app.schemas import MarketSignal

MARKET_ZONE = ZoneInfo("America/New_York")
MATERIAL_REGULAR_MOVE = 1.5
MATERIAL_EXTENDED_MOVE = 2.0
MAX_RESEARCH_CANDIDATES = 5


class MarketDataClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client

    async def quotes(self, tickers: list[str]) -> dict[str, dict]:
        rows = await self._get("/stable/batch-quote", {"symbols": _symbols(tickers)})
        return _by_symbol(rows)

    async def extended_trades(self, tickers: list[str]) -> dict[str, dict]:
        rows = await self._get("/stable/batch-aftermarket-trade", {"symbols": _symbols(tickers)})
        return _by_symbol(rows)

    async def earnings_calendar(self, start: date, end: date) -> list[dict]:
        rows = await self._get(
            "/stable/earnings-calendar",
            {"from": start.isoformat(), "to": end.isoformat()},
        )
        return rows if isinstance(rows, list) else []

    async def signals(
        self,
        snapshot: PortfolioSnapshot,
        *,
        now: datetime | None = None,
    ) -> list[MarketSignal]:
        tickers = sorted({holding.ticker.upper() for holding in snapshot.holdings if holding.ticker})
        quotes = await self.quotes(tickers)
        session = market_session(now)
        extended = await self.extended_trades(tickers) if session != "regular" else {}
        return build_signals(snapshot, quotes, extended, now=now)

    async def _get(self, path: str, params: dict[str, str]) -> Any:
        if not self.settings.fmp_api_key or not params.get("symbols", "ok"):
            return []
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            base_url="https://financialmodelingprep.com", timeout=20
        )
        try:
            response = await client.get(path, params={**params, "apikey": self.settings.fmp_api_key})
            response.raise_for_status()
            return response.json()
        finally:
            if own_client:
                await client.aclose()


def build_signals(
    snapshot: PortfolioSnapshot,
    quotes: dict[str, dict],
    extended: dict[str, dict],
    *,
    now: datetime | None = None,
) -> list[MarketSignal]:
    observed = _utc(now or datetime.now(UTC))
    session = market_session(observed)
    holdings = _aggregate_holdings(snapshot)
    signals = []
    for ticker, holding in holdings.items():
        quote = quotes.get(ticker, {})
        after = extended.get(ticker, {})
        regular_price = _number(quote.get("price"))
        previous_close = _number(quote.get("previousClose"))
        regular_change = _number(quote.get("changePercentage"))
        if regular_change is None:
            regular_change = _percent_change(regular_price, previous_close)

        extended_price = _number(after.get("price"))
        extended_change = _percent_change(extended_price, regular_price)
        timestamp = _timestamp(after) if extended_price is not None else _timestamp(quote)
        timestamp = timestamp or observed
        fresh = observed - timestamp <= _freshness_window(session)
        active_move = (
            extended_change
            if extended_change is not None and session != "regular"
            else regular_change
        )
        weight = holding["weight"]
        magnitude = abs(active_move or 0)
        effect = weight * (active_move or 0)
        signals.append(
            MarketSignal(
                ticker=ticker,
                name=holding["name"],
                portfolio_weight=weight,
                regular_price=regular_price,
                regular_change_percent=regular_change,
                extended_price=extended_price,
                extended_change_percent=extended_change,
                estimated_portfolio_effect=effect,
                session=session,
                observed_at=timestamp,
                is_fresh=fresh,
                rank_score=magnitude + min(weight * 0.1, 0.25),
            )
        )
    return sorted(signals, key=lambda signal: signal.rank_score, reverse=True)


def research_candidates(
    signals: list[MarketSignal], limit: int = MAX_RESEARCH_CANDIDATES
) -> list[MarketSignal]:
    material = [
        signal
        for signal in signals
        if signal.is_fresh
        and (
            abs(signal.extended_change_percent or 0) >= MATERIAL_EXTENDED_MOVE
            or abs(signal.regular_change_percent or 0) >= MATERIAL_REGULAR_MOVE
        )
    ]
    return material[:limit]


def market_session(now: datetime | None = None) -> str:
    local = _utc(now or datetime.now(UTC)).astimezone(MARKET_ZONE)
    if local.weekday() >= 5:
        return "closed"
    value = local.time()
    if time(4) <= value < time(9, 30):
        return "premarket"
    if time(9, 30) <= value < time(16):
        return "regular"
    if time(16) <= value < time(20):
        return "afterhours"
    return "closed"


def _aggregate_holdings(snapshot: PortfolioSnapshot) -> dict[str, dict]:
    values: dict[str, dict] = defaultdict(lambda: {"weight": 0.0, "name": None})
    for holding in snapshot.holdings:
        ticker = holding.ticker.upper()
        values[ticker]["weight"] += float(holding.portfolio_weight)
        values[ticker]["name"] = values[ticker]["name"] or holding.name
    return dict(values)


def _by_symbol(value: Any) -> dict[str, dict]:
    if not isinstance(value, list):
        return {}
    return {
        str(row.get("symbol") or row.get("ticker") or "").upper(): row
        for row in value
        if isinstance(row, dict) and (row.get("symbol") or row.get("ticker"))
    }


def _symbols(tickers: list[str]) -> str:
    return ",".join(sorted({ticker.upper() for ticker in tickers if ticker}))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _percent_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline in {None, 0}:
        return None
    return (current - baseline) / baseline * 100


def _timestamp(row: dict) -> datetime | None:
    value = row.get("timestamp") or row.get("lastUpdated") or row.get("date")
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=UTC)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return _utc(parsed)
    except (TypeError, ValueError, OSError):
        return None


def _freshness_window(session: str) -> timedelta:
    return timedelta(hours=20 if session in {"premarket", "closed"} else 2)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
