from decimal import Decimal

from app.services.wealthsimple import _normalize_position


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
