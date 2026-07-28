import json

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.dependencies import DbSession
from app.models import WebhookDelivery
from app.schemas import MessagesWebhook
from app.security import verify_messages_webhook
from app.services.conversation import UnsupportedInboundMessage

router = APIRouter(tags=["webhooks"])


@router.post("/messages", status_code=status.HTTP_202_ACCEPTED)
async def messages_webhook(
    request: Request,
    session: DbSession,
) -> dict[str, str]:
    raw_body = await request.body()
    settings = get_settings()
    if not verify_messages_webhook(
        raw_body,
        request.headers.get("X-Webhook-Signature", ""),
        request.headers.get("X-Webhook-Timestamp", ""),
        settings.messages_webhook_secret,
        settings.webhook_tolerance_seconds,
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = MessagesWebhook.model_validate(json.loads(raw_body))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid webhook payload") from exc

    session.add(WebhookDelivery(delivery_id=payload.delivery_id, event_name=payload.event))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"status": "duplicate"}

    if "received" not in payload.event.lower() and "inbound" not in payload.event.lower():
        return {"status": "ignored"}
    try:
        await request.app.state.conversations.handle_inbound(session, payload.data)
    except UnsupportedInboundMessage:
        return {"status": "ignored"}
    return {"status": "accepted"}
