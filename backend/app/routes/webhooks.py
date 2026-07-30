import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.dependencies import DbSession
from app.models import WebhookDelivery
from app.schemas import SpectrumInboundMessage
from app.security import constant_time_equal
from app.services.conversation import UnsupportedInboundMessage

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger("bagel.webhooks")


@router.post("/spectrum/inbound", status_code=status.HTTP_202_ACCEPTED)
async def spectrum_inbound(
    payload: SpectrumInboundMessage,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DbSession,
) -> dict[str, str]:
    settings = get_settings()
    authorization = request.headers.get("Authorization", "")
    supplied_token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    client_host = request.client.host if request.client else ""
    local_development = settings.app_env != "production" and client_host in {"127.0.0.1", "::1", "testclient"}
    if settings.spectrum_bridge_token:
        authorized = constant_time_equal(supplied_token, settings.spectrum_bridge_token)
    else:
        authorized = local_development
    if not authorized:
        raise HTTPException(status_code=401, detail="Invalid bridge token")

    session.add(WebhookDelivery(delivery_id=payload.delivery_id, event_name="message.received"))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return {"status": "duplicate"}

    data = {
        "id": payload.provider_message_id,
        "from": payload.sender,
        "text": payload.text,
        "created_at": payload.timestamp.isoformat() if payload.timestamp else None,
    }
    background_tasks.add_task(_process_inbound, request.app.state.conversations, data)
    return {"status": "queued"}


async def _process_inbound(conversations, data: dict) -> None:
    async with SessionLocal() as session:
        try:
            await conversations.handle_inbound(session, data)
        except UnsupportedInboundMessage:
            logger.info("Ignoring unsupported inbound message")
        except Exception:
            logger.exception("Inbound message processing failed")
