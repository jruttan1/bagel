import logging
import uuid
from contextlib import asynccontextmanager
from functools import partial

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import briefs
from app.config import get_settings
from app.db import Base, engine
from app.intelligence import IntelligenceService
from app.market import MarketDataClient
from app.routes import router
from app.scheduler import build_scheduler
from app.security import SecretBox
from app.wealthsimple import WealthsimpleService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bagel")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_create_tables and settings.app_env != "production":
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    market_data = MarketDataClient(settings)
    intelligence = IntelligenceService(settings, market_data)
    secret_box = SecretBox(settings.encryption_key) if settings.encryption_key else None
    wealthsimple = WealthsimpleService(secret_box) if secret_box else None
    http = httpx.AsyncClient(base_url=settings.spectrum_bridge_url, timeout=20)

    app.state.http = http
    app.state.intelligence = intelligence
    app.state.wealthsimple = wealthsimple
    app.state.send_brief = (
        partial(
            briefs.send_for_user,
            settings,
            wealthsimple,
            intelligence,
            market_data,
            http=http,
        )
        if wealthsimple
        else None
    )
    scheduler = None
    if settings.scheduler_enabled and wealthsimple is not None:
        scheduler = build_scheduler(
            partial(
                briefs.run_due,
                settings,
                wealthsimple,
                intelligence,
                market_data,
                http=http,
            ),
            partial(briefs.refresh_earnings_calendar, market_data),
        )
        scheduler.start()
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    await http.aclose()
    await engine.dispose()


app = FastAPI(title="Bagel API", version="0.1.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list({"http://localhost:5173", settings.app_base_url.rstrip("/")}),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_complete method=%s path=%s status=%s request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        request_id,
    )
    return response


app.include_router(router)
