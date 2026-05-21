import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from db.client import convert_referral, get_user, get_user_referral_count, update_user

logger = logging.getLogger(__name__)


async def handle_referral_conversion(bot: Bot, referred_telegram_id: int) -> None:
    """Called when a referred user activates a paid subscription."""
    try:
        referrer_id = await convert_referral(referred_telegram_id)
        if not referrer_id:
            return

        referrer = await get_user(referrer_id)
        if not referrer:
            return

        # Extend referrer's access by 30 days
        existing = referrer.get("paid_until")
        if existing:
            base = datetime.fromisoformat(existing)
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            # If already in the past, extend from now
            base = max(base, datetime.now(timezone.utc))
        else:
            base = datetime.now(timezone.utc)

        new_paid_until = (base + timedelta(days=30)).isoformat()
        await update_user(referrer_id, {
            "paid_until": new_paid_until,
            "subscription_status": "paid",
        })

        count = await get_user_referral_count(referrer_id)

        try:
            await bot.send_message(
                referrer_id,
                f"🎉 *Referral reward!*\n\n"
                f"Someone you referred just subscribed!\n"
                f"You've earned *1 free month* added to your plan.\n\n"
                f"Total successful referrals: *{count}* 🏆",
                parse_mode="Markdown",
            )
        except TelegramForbiddenError:
            await update_user(referrer_id, {"is_active": False})
        except Exception as e:
            logger.error(f"Failed to notify referrer {referrer_id}: {e}")

        logger.info(f"Referral converted: referrer={referrer_id}, referred={referred_telegram_id}")

    except Exception as e:
        logger.error(f"handle_referral_conversion({referred_telegram_id}): {e}")
