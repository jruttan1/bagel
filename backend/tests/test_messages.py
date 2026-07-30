import httpx
import pytest

from app.config import Settings
from app.services.messages import MessagingError, SpectrumBridgeClient


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
        result = await SpectrumBridgeClient(settings, http).send_message("+14165550123", "hello")
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
        with pytest.raises(MessagingError) as error:
            await SpectrumBridgeClient(settings, http).send_message("+14165550123", "hello")
    assert error.value.status_code == 403
    assert error.value.code == "contact_first"
