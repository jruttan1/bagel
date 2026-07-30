"""Bagel's complete messaging and conversational interface."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx

from app import crud
from app.config import Settings
from app.models import OnboardingStep, User
from app.phone import InvalidPhoneNumber, normalize_phone
from app.schemas import MessageDraft, MessageSendResult


class Agent(Protocol):
    async def reply(self, user_id, text) -> MessageDraft: ...


class Onboarding(Protocol):
    async def onboarding_question(self, category, snapshot) -> str: ...
    async def distill_profile(self, answers, snapshot) -> dict: ...


class MessagingError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class UnsupportedInboundMessage(ValueError):
    pass


QUESTION_CATEGORIES = {
    OnboardingStep.financial_position: "financial position, obligations, liquidity, and investment horizon",
    OnboardingStep.investing_style: (
        "risk tolerance, decision style, and how actively they want to manage investments"
    ),
    OnboardingStep.portfolio_context: (
        "the reasoning behind the most decision-relevant portfolio concentration"
    ),
}
FALLBACK_QUESTIONS = {
    OnboardingStep.financial_position: "What does this money need to do for you, and roughly when?",
    OnboardingStep.investing_style: "How do you usually decide when to buy, hold, or sell an investment?",
    OnboardingStep.portfolio_context: (
        "Which part of your portfolio reflects your strongest current conviction?"
    ),
}


async def send(
    settings: Settings,
    to: str,
    message: MessageDraft | str,
    *,
    reply_to: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> MessageSendResult:
    draft = _draft(message)
    formatted = _native_message(draft)
    payload: dict[str, Any] = {"to": to, "text": formatted or draft.text}
    if formatted:
        payload["format"] = "markdown"
    if reply_to:
        payload["replyTo"] = reply_to
    return MessageSendResult.model_validate(await _post(settings, "/messages", payload, client))


async def react(
    settings: Settings,
    to: str,
    message_id: str,
    reaction: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    await _post(
        settings,
        "/reactions",
        {"to": to, "messageId": message_id, "reaction": reaction},
        client,
    )


async def set_typing(
    settings: Settings,
    to: str,
    enabled: bool,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    try:
        await _post(settings, "/typing", {"to": to, "enabled": enabled}, client)
    except MessagingError:
        return


@asynccontextmanager
async def typing(
    settings: Settings,
    to: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[None]:
    await set_typing(settings, to, True, client=client)
    try:
        yield
    finally:
        await set_typing(settings, to, False, client=client)


async def send_and_record(
    settings: Settings,
    user: User,
    message: MessageDraft | str,
    *,
    reply_to: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> MessageSendResult:
    draft = _draft(message)
    result = await send(settings, user.phone_number, draft, reply_to=reply_to, client=client)
    await crud.record_outbound(
        user.id,
        draft.text,
        result.id,
        {
            "status": result.status,
            "request_id": result.request_id,
            "emphasis_phrase": draft.emphasis_phrase,
            "evidence": draft._evidence,
        },
    )
    return result


async def welcome(
    settings: Settings,
    user: User,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    token = await crud.issue_token(user.id, settings.connection_link_ttl_minutes)
    url = f"{settings.app_base_url.rstrip('/')}/connect?token={token}"
    text = (
        "Thanks for joining! Connect your Brokerage, then I’ll learn "
        f"what matters to you in a few texts: {url}"
    )
    await send_and_record(settings, user, text, client=client)
    return text


async def handle_inbound(
    settings: Settings,
    agent: Agent,
    onboarding: Onboarding,
    data: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    sender, text, provider_id = _extract(data)
    try:
        phone = normalize_phone(sender)
    except InvalidPhoneNumber as exc:
        raise UnsupportedInboundMessage("Inbound message has an invalid sender") from exc
    user = await crud.record_inbound(phone, text, provider_id, _safe_data(data))
    async with typing(settings, phone, client=client):
        if user.onboarding_step == OnboardingStep.awaiting_connection:
            await welcome(settings, user, client=client)
            return
        if user.onboarding_step != OnboardingStep.complete:
            reply = await handle_answer(onboarding, user, text)
        else:
            try:
                reply = await agent.reply(user.id, text)
            except Exception:
                reply = "I can see your message, but my market analysis is temporarily unavailable."
        await send_and_record(settings, user, reply, reply_to=provider_id, client=client)


async def question_for(onboarding: Onboarding, user: User) -> str:
    snapshot = await crud.latest_snapshot(user.id)
    try:
        question = await onboarding.onboarding_question(QUESTION_CATEGORIES[user.onboarding_step], snapshot)
    except Exception:
        question = FALLBACK_QUESTIONS[user.onboarding_step]
    await crud.save_question(user.id, question)
    return question


async def handle_answer(onboarding: Onboarding, user: User, answer: str) -> str:
    if user.onboarding_step not in QUESTION_CATEGORIES:
        return await question_for(onboarding, await crud.start_onboarding(user.id))
    user = await crud.save_answer(user.id, answer)
    if user.onboarding_step != OnboardingStep.complete:
        return await question_for(onboarding, user)
    answers = await crud.answers(user.id)
    snapshot = await crud.latest_snapshot(user.id)
    try:
        profile = await onboarding.distill_profile(answers, snapshot)
    except Exception:
        profile = {"answers": answers, "summary": "Onboarding completed; profile distillation pending."}
    await crud.save_profile(user.id, profile)
    return (
        "That’s enough for now. I’ll use it quietly in the background and text when "
        "something worth your attention changes."
    )


async def _post(
    settings: Settings,
    path: str,
    payload: dict,
    client: httpx.AsyncClient | None,
) -> dict:
    headers = (
        {"Authorization": f"Bearer {settings.spectrum_bridge_token}"}
        if settings.spectrum_bridge_token
        else {}
    )
    own_client = client is None
    http = client or httpx.AsyncClient(base_url=settings.spectrum_bridge_url, timeout=20)
    try:
        response = await http.post(path, json=payload, headers=headers)
    finally:
        if own_client:
            await http.aclose()
    if response.is_error:
        data = _safe_json(response)
        error = data.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        code = error.get("code") if isinstance(error, dict) else None
        raise MessagingError(str(message or "Spectrum bridge request failed"), response.status_code, code)
    return _safe_json(response)


def _extract(data: dict[str, Any]) -> tuple[str, str, str | None]:
    message = data.get("message") if isinstance(data.get("message"), dict) else data
    sender = message.get("from") or message.get("sender") or data.get("from")
    text = message.get("text") or message.get("body") or data.get("text")
    provider_id = message.get("id") or message.get("message_id") or data.get("id")
    if not isinstance(sender, str) or not isinstance(text, str) or not text.strip():
        raise UnsupportedInboundMessage("Inbound payload does not contain a text message")
    return sender, text.strip()[:3500], str(provider_id) if provider_id else None


def _safe_data(data: dict) -> dict:
    allowed = {"id", "message_id", "from", "to", "status", "created_at"}
    return {key: value for key, value in data.items() if key in allowed}


def _safe_json(response: httpx.Response) -> dict:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _draft(message: MessageDraft | str) -> MessageDraft:
    return message if isinstance(message, MessageDraft) else MessageDraft(text=message)


def _native_message(draft: MessageDraft) -> str | None:
    phrase = draft.emphasis_phrase
    if not phrase or phrase not in draft.text:
        return None
    start = draft.text.index(phrase)
    end = start + len(phrase)
    return (
        _escape_markdown(draft.text[:start])
        + f"**{_escape_markdown(phrase)}**"
        + _escape_markdown(draft.text[end:])
    )


def _escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", value)
