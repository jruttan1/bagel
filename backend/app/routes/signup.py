from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, status

from app import crud, messages
from app.config import get_settings
from app.models import ConnectionStatus, OnboardingStep
from app.phone import InvalidPhoneNumber, normalize_phone
from app.schemas import SignupRequest, SignupResponse

router = APIRouter(tags=["signup"])


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_202_ACCEPTED)
async def signup(
    payload: SignupRequest,
    request: Request,
) -> SignupResponse:
    try:
        phone = normalize_phone(payload.phone_number)
        ZoneInfo(payload.timezone)
    except (InvalidPhoneNumber, ZoneInfoNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user, created = await crud.get_or_create_user(phone, payload.timezone)
    existing = not created
    settings = get_settings()

    if (
        existing
        and user.wealthsimple_connection is not None
        and user.wealthsimple_connection.status == ConnectionStatus.connected
        and user.onboarding_step != OnboardingStep.awaiting_connection
    ):
        try:
            await messages.send_and_record(
                settings,
                user,
                "You’re already connected. Just text me whenever you want to talk investments.",
                client=request.app.state.http,
            )
        except messages.MessagingError:
            return SignupResponse(
                user_id=user.id,
                status="needs_first_message",
                line_handle=settings.spectrum_shared_number or None,
            )
        return SignupResponse(user_id=user.id, status="already_registered")

    return SignupResponse(
        user_id=user.id,
        status="needs_first_message",
        line_handle=settings.spectrum_shared_number or None,
    )
