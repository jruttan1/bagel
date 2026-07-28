import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import ConnectionToken, User


class InvalidConnectionToken(ValueError):
    pass


class ConnectionLinkService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def issue(self, session: AsyncSession, user: User) -> str:
        await session.execute(
            update(ConnectionToken)
            .where(ConnectionToken.user_id == user.id, ConnectionToken.used_at.is_(None))
            .values(used_at=datetime.now(UTC))
        )
        token = secrets.token_urlsafe(32)
        session.add(
            ConnectionToken(
                user_id=user.id,
                token_hash=_token_hash(token),
                expires_at=datetime.now(UTC) + timedelta(minutes=self.settings.connection_link_ttl_minutes),
            )
        )
        await session.commit()
        return token

    async def resolve(self, session: AsyncSession, token: str) -> tuple[ConnectionToken, User]:
        connection_token = await session.scalar(
            select(ConnectionToken).where(ConnectionToken.token_hash == _token_hash(token))
        )
        now = datetime.now(UTC)
        if (
            connection_token is None
            or connection_token.used_at is not None
            or _as_utc(connection_token.expires_at) <= now
        ):
            raise InvalidConnectionToken("This connection link is invalid or has expired")
        user = await session.get(User, connection_token.user_id)
        if user is None or not user.is_active:
            raise InvalidConnectionToken("This connection link is no longer active")
        return connection_token, user

    def url(self, token: str) -> str:
        return f"{self.settings.app_base_url.rstrip('/')}/connect?token={token}"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
