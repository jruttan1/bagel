from fastapi import APIRouter

from app.routes import admin, health, signup, wealthsimple, webhooks

router = APIRouter()
router.include_router(health.router)
router.include_router(signup.router, prefix="/api/v1")
router.include_router(wealthsimple.router, prefix="/api/v1")
router.include_router(admin.router, prefix="/api/v1/admin")
router.include_router(webhooks.router, prefix="/internal")
