from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, status

from app.dependencies import DbSession
from app.models import ConnectionStatus, OnboardingStep, User
from app.phone import InvalidPhoneNumber, normalize_phone
from app.repositories import get_user_by_phone
from app.schemas import SignupRequest, SignupResponse
from app.services.messages import MessagesDevError

router = APIRouter(tags=["signup"])


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_202_ACCEPTED)
async def signup(
    payload: SignupRequest,
    request: Request,
    session: DbSession,
) -> SignupResponse:
    try:
        phone = normalize_phone(payload.phone_number)
        ZoneInfo(payload.timezone)
    except (InvalidPhoneNumber, ZoneInfoNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user = await get_user_by_phone(session, phone)
    existing = user is not None
    if user is None:
        user = User(phone_number=phone, timezone=payload.timezone)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    if (
        existing
        and user.wealthsimple_connection is not None
        and user.wealthsimple_connection.status == ConnectionStatus.connected
        and user.onboarding_step != OnboardingStep.awaiting_connection
    ):
        try:
            await request.app.state.conversations.send_and_record(
                session, user, "You’re already connected. Just text me whenever you want to talk investments."
            )
        except MessagesDevError as exc:
            raise HTTPException(status_code=502, detail="Could not send the welcome message") from exc
        return SignupResponse(user_id=user.id, status="already_registered")

    try:
        await request.app.state.conversations.welcome(session, user)
    except MessagesDevError as exc:
        if exc.status_code == 403:
            return SignupResponse(
                user_id=user.id,
                status="needs_first_message",
                line_handle=request.app.state.messages.settings.messages_line_handle or None,
            )
        raise HTTPException(status_code=502, detail="Could not send the welcome message") from exc
    return SignupResponse(
        user_id=user.id,
        status="already_registered" if existing else "message_queued",
    )
