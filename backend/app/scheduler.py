# Cron to run each morning
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.services.briefs import BriefService


def build_scheduler(briefs: BriefService) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        briefs.run_due,
        "interval",
        minutes=10,
        id="morning-briefs",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        briefs.refresh_earnings_calendar,
        "cron",
        hour="*/6",
        id="earnings-calendar",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
