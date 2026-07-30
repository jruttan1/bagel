from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.crud import _historical
from app.models import PortfolioSnapshot, User
from app.wealthsimple import _normalize_position


def test_normalizes_ws_api_position() -> None:
    position = {
        "node": {
            "security": {"id": "security-1"},
            "quantity": "4",
            "totalValue": {"amount": "500", "currency": "CAD"},
            "averagePrice": {"amount": "90"},
            "accounts": [{"id": "account-1"}],
        }
    }
    market_data = {"security-1": {"stock": {"symbol": "XYZ", "name": "Example"}}}
    result = _normalize_position(position, market_data)
    assert result is not None
    assert result["ticker"] == "XYZ"
    assert result["current_price"] == Decimal("125")
    assert result["account_id"] == "account-1"


@pytest.mark.asyncio
async def test_imports_historical_portfolio_values_once(session) -> None:
    user = User(id=uuid4(), phone_number="+14165550123")
    session.add(user)
    await session.commit()
    history = [
        {
            "node": {
                "date": "2025-01-02",
                "netLiquidationValueV2": {"amount": "12345.67", "currency": "CAD"},
            }
        }
    ]
    await _historical(session, user.id, history)
    await session.commit()
    await _historical(session, user.id, history)
    await session.commit()

    rows = (
        (await session.execute(select(PortfolioSnapshot).where(PortfolioSnapshot.user_id == user.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].total_value == Decimal("12345.6700")
