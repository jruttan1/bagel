from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.dependencies import DbSession, require_admin
from app.models import InvestmentThesis, User
from app.schemas import SyncResult, ThesisUpsertRequest
from app.services.wealthsimple import WealthsimpleIntegrationError

router = APIRouter(dependencies=[Depends(require_admin)], tags=["admin"])


@router.post("/users/{user_id}/sync", response_model=SyncResult)
async def sync_user(
    user_id: UUID,
    request: Request,
    session: DbSession,
) -> SyncResult:
    if request.app.state.wealthsimple is None:
        raise HTTPException(status_code=503, detail="Wealthsimple is not configured")
    try:
        return await request.app.state.wealthsimple.sync_user(session, user_id)
    except WealthsimpleIntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/users/{user_id}/brief")
async def send_brief(user_id: UUID, request: Request) -> dict[str, bool]:
    if request.app.state.briefs is None:
        raise HTTPException(status_code=503, detail="Portfolio briefs are not configured")
    return {"sent": await request.app.state.briefs.send_for_user(user_id)}


@router.put("/users/{user_id}/theses/{ticker}")
async def upsert_thesis(
    user_id: UUID,
    ticker: str,
    payload: ThesisUpsertRequest,
    session: DbSession,
) -> dict[str, str]:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    symbol = ticker.strip().upper()
    thesis = await session.scalar(
        select(InvestmentThesis).where(
            InvestmentThesis.user_id == user_id,
            InvestmentThesis.ticker == symbol,
            InvestmentThesis.is_active.is_(True),
        )
    )
    values = payload.model_dump(exclude={"ticker"})
    if thesis is None:
        thesis = InvestmentThesis(user_id=user_id, ticker=symbol, **values)
        session.add(thesis)
    else:
        for key, value in values.items():
            setattr(thesis, key, value)
    await session.commit()
    return {"id": str(thesis.id)}
