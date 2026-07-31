from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app import crud
from app.dependencies import require_admin
from app.schemas import SyncResult, ThesisUpsertRequest
from app.wealthsimple import WealthsimpleIntegrationError

router = APIRouter(dependencies=[Depends(require_admin)], tags=["admin"])


@router.post("/users/{user_id}/sync", response_model=SyncResult)
async def sync_user(
    user_id: UUID,
    request: Request,
) -> SyncResult:
    if request.app.state.wealthsimple is None:
        raise HTTPException(status_code=503, detail="Wealthsimple is not configured")
    try:
        return await request.app.state.wealthsimple.sync_user(user_id)
    except WealthsimpleIntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/users/{user_id}/brief")
async def send_brief(user_id: UUID, request: Request) -> dict[str, bool]:
    if request.app.state.send_brief is None:
        raise HTTPException(status_code=503, detail="Portfolio briefs are not configured")
    return {"sent": await request.app.state.send_brief(user_id, force=True)}


@router.put("/users/{user_id}/theses/{ticker}")
async def upsert_thesis(
    user_id: UUID,
    ticker: str,
    payload: ThesisUpsertRequest,
) -> dict[str, str]:
    thesis = await crud.upsert_thesis(
        user_id,
        ticker.strip().upper(),
        payload.model_dump(exclude={"ticker"}),
    )
    if thesis is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"id": str(thesis.id)}
