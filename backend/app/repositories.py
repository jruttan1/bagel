from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ConversationMessage, PortfolioSnapshot, User


async def get_user_by_phone(session: AsyncSession, phone_number: str) -> User | None:
    result = await session.execute(select(User).where(User.phone_number == phone_number).options(selectinload(User.wealthsimple_connection)))
    return result.scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id).options(selectinload(User.wealthsimple_connection), selectinload(User.theses)))
    return result.scalar_one_or_none()


async def latest_snapshot(session: AsyncSession, user_id: UUID) -> PortfolioSnapshot | None:
    result = await session.execute(select(PortfolioSnapshot).where(PortfolioSnapshot.user_id == user_id).options(selectinload(PortfolioSnapshot.holdings)).order_by(PortfolioSnapshot.captured_at.desc()).limit(1))
    return result.scalar_one_or_none()


async def previous_snapshot(session: AsyncSession, user_id: UUID, before: datetime) -> PortfolioSnapshot | None:
    result = await session.execute(select(PortfolioSnapshot).where(PortfolioSnapshot.user_id == user_id, PortfolioSnapshot.captured_at < before).options(selectinload(PortfolioSnapshot.holdings)).order_by(PortfolioSnapshot.captured_at.desc()).limit(1))
    return result.scalar_one_or_none()


async def recent_messages(session: AsyncSession, user_id: UUID, limit: int = 12) -> list[ConversationMessage]:
    result = await session.execute(select(ConversationMessage).where(ConversationMessage.user_id == user_id).order_by(ConversationMessage.created_at.desc()).limit(limit))
    return list(reversed(result.scalars().all()))

