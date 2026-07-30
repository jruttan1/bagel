import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.routes import router
from app.scheduler import build_scheduler
from app.security import SecretBox
from app.services.briefs import BriefService
from app.services.connections import ConnectionLinkService
from app.services.conversation import ConversationService
from app.services.intelligence import IntelligenceService
from app.services.market_data import MarketDataClient
from app.services.messages import SpectrumBridgeClient
from app.services.onboarding import OnboardingService
from app.services.wealthsimple import WealthsimpleService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bagel")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_create_tables and settings.app_env != "production":
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    messages = SpectrumBridgeClient(settings)
    intelligence = IntelligenceService(settings)
    onboarding = OnboardingService(intelligence)
    connection_links = ConnectionLinkService(settings)
    secret_box = SecretBox(settings.encryption_key) if settings.encryption_key else None
    wealthsimple = WealthsimpleService(secret_box) if secret_box else None
    market_data = MarketDataClient(settings)

    app.state.messages = messages
    app.state.intelligence = intelligence
    app.state.onboarding = onboarding
    app.state.connection_links = connection_links
    app.state.wealthsimple = wealthsimple
    app.state.utcnow = lambda: datetime.now(UTC)
    app.state.conversations = ConversationService(messages, intelligence, onboarding, connection_links)
    app.state.briefs = (
        BriefService(SessionLocal, wealthsimple, intelligence, market_data, messages)
        if wealthsimple
        else None
    )
    scheduler = None
    if settings.scheduler_enabled and app.state.briefs is not None:
        scheduler = build_scheduler(app.state.briefs)
        scheduler.start()
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
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
