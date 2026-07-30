from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, status

from app.dependencies import DbSession
from app.models import ConnectionStatus, OnboardingStep, User
from app.phone import InvalidPhoneNumber, normalize_phone
from app.repositories import get_user_by_phone
from app.schemas import SignupRequest, SignupResponse
from app.services.messages import MessagingError

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
        except MessagingError:
            return SignupResponse(
                user_id=user.id,
                status="needs_first_message",
                line_handle=request.app.state.messages.settings.spectrum_shared_number or None,
            )
        return SignupResponse(user_id=user.id, status="already_registered")

    return SignupResponse(
        user_id=user.id,
        status="needs_first_message",
        line_handle=request.app.state.messages.settings.spectrum_shared_number or None,
    )
