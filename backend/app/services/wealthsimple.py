import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BrokerageAccount,
    ConnectionStatus,
    Holding,
    PortfolioSnapshot,
    Transaction,
    WealthsimpleConnection,
)
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


class WealthsimpleService:
    def __init__(self, secret_box: SecretBox):
        self.secret_box = secret_box

    async def connect(
        self,
        session: AsyncSession,
        user_id: UUID,
        username: str,
        password: str,
        otp: str | None = None,
    ) -> WealthsimpleConnection:
        session_json = await asyncio.to_thread(self._login_sync, username, password, otp)
        connection = await session.scalar(
            select(WealthsimpleConnection).where(WealthsimpleConnection.user_id == user_id)
        )
        if connection is None:
            connection = WealthsimpleConnection(
                user_id=user_id,
                encrypted_session=self.secret_box.encrypt_text(session_json),
                encrypted_username=self.secret_box.encrypt_text(username),
                status=ConnectionStatus.connected,
            )
            session.add(connection)
        else:
            connection.encrypted_session = self.secret_box.encrypt_text(session_json)
            connection.encrypted_username = self.secret_box.encrypt_text(username)
            connection.status = ConnectionStatus.connected
            connection.last_error = None
        await session.commit()
        return connection

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

    async def sync_user(self, session: AsyncSession, user_id: UUID) -> SyncResult:
        connection = await session.scalar(
            select(WealthsimpleConnection).where(WealthsimpleConnection.user_id == user_id)
        )
        if connection is None or connection.status != ConnectionStatus.connected:
            raise WealthsimpleIntegrationError("Wealthsimple is not connected")

        username = self.secret_box.decrypt_text(connection.encrypted_username or "")
        session_json = self.secret_box.decrypt_text(connection.encrypted_session)
        try:
            raw = await asyncio.to_thread(self._fetch_sync, session_json, username)
            result = await self._persist_sync(session, user_id, connection, raw)
            return result
        except Exception as exc:
            connection.status = (
                ConnectionStatus.reauth_required if _looks_like_auth_error(exc) else ConnectionStatus.error
            )
            connection.last_error = _safe_error(exc)
            await session.commit()
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
        return RawWealthsimpleData(refreshed["session"], accounts, positions, market_data, activities)

    async def _persist_sync(
        self,
        session: AsyncSession,
        user_id: UUID,
        connection: WealthsimpleConnection,
        raw: RawWealthsimpleData,
    ) -> SyncResult:
        accounts_by_id = {account.get("id"): account for account in raw.accounts if account.get("id")}
        for account in raw.accounts:
            provider_id = account.get("id")
            if not provider_id:
                continue
            db_account = await session.scalar(
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
            if db_account is None:
                session.add(BrokerageAccount(user_id=user_id, provider_account_id=provider_id, **values))
            else:
                for key, value in values.items():
                    setattr(db_account, key, value)

        normalized = [_normalize_position(row, raw.market_data) for row in raw.positions]
        normalized = [row for row in normalized if row is not None and row["current_value"] != 0]
        holdings_total = sum((row["current_value"] for row in normalized), Decimal("0"))
        account_total = sum((_account_value(account) for account in raw.accounts), Decimal("0"))
        cash = max(Decimal("0"), account_total - holdings_total)
        total_value = account_total if account_total > 0 else holdings_total
        for row in normalized:
            row["portfolio_weight"] = row["current_value"] / total_value if total_value else Decimal("0")

        source_payload = [
            {"ticker": row["ticker"], "shares": str(row["shares"]), "value": str(row["current_value"])}
            for row in sorted(normalized, key=lambda item: (item["ticker"], item.get("account_id") or ""))
        ]
        source_hash = hashlib.sha256(json.dumps(source_payload, sort_keys=True).encode()).hexdigest()
        allocation = {
            row["ticker"]: float(row["portfolio_weight"])
            for row in sorted(normalized, key=lambda item: item["current_value"], reverse=True)
        }
        snapshot = PortfolioSnapshot(
            user_id=user_id,
            total_value=total_value,
            cash=cash,
            currency="CAD",
            allocation=allocation,
            source_hash=source_hash,
        )
        session.add(snapshot)
        await session.flush()
        for row in normalized:
            session.add(Holding(snapshot_id=snapshot.id, user_id=user_id, **row))

        transaction_count = 0
        for account_id, activity in raw.activities:
            provider_id = str(activity.get("canonicalId") or activity.get("id") or "")
            if not provider_id:
                continue
            existing = await session.scalar(
                select(Transaction.id).where(
                    Transaction.user_id == user_id,
                    Transaction.provider_transaction_id == provider_id,
                )
            )
            if existing:
                continue
            occurred_at = _parse_datetime(activity.get("occurredAt"))
            session.add(
                Transaction(
                    user_id=user_id,
                    provider_transaction_id=provider_id,
                    account_id=account_id,
                    transaction_type=str(activity.get("type") or "unknown"),
                    occurred_at=occurred_at,
                    amount=_decimal(activity.get("amount")),
                    currency=str(activity.get("currency") or "CAD"),
                    description=activity.get("description"),
                    raw_data=activity,
                )
            )
            transaction_count += 1

        connection.encrypted_session = self.secret_box.encrypt_text(raw.session_json)
        connection.status = ConnectionStatus.connected
        connection.last_successful_sync = datetime.now(UTC)
        connection.last_error = None
        await session.commit()
        return SyncResult(
            snapshot_id=snapshot.id,
            account_count=len(accounts_by_id),
            holding_count=len(normalized),
            transaction_count=transaction_count,
            total_value=float(total_value),
            cash=float(cash),
            captured_at=snapshot.captured_at,
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


def _looks_like_auth_error(exc: Exception) -> bool:
    value = str(exc).lower()
    return any(word in value for word in ("auth", "token", "unauthorized", "forbidden", "expired"))


def _safe_error(exc: Exception) -> str:
    # Do not persist provider responses that may include credentials.
    return f"{type(exc).__name__}: synchronization failed"
