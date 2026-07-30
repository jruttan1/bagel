from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConversationMessage, MessageDirection, OnboardingStep, User
from app.phone import InvalidPhoneNumber, normalize_phone
from app.repositories import get_user_by_phone, latest_snapshot, recent_messages
from app.services.connections import ConnectionLinkService
from app.services.intelligence import IntelligenceService, IntelligenceUnavailable
from app.services.messages import SpectrumBridgeClient
from app.services.onboarding import OnboardingService


class UnsupportedInboundMessage(ValueError):
    pass


class ConversationService:
    def __init__(
        self,
        messages: SpectrumBridgeClient,
        intelligence: IntelligenceService,
        onboarding: OnboardingService,
        connection_links: ConnectionLinkService,
    ):
        self.messages = messages
        self.intelligence = intelligence
        self.onboarding = onboarding
        self.connection_links = connection_links

    async def welcome(self, session: AsyncSession, user: User) -> str:
        token = await self.connection_links.issue(session, user)
        text = (
            "Thanks for joining! Connect your Brokerage, then I’ll learn "
            f"what matters to you in a few texts: {self.connection_links.url(token)}"
        )
        await self.send_and_record(session, user, text)
        return text

    async def handle_inbound(self, session: AsyncSession, data: dict[str, Any]) -> None:
        sender, text, provider_id = _extract_inbound(data)
        try:
            phone = normalize_phone(sender)
        except InvalidPhoneNumber as exc:
            raise UnsupportedInboundMessage("Inbound message has an invalid sender") from exc

        user = await get_user_by_phone(session, phone)
        if user is None:
            user = User(phone_number=phone)
            session.add(user)
            await session.flush()

        session.add(
            ConversationMessage(
                user_id=user.id,
                provider_message_id=provider_id,
                direction=MessageDirection.inbound,
                content=text,
                provider_data=_safe_provider_data(data),
            )
        )
        await session.commit()

        async with self.messages.typing(phone):
            if user.onboarding_step == OnboardingStep.awaiting_connection:
                await self.welcome(session, user)
                return
            if user.onboarding_step != OnboardingStep.complete:
                reply = await self.onboarding.handle_answer(session, user, text)
            else:
                snapshot = await latest_snapshot(session, user.id)
                history = await recent_messages(session, user.id)
                try:
                    reply = await self.intelligence.reply(user, text, snapshot, list(user.theses), history)
                except IntelligenceUnavailable:
                    reply = (
                        "I can see your message, but my market analysis is temporarily unavailable; "
                        "I’ll pick this back up once it’s restored."
                    )
            await self.send_and_record(session, user, reply, reply_to=provider_id)

    async def send_and_record(
        self,
        session: AsyncSession,
        user: User,
        text: str,
        *,
        reply_to: str | None = None,
    ) -> None:
        result = await self.messages.send_message(user.phone_number, text, reply_to=reply_to)
        session.add(
            ConversationMessage(
                user_id=user.id,
                provider_message_id=result.id,
                direction=MessageDirection.outbound,
                content=text,
                provider_data={"status": result.status, "request_id": result.request_id},
            )
        )
        await session.commit()


def _extract_inbound(data: dict[str, Any]) -> tuple[str, str, str | None]:
    message = data.get("message") if isinstance(data.get("message"), dict) else data
    sender = message.get("from") or message.get("sender") or data.get("from")
    text = message.get("text") or message.get("body") or data.get("text")
    provider_id = message.get("id") or message.get("message_id") or data.get("id")
    if not isinstance(sender, str) or not isinstance(text, str) or not text.strip():
        raise UnsupportedInboundMessage("Webhook does not contain an inbound text message")
    return sender, text.strip()[:3500], str(provider_id) if provider_id else None


def _safe_provider_data(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {"id", "message_id", "from", "to", "status", "created_at"}
    return {key: value for key, value in data.items() if key in allowed}
