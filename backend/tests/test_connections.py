from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.models import User
from app.services.connections import ConnectionLinkService, InvalidConnectionToken


@pytest.mark.asyncio
async def test_connection_tokens_are_single_use_and_expire(session) -> None:
    settings = Settings(_env_file=None, app_base_url="https://bagel.test")
    links = ConnectionLinkService(settings)
    user = User(phone_number="+14165550123")
    session.add(user)
    await session.commit()

    token = await links.issue(session, user)
    row, resolved_user = await links.resolve(session, token)
    assert resolved_user.id == user.id
    assert token not in row.token_hash
    assert links.url(token).startswith("https://bagel.test/connect?token=")

    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    with pytest.raises(InvalidConnectionToken):
        await links.resolve(session, token)
