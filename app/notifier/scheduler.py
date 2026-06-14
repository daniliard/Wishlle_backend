import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.core.config import settings
from app.notifier.service import send_daily_reminders

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.notifier_timezone))


async def _run_reminders_job() -> None:
    try:
        result = await send_daily_reminders()
        logger.info(
            "Event reminders: due=%d created=%d skipped=%d failed=%d",
            result["events_due"],
            result["notifications_created"],
            result["skipped_existing"],
            result["failed"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Reminder job failed: %s", exc)


def start_scheduler() -> None:
    if not settings.notifier_enabled:
        logger.info("Notifier scheduler is disabled")
        return
    if scheduler.running:
        return

    tz = ZoneInfo(settings.notifier_timezone)
    scheduler.add_job(
        _run_reminders_job,
        trigger=CronTrigger(
            hour=settings.notifier_cron_hour,
            minute=settings.notifier_cron_minute,
            timezone=tz,
        ),
        id="daily_event_reminders",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    if settings.notifier_run_on_startup:
        scheduler.add_job(
            _run_reminders_job,
            trigger=DateTrigger(
                run_date=datetime.now(tz) + timedelta(seconds=15),
                timezone=tz,
            ),
            id="startup_event_reminders",
            replace_existing=True,
            misfire_grace_time=300,
        )

    scheduler.start()
    logger.info(
        "Scheduler started: daily reminders at %02d:%02d %s; days=%s",
        settings.notifier_cron_hour,
        settings.notifier_cron_minute,
        settings.notifier_timezone,
        settings.notifier_reminder_days,
    )
