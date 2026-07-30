from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.config import Settings
from app.market import MarketDataClient, MarketDataUnavailable, build_signals, research_candidates
from app.models import Holding, PortfolioSnapshot


def holding(ticker: str, weight: str) -> Holding:
    return Holding(
        ticker=ticker,
        shares=Decimal("1"),
        current_value=Decimal("100"),
        portfolio_weight=Decimal(weight),
        currency="USD",
        user_id=uuid4(),
    )


def snapshot(*holdings: Holding) -> PortfolioSnapshot:
    row = PortfolioSnapshot(
        user_id=uuid4(),
        total_value=Decimal("10000"),
        cash=Decimal("0"),
        currency="USD",
        source_hash="test",
    )
    row.holdings = list(holdings)
    return row


def test_small_mover_outranks_large_unchanged_holding() -> None:
    now = datetime(2026, 7, 30, 14, tzinfo=UTC)
    rows = build_signals(
        snapshot(holding("BIG", "0.30"), holding("MOVE", "0.02")),
        {
            "BIG": {"price": 100, "previousClose": 100, "timestamp": now.timestamp()},
            "MOVE": {"price": 105, "previousClose": 100, "timestamp": now.timestamp()},
        },
        {},
        now=now,
    )
    assert rows[0].ticker == "MOVE"
    assert [row.ticker for row in research_candidates(rows)] == ["MOVE"]


def test_afterhours_move_uses_regular_close_as_baseline() -> None:
    now = datetime(2026, 7, 30, 21, tzinfo=UTC)
    rows = build_signals(
        snapshot(holding("MOVE", "0.02")),
        {"MOVE": {"price": 100, "previousClose": 96, "timestamp": now.timestamp()}},
        {"MOVE": {"price": 105, "timestamp": now.timestamp()}},
        now=now,
    )
    assert rows[0].session == "afterhours"
    assert rows[0].regular_change_percent == 4.166666666666666
    assert rows[0].extended_change_percent == 5


def test_duplicate_tickers_are_aggregated() -> None:
    now = datetime(2026, 7, 30, 14, tzinfo=UTC)
    rows = build_signals(
        snapshot(holding("MSFT", "0.10"), holding("msft", "0.15")),
        {"MSFT": {"price": 102, "previousClose": 100, "timestamp": now.timestamp()}},
        {},
        now=now,
    )
    assert len(rows) == 1
    assert rows[0].portfolio_weight == 0.25


@pytest.mark.asyncio
async def test_signals_fail_closed_without_market_data() -> None:
    market = MarketDataClient(Settings(_env_file=None, fmp_api_key=""))
    with pytest.raises(MarketDataUnavailable):
        await market.signals(snapshot(holding("MSFT", "0.25")))
