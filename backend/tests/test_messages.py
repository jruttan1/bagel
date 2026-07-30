import json

import httpx
import pytest

from app import messages
from app.config import Settings
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


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("send my morning bagel at 9:45", "09:45"),
        ("text me the brief at 8 pm", "20:00"),
        ("morning message around 12 a.m.", "00:00"),
        ("MSFT is at 500", None),
    ],
)
def test_parses_brief_time_only_from_setting_requests(message: str, expected: str | None) -> None:
    assert messages.parse_brief_time(message) == expected
