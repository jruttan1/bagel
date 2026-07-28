import httpx
import pytest

from app.config import Settings
from app.services.messages import MessagesDevClient, MessagesDevError


@pytest.mark.asyncio
async def test_sends_message_with_configured_line() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer api-key"
        assert b'"from":"+14165550000"' in request.content
        return httpx.Response(200, json={"id": "msg_1", "status": "queued"})

    settings = Settings(
        _env_file=None,
        messages_api_key="api-key",
        messages_line_handle="+14165550000",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.messages.dev"
    ) as http:
        result = await MessagesDevClient(settings, http).send_message("+14165550123", "hello")
    assert result.id == "msg_1"


@pytest.mark.asyncio
async def test_surfaces_provider_error_code() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"message": "Contact must message first", "code": "contact_first"}},
        )

    settings = Settings(_env_file=None, messages_api_key="api-key")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.messages.dev"
    ) as http:
        with pytest.raises(MessagesDevError) as error:
            await MessagesDevClient(settings, http).send_message("+14165550123", "hello")
    assert error.value.status_code == 403
    assert error.value.code == "contact_first"
