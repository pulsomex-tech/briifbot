import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone, timedelta

from aiohttp import web
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from config.settings import INVITEMEMBER_WEBHOOK_SECRET, INVITEMEMBER_MONTHLY_URL, INVITEMEMBER_YEARLY_URL
from db.client import get_user, record_webhook_event, update_user
from workers.referral_engine import handle_referral_conversion

logger = logging.getLogger(__name__)


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "briifbot"})


async def _invitemember_webhook(request: web.Request) -> web.Response:
    bot: Bot = request.app["bot"]

    try:
        body = await request.read()

        if INVITEMEMBER_WEBHOOK_SECRET:
            sig = request.headers.get("X-InviteMember-Signature", "")
            expected = hmac.new(
                INVITEMEMBER_WEBHOOK_SECRET.encode(),
                body,
                digestmod=hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                logger.warning("Invalid InviteMember signature")
                return web.Response(status=401, text="Invalid signature")

        payload = json.loads(body)
        event_type: str = payload.get("event", "unknown")

        await record_webhook_event(event_type, payload)
        logger.info(f"InviteMember event: {event_type}")

        handlers = {
            "subscription.activated": _on_activated,
            "subscription.cancelled": _on_cancelled,
            "subscription.payment_failed": _on_payment_failed,
        }
        handler = handlers.get(event_type)
        if handler:
            await handler(bot, payload)

        return web.json_response({"ok": True})

    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON")
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return web.Response(status=500, text="Internal error")


def _extract_telegram_id(payload: dict) -> int | None:
    tid = payload.get("telegram_id") or payload.get("user", {}).get("telegram_id")
    try:
        return int(tid) if tid else None
    except (TypeError, ValueError):
        return None


def _days_for_plan(payload: dict) -> int:
    period = (payload.get("plan") or {}).get("period", "monthly")
    return 365 if period in ("annual", "yearly") else 30


async def _on_activated(bot: Bot, payload: dict) -> None:
    telegram_id = _extract_telegram_id(payload)
    if not telegram_id:
        logger.error("subscription.activated: missing telegram_id")
        return

    days = _days_for_plan(payload)
    paid_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    await update_user(telegram_id, {
        "subscription_status": "paid",
        "paid_until": paid_until,
        "is_active": True,
        "alerts_paused": False,
    })

    await handle_referral_conversion(bot, telegram_id)

    user = await get_user(telegram_id)
    code = (user or {}).get("referral_code", "")
    referral_link = f"https://t.me/getbriifbot?start=ref_{code}"

    try:
        await bot.send_message(
            telegram_id,
            "🎉 *Welcome to Briifbot Pro!*\n\n"
            "You now get up to *3 personalized AI tool alerts per day*, "
            "scored specifically for your workflow.\n\n"
            "📤 Share your referral link and earn free months:\n"
            f"`{referral_link}`\n\n"
            "Use /profile to review your settings.",
            parse_mode="Markdown",
        )
    except TelegramForbiddenError:
        await update_user(telegram_id, {"is_active": False})
    except Exception as e:
        logger.error(f"_on_activated notify {telegram_id}: {e}")


async def _on_cancelled(bot: Bot, payload: dict) -> None:
    telegram_id = _extract_telegram_id(payload)
    if not telegram_id:
        return

    await update_user(telegram_id, {"subscription_status": "free"})

    try:
        await bot.send_message(
            telegram_id,
            "😢 Your Briifbot Pro subscription has been cancelled.\n\n"
            "You'll now receive 1 generic alert per day.\n\n"
            "Resubscribe anytime → /upgrade",
        )
    except TelegramForbiddenError:
        await update_user(telegram_id, {"is_active": False})
    except Exception as e:
        logger.error(f"_on_cancelled notify {telegram_id}: {e}")


async def _on_payment_failed(bot: Bot, payload: dict) -> None:
    telegram_id = _extract_telegram_id(payload)
    if not telegram_id:
        return

    try:
        await bot.send_message(
            telegram_id,
            "⚠️ *Payment Failed*\n\n"
            "We couldn't process your subscription payment.\n\n"
            "Please update your payment method to keep receiving personalized alerts.\n\n"
            "/upgrade to renew",
            parse_mode="Markdown",
        )
    except TelegramForbiddenError:
        await update_user(telegram_id, {"is_active": False})
    except Exception as e:
        logger.error(f"_on_payment_failed notify {telegram_id}: {e}")


def create_webhook_app(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/health", _health)
    app.router.add_post("/webhook/invitemember", _invitemember_webhook)
    return app
