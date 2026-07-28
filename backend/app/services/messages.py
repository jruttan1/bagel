from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.config import Settings
from app.schemas import MessageSendResult


class MessagesDevError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class MessagesDevClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            base_url=self.settings.messages_api_base,
            headers={"Authorization": f"Bearer {self.settings.messages_api_key}"},
            timeout=20,
        ) as client:
            yield client

    async def _post(self, path: str, payload: dict) -> dict:
        if not self.settings.messages_api_key:
            raise MessagesDevError("MESSAGES_API_KEY is not configured")
        async with self._http() as client:
            response = await client.post(path, json=payload)
        if response.is_error:
            data = _safe_json(response)
            error = data.get("error", {})
            raise MessagesDevError(
                error.get("message", f"messages.dev returned {response.status_code}"),
                response.status_code,
                error.get("code"),
            )
        return response.json()

    async def send_message(self, to: str, text: str, *, reply_to: str | None = None) -> MessageSendResult:
        payload = {"from": self.settings.messages_line_handle, "to": to, "text": text}
        if reply_to:
            payload["reply_to"] = reply_to
        return MessageSendResult.model_validate(await self._post("/v1/messages", payload))

    async def set_typing(self, to: str, enabled: bool) -> None:
        try:
            await self._post(
                "/v1/typing",
                {"from": self.settings.messages_line_handle, "to": to, "state": "on" if enabled else "off"},
            )
        except MessagesDevError:
            # Typing is cosmetic and must never suppress the actual response.
            return

    @asynccontextmanager
    async def typing(self, to: str) -> AsyncIterator[None]:
        await self.set_typing(to, True)
        try:
            yield
        finally:
            await self.set_typing(to, False)


def _safe_json(response: httpx.Response) -> dict:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}
