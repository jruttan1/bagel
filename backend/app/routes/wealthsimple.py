from fastapi import APIRouter, HTTPException, Request

from app import crud, messages
from app.config import get_settings
from app.schemas import ConnectionTokenStatus, WealthsimpleConnectRequest, WealthsimpleConnectResponse
from app.wealthsimple import (
    WealthsimpleIntegrationError,
    WealthsimpleOTPRequired,
)

router = APIRouter(prefix="/wealthsimple", tags=["wealthsimple"])


@router.get("/connection/{token}", response_model=ConnectionTokenStatus)
async def connection_status(
    token: str,
    request: Request,
) -> ConnectionTokenStatus:
    resolved = await crud.resolve_token(token)
    if resolved is None:
        return ConnectionTokenStatus(valid=False)
    _, user = resolved
    return ConnectionTokenStatus(valid=True, phone_hint=f"••• ••• {user.phone_number[-4:]}")


@router.post("/connect", response_model=WealthsimpleConnectResponse)
async def connect(
    payload: WealthsimpleConnectRequest,
    request: Request,
) -> WealthsimpleConnectResponse:
    if request.app.state.wealthsimple is None:
        raise HTTPException(status_code=503, detail="Credential encryption is not configured")
    try:
        resolved = await crud.resolve_token(payload.token)
        if resolved is None:
            raise ValueError("This connection link is invalid or has expired")
        link, user = resolved
        await request.app.state.wealthsimple.connect(user.id, payload.username, payload.password, payload.otp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except WealthsimpleOTPRequired:
        return WealthsimpleConnectResponse(status="otp_required")
    except WealthsimpleIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        await request.app.state.wealthsimple.sync_user(user.id)
    except WealthsimpleIntegrationError as exc:
        raise HTTPException(status_code=502, detail="Connected, but the first sync failed") from exc

    await crud.consume_token(link.id, user.id)
    user = await crud.user_by_id(user.id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    question = await messages.question_for(request.app.state.intelligence, user)
    await messages.send_and_record(get_settings(), user, question, client=request.app.state.http)
    return WealthsimpleConnectResponse(status="connected")
