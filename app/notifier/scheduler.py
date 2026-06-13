import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.notifier.service import send_daily_reminders


logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.notifier_timezone))


async def _run_reminders_job() -> None:
    try:
        result = await send_daily_reminders()
        logger.info(
            "Daily reminders sent: %d events, %d wishes",
            result["events"], result["wishes"],
        )
    except Exception as exc:
        logger.exception("Reminder job failed: %s", exc)


def start_scheduler() -> None:
    if scheduler.running:
        return

    trigger = CronTrigger(
        hour=settings.notifier_cron_hour,
        minute=settings.notifier_cron_minute,
        timezone=ZoneInfo(settings.notifier_timezone),
    )

    scheduler.add_job(
        _run_reminders_job,
        trigger=trigger,
        id="daily_reminders",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: daily_reminders at %02d:%02d %s",
        settings.notifier_cron_hour,
        settings.notifier_cron_minute,
        settings.notifier_timezone,
    )
