import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from config.settings import INGESTION_INTERVAL_MINUTES

logger = logging.getLogger(__name__)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        _ingest_and_dispatch,
        IntervalTrigger(minutes=INGESTION_INTERVAL_MINUTES),
        args=[bot],
        id="ingest_dispatch",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )

    scheduler.add_job(
        _daily_tasks,
        CronTrigger(hour=9, minute=0, timezone="UTC"),
        args=[bot],
        id="daily_tasks",
        max_instances=1,
    )

    scheduler.add_job(
        _downgrade_expired_trials,
        CronTrigger(hour=0, minute=0, timezone="UTC"),
        args=[bot],
        id="trial_downgrade",
        max_instances=1,
    )

    return scheduler


async def _ingest_and_dispatch(bot: Bot) -> None:
    try:
        from workers.ingestion_worker import ingest_all
        from workers.filter_worker import filter_tools
        from workers.alert_engine import dispatch_immediate_alerts
        from db.client import get_recent_confirmed_tools

        await ingest_all()
        await filter_tools()

        # Dispatch priority alerts for tools ingested in the last 20 minutes
        fresh_tools = await get_recent_confirmed_tools(hours=24)  # filter to ingest window below

        # Only process tools published in the last ingest window
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=INGESTION_INTERVAL_MINUTES + 5)
        for tool in fresh_tools:
            published = tool.get("published_at", "")
            try:
                pub_dt = datetime.fromisoformat(published)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except Exception:
                continue
            await dispatch_immediate_alerts(bot, tool)

    except Exception as e:
        logger.error(f"_ingest_and_dispatch error: {e}")


async def _daily_tasks(bot: Bot) -> None:
    try:
        from workers.alert_engine import (
            dispatch_batch_alerts,
            dispatch_generic_alerts,
            send_trial_expiry_warning,
        )
        from db.client import get_trial_users

        await dispatch_batch_alerts(bot)
        await dispatch_generic_alerts(bot)

        # Warn users whose trial ends tomorrow
        now = datetime.now(timezone.utc)
        for user in await get_trial_users():
            trial_ends = user.get("trial_ends_at", "")
            if not trial_ends:
                continue
            try:
                ends_dt = datetime.fromisoformat(trial_ends)
                if ends_dt.tzinfo is None:
                    ends_dt = ends_dt.replace(tzinfo=timezone.utc)
                days_left = (ends_dt - now).days
                if days_left == 1:
                    await send_trial_expiry_warning(bot, user)
            except Exception:
                continue

    except Exception as e:
        logger.error(f"_daily_tasks error: {e}")


async def _downgrade_expired_trials(bot: Bot) -> None:
    try:
        from db.client import get_expired_trials, update_user

        expired = await get_expired_trials()
        for user in expired:
            await update_user(user["telegram_id"], {"subscription_status": "free"})
            try:
                await bot.send_message(
                    user["telegram_id"],
                    "📋 Your free trial has ended.\n\n"
                    "You'll now receive 1 generic alert per day.\n\n"
                    "Upgrade to restore personalized alerts → /upgrade",
                )
            except TelegramForbiddenError:
                await update_user(user["telegram_id"], {"is_active": False})
            except Exception as e:
                logger.error(f"_downgrade_expired_trials notify {user['telegram_id']}: {e}")

        if expired:
            logger.info(f"Downgraded {len(expired)} expired trials")

    except Exception as e:
        logger.error(f"_downgrade_expired_trials error: {e}")
