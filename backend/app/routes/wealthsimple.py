from fastapi import APIRouter, HTTPException, Request

from app.dependencies import DbSession
from app.models import OnboardingStep
from app.schemas import ConnectionTokenStatus, WealthsimpleConnectRequest, WealthsimpleConnectResponse
from app.services.connections import InvalidConnectionToken
from app.services.wealthsimple import (
    WealthsimpleIntegrationError,
    WealthsimpleOTPRequired,
)

router = APIRouter(prefix="/wealthsimple", tags=["wealthsimple"])


@router.get("/connection/{token}", response_model=ConnectionTokenStatus)
async def connection_status(
    token: str,
    request: Request,
    session: DbSession,
) -> ConnectionTokenStatus:
    try:
        _, user = await request.app.state.connection_links.resolve(session, token)
    except InvalidConnectionToken:
        return ConnectionTokenStatus(valid=False)
    return ConnectionTokenStatus(valid=True, phone_hint=f"••• ••• {user.phone_number[-4:]}")


@router.post("/connect", response_model=WealthsimpleConnectResponse)
async def connect(
    payload: WealthsimpleConnectRequest,
    request: Request,
    session: DbSession,
) -> WealthsimpleConnectResponse:
    try:
        link, user = await request.app.state.connection_links.resolve(session, payload.token)
        await request.app.state.wealthsimple.connect(
            session, user.id, payload.username, payload.password, payload.otp
        )
    except InvalidConnectionToken as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except WealthsimpleOTPRequired:
        return WealthsimpleConnectResponse(status="otp_required")
    except WealthsimpleIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        await request.app.state.wealthsimple.sync_user(session, user.id)
    except WealthsimpleIntegrationError as exc:
        raise HTTPException(status_code=502, detail="Connected, but the first sync failed") from exc

    link.used_at = request.app.state.utcnow()
    user.onboarding_step = OnboardingStep.financial_position
    await session.commit()
    question = await request.app.state.onboarding.question_for(session, user)
    await request.app.state.conversations.send_and_record(session, user, question)
    return WealthsimpleConnectResponse(status="connected")
