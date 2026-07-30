import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app import crud
from app.models import ConnectionStatus
from app.schemas import SyncResult
from app.security import SecretBox


class WealthsimpleIntegrationError(RuntimeError):
    pass


class WealthsimpleOTPRequired(WealthsimpleIntegrationError):
    pass


@dataclass
class RawWealthsimpleData:
    session_json: str
    accounts: list[dict]
    positions: list[dict]
    market_data: dict[str, dict]
    activities: list[tuple[str, dict]]
    historical: list[dict]


class WealthsimpleService:
    def __init__(self, secret_box: SecretBox):
        self.secret_box = secret_box

    async def connect(
        self,
        user_id: UUID,
        username: str,
        password: str,
        otp: str | None = None,
    ) -> None:
        session_json = await asyncio.to_thread(self._login_sync, username, password, otp)
        await crud.save_connection(
            user_id,
            self.secret_box.encrypt_text(session_json),
            self.secret_box.encrypt_text(username),
        )

    @staticmethod
    def _login_sync(username: str, password: str, otp: str | None) -> str:
        from ws_api import LoginFailedException, OTPRequiredException, WealthsimpleAPI

        captured: dict[str, str] = {}

        def persist_session(value: str, _username: str | None = None) -> None:
            captured["session"] = value

        try:
            session_obj = WealthsimpleAPI.login(
                username,
                password,
                otp_answer=otp,
                persist_session_fct=persist_session,
            )
        except OTPRequiredException as exc:
            raise WealthsimpleOTPRequired("A Wealthsimple verification code is required") from exc
        except LoginFailedException as exc:
            raise WealthsimpleIntegrationError("Wealthsimple authentication failed") from exc
        if captured.get("session"):
            return captured["session"]
        if hasattr(session_obj, "to_json"):
            return session_obj.to_json()
        raise WealthsimpleIntegrationError("Wealthsimple did not return a reusable session")

    async def sync_user(self, user_id: UUID) -> SyncResult:
        connection = await crud.connection(user_id)
        if connection is None or connection.status != ConnectionStatus.connected:
            raise WealthsimpleIntegrationError("Wealthsimple is not connected")

        username = self.secret_box.decrypt_text(connection.encrypted_username or "")
        session_json = self.secret_box.decrypt_text(connection.encrypted_session)
        try:
            raw = await asyncio.to_thread(self._fetch_sync, session_json, username)
            positions = [_normalize_position(row, raw.market_data) for row in raw.positions]
            positions = [row for row in positions if row is not None and row["current_value"] != 0]
            return await crud.save_sync(
                user_id,
                self.secret_box.encrypt_text(raw.session_json),
                raw.accounts,
                positions,
                raw.activities,
                raw.historical,
            )
        except Exception as exc:
            await crud.mark_connection_error(user_id, _looks_like_auth_error(exc))
            if isinstance(exc, WealthsimpleIntegrationError):
                raise
            raise WealthsimpleIntegrationError("Wealthsimple synchronization failed") from exc

    @staticmethod
    def _fetch_sync(session_json: str, username: str) -> RawWealthsimpleData:
        from ws_api import WealthsimpleAPI, WSAPISession

        refreshed: dict[str, str] = {"session": session_json}

        def persist_session(value: str, _username: str | None = None) -> None:
            refreshed["session"] = value

        ws = WealthsimpleAPI.from_token(
            WSAPISession.from_json(session_json), persist_session_fct=persist_session, username=username
        )
        accounts = ws.get_accounts()
        positions = ws.get_identity_positions(None, "CAD")
        security_ids = {
            _dig(position, "node", "security", "id") or _dig(position, "security", "id")
            for position in positions
        }
        market_data = {
            security_id: ws.get_security_market_data(security_id)
            for security_id in security_ids
            if security_id
        }
        activities: list[tuple[str, dict]] = []
        for account in accounts:
            account_id = account.get("id")
            if account_id and "credit-card" not in account_id:
                for activity in ws.get_activities(account_id) or []:
                    activities.append((account_id, activity))
        historical = ws.get_identity_historical_financials(currency="CAD", first=365) or []
        return RawWealthsimpleData(
            refreshed["session"], accounts, positions, market_data, activities, historical
        )


def _normalize_position(row: dict, market_data: dict[str, dict]) -> dict | None:
    node = row.get("node", row)
    security_id = _dig(node, "security", "id")
    if not security_id:
        return None
    security = market_data.get(security_id, {})
    stock = security.get("stock") or {}
    ticker = stock.get("symbol") or security_id
    quantity = _decimal(node.get("quantity"))
    current_value = _decimal(_dig(node, "totalValue", "amount"))
    average_cost = _decimal_or_none(_dig(node, "averagePrice", "amount"))
    price = current_value / quantity if quantity else None
    accounts = node.get("accounts") or []
    account_id = accounts[0].get("id") if accounts else None
    return {
        "account_id": account_id,
        "security_id": security_id,
        "ticker": str(ticker).upper(),
        "name": stock.get("name") or stock.get("description"),
        "shares": quantity,
        "average_cost": average_cost,
        "current_price": price,
        "current_value": current_value,
        "currency": _dig(node, "totalValue", "currency") or "CAD",
        "portfolio_weight": Decimal("0"),
        "sector": None,
        "industry": None,
        "provider_data": {"position": node, "security": security},
    }


def _account_value(account: dict) -> Decimal:
    return _decimal(
        _dig(account, "financials", "currentCombined", "netLiquidationValue", "amount")
        or _dig(account, "financials", "current", "netLiquidationValue", "amount")
    )


def _redact_account(account: dict) -> dict:
    clean = dict(account)
    number = clean.get("number")
    if number:
        clean["number"] = f"***{str(number)[-4:]}"
    return clean


def _dig(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value)


def _parse_datetime(value: Any) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


def _parse_date(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _as_date(value: datetime):
    return value.date()


def _looks_like_auth_error(exc: Exception) -> bool:
    value = str(exc).lower()
    return any(word in value for word in ("auth", "token", "unauthorized", "forbidden", "expired"))


def _safe_error(exc: Exception) -> str:
    # Do not persist provider responses that may include credentials.
    return f"{type(exc).__name__}: synchronization failed"
