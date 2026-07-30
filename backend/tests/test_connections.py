from datetime import UTC, datetime, timedelta

import pytest

from app import crud
from app.models import ConnectionToken, User


@pytest.mark.asyncio
async def test_connection_tokens_are_single_use_and_expire(session) -> None:
    user = User(phone_number="+14165550123")
    session.add(user)
    await session.commit()

    token = await crud.issue_token(user.id, 30)
    row, resolved_user = await crud.resolve_token(token)
    assert resolved_user.id == user.id
    assert token not in row.token_hash

    stored = await session.get(ConnectionToken, row.id)
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    assert await crud.resolve_token(token) is None
