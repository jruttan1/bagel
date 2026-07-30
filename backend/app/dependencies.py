from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.security import constant_time_equal

SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_admin(
    settings: SettingsDep,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.admin_api_key or not constant_time_equal(x_admin_key or "", settings.admin_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key")


def service(request: Request, name: str):
    return getattr(request.app.state, name)
