# Cron to run each morning
from apscheduler.schedulers.asyncio import AsyncIOScheduler


def build_scheduler(run_briefs, refresh_events) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_briefs,
        "interval",
        minutes=10,
        id="morning-briefs",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        refresh_events,
        "cron",
        hour="*/6",
        id="earnings-calendar",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
