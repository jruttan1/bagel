from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from app import briefs, crud
from app.config import Settings
from app.models import OnboardingStep, PortfolioSnapshot, User
from app.schemas import MessageDraft


def test_due_window_uses_user_timezone(monkeypatch) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 7, 28, 11, 35, tzinfo=UTC)
            return value if tz else value.replace(tzinfo=None)

    monkeypatch.setattr("app.briefs.datetime", FixedDateTime)
    assert briefs._is_due("America/Toronto", "07:30")
    assert not briefs._is_due("America/Toronto", "08:00")


@pytest.mark.asyncio
async def test_cron_brief_runs_agent(session) -> None:
    user = User(
        id=uuid4(),
        phone_number="+14165550123",
        onboarding_step=OnboardingStep.complete,
    )
    snapshot = PortfolioSnapshot(
        id=uuid4(),
        user_id=user.id,
        total_value=Decimal("1000"),
        cash=Decimal("0"),
        currency="CAD",
        source_hash="cron-test",
    )
    session.add_all([user, snapshot])
    await session.commit()

    class FakeWealthsimple:
        async def sync_user(self, user_id):
            return None

    class FakeAgent:
        called_with = None

        async def morning_brief(self, user_id):
            self.called_with = user_id
            return MessageDraft(text="Nothing meaningful changed this morning.")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "brief-1", "status": "sent"})

    agent = FakeAgent()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8787"
    ) as http:
        sent = await briefs.send_for_user(
            Settings(_env_file=None), FakeWealthsimple(), agent, user.id, http=http
        )

    assert sent
    assert agent.called_with == user.id
    assert await crud.brief_exists(user.id, datetime.now(UTC).date())
