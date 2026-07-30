from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.config import Settings
from app.schemas import MessageSendResult


class MessagingError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SpectrumBridgeClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            base_url=self.settings.spectrum_bridge_url,
            timeout=20,
        ) as client:
            yield client

    async def _post(self, path: str, payload: dict) -> dict:
        headers = {}
        if self.settings.spectrum_bridge_token:
            headers["Authorization"] = f"Bearer {self.settings.spectrum_bridge_token}"
        async with self._http() as client:
            response = await client.post(path, json=payload, headers=headers)
        if response.is_error:
            data = _safe_json(response)
            error = data.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            code = error.get("code") if isinstance(error, dict) else None
            raise MessagingError(
                str(message or f"Spectrum bridge returned {response.status_code}"),
                response.status_code,
                code,
            )
        return response.json()

    async def send_message(self, to: str, text: str, *, reply_to: str | None = None) -> MessageSendResult:
        payload = {"to": to, "text": text}
        if reply_to:
            payload["replyTo"] = reply_to
        return MessageSendResult.model_validate(await self._post("/messages", payload))

    async def set_typing(self, to: str, enabled: bool) -> None:
        try:
            await self._post("/typing", {"to": to, "enabled": enabled})
        except MessagingError:
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
