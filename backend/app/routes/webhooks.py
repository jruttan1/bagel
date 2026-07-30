import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app import crud, messages
from app.config import get_settings
from app.schemas import SpectrumInboundMessage
from app.security import constant_time_equal

router = APIRouter(tags=["webhooks"])
logger = logging.getLogger("bagel.webhooks")


@router.post("/spectrum/inbound", status_code=status.HTTP_202_ACCEPTED)
async def spectrum_inbound(
    payload: SpectrumInboundMessage,
    request: Request,
    background_tasks: BackgroundTasks,
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

    if not await crud.register_delivery(payload.delivery_id):
        return {"status": "duplicate"}

    data = {
        "id": payload.provider_message_id,
        "from": payload.sender,
        "text": payload.text,
        "created_at": payload.timestamp.isoformat() if payload.timestamp else None,
    }
    background_tasks.add_task(
        _process_inbound,
        settings,
        request.app.state.agent,
        request.app.state.onboarding,
        request.app.state.http,
        data,
    )
    return {"status": "queued"}


async def _process_inbound(settings, agent, onboarding, http, data: dict) -> None:
    try:
        await messages.handle_inbound(settings, agent, onboarding, data, client=http)
    except messages.UnsupportedInboundMessage:
        logger.info("Ignoring unsupported inbound message")
    except Exception:
        logger.exception("Inbound message processing failed")
