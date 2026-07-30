import json

import httpx
import pytest

from app import crud, messages
from app.config import Settings
from app.models import OnboardingStep, User
from app.schemas import MessageDraft


@pytest.mark.asyncio
async def test_sends_message_with_configured_line() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer bridge-token"
        assert b'"to":"+14165550123"' in request.content
        return httpx.Response(200, json={"id": "msg_1", "status": "queued"})

    settings = Settings(
        _env_file=None,
        spectrum_bridge_token="bridge-token",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8787"
    ) as http:
        result = await messages.send(settings, "+14165550123", "hello", client=http)
    assert result.id == "msg_1"


@pytest.mark.asyncio
async def test_surfaces_provider_error_code() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"message": "Contact must message first", "code": "contact_first"}},
        )

    settings = Settings(_env_file=None, spectrum_bridge_token="bridge-token")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8787"
    ) as http:
        with pytest.raises(messages.MessagingError) as error:
            await messages.send(settings, "+14165550123", "hello", client=http)
    assert error.value.status_code == 403
    assert error.value.code == "contact_first"


@pytest.mark.asyncio
async def test_sends_native_emphasis_without_storing_markdown() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["format"] == "markdown"
        assert payload["text"].startswith("**Oracle is the story today**")
        return httpx.Response(200, json={"id": "msg_2", "status": "sent"})

    settings = Settings(_env_file=None)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8787"
    ) as http:
        await messages.send(
            settings,
            "+14165550123",
            MessageDraft(
                text="Oracle is the story today\nThe move matters.",
                emphasis_phrase="Oracle is the story today",
            ),
            client=http,
        )


@pytest.mark.asyncio
async def test_completed_user_inbound_runs_agent(session) -> None:
    user = User(phone_number="+14165550123", onboarding_step=OnboardingStep.complete)
    session.add(user)
    await session.commit()

    class FakeAgent:
        called_with = None

        async def reply(self, user_id, text):
            self.called_with = (user_id, text)
            return MessageDraft(text="The answer from the agent.")

    class FakeOnboarding:
        pass

    sent = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/messages":
            sent.append(json.loads(request.content)["text"])
            return httpx.Response(200, json={"id": "msg-agent", "status": "sent"})
        return httpx.Response(200, json={"status": "ok"})

    agent = FakeAgent()
    settings = Settings(_env_file=None)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8787"
    ) as http:
        await messages.handle_inbound(
            settings,
            agent,
            FakeOnboarding(),
            {"from": user.phone_number, "text": "What changed today?", "id": "in-1"},
            client=http,
        )

    saved = await crud.user_by_id(user.id)
    assert agent.called_with == (user.id, "What changed today?")
    assert sent == ["The answer from the agent."]
    assert saved is not None
