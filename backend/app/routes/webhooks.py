import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.dependencies import DbSession
from app.models import WebhookDelivery
from app.schemas import MessagesWebhook
from app.security import verify_messages_webhook
from app.services.conversation import UnsupportedInboundMessage

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger("bagel.webhooks")


@router.post("/messages", status_code=status.HTTP_202_ACCEPTED)
async def messages_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
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
    background_tasks.add_task(_process_inbound, request.app.state.conversations, payload.data)
    return {"status": "queued"}


async def _process_inbound(conversations, data: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        try:
            await conversations.handle_inbound(session, data)
        except UnsupportedInboundMessage:
            logger.info("Ignoring unsupported inbound message")
        except Exception:
            logger.exception("Inbound message processing failed")
