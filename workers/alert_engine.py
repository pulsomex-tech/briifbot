import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import (
    FREE_ALERTS_PER_DAY,
    INVITEMEMBER_MONTHLY_URL,
    INVITEMEMBER_YEARLY_URL,
    PAID_ALERTS_PER_DAY,
    PRIORITY_SCORE_THRESHOLD,
    STANDARD_SCORE_THRESHOLD,
)
from db.client import (
    create_alert,
    get_all_active_users,
    get_category_weights,
    get_free_users,
    get_recent_confirmed_tools,
    get_user_alert_count_today,
    get_user_profile,
    has_user_received_alert_for_tool,
    increment_daily_stat,
    update_user,
)
from workers.scoring_engine import score_tool_for_user

logger = logging.getLogger(__name__)


def _feedback_keyboard(alert_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Useful", callback_data=f"feedback:useful:{alert_id}"),
        InlineKeyboardButton(text="❌ Not relevant", callback_data=f"feedback:not_relevant:{alert_id}"),
    ]])


def _upgrade_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Monthly $29", url=INVITEMEMBER_MONTHLY_URL),
        InlineKeyboardButton(text="📅 Yearly $199", url=INVITEMEMBER_YEARLY_URL),
    ]])


def _fmt_priority(tool: dict, score_result: dict) -> str:
    name = tool.get("name") or tool.get("title", "")
    url = tool.get("url") or tool.get("source_url", "")
    return (
        f"🚨 *JUST LAUNCHED*\n\n"
        f"*{name}*\n\n"
        f"💡 _{score_result['reason']}_\n\n"
        f"🔗 [View Tool]({url})\n\n"
        f"Score: *{score_result['score']}/100*"
    )


def _fmt_standard(tool: dict, score_result: dict) -> str:
    name = tool.get("name") or tool.get("title", "")
    url = tool.get("url") or tool.get("source_url", "")
    return (
        f"🔧 *NEW TOOL*\n\n"
        f"*{name}*\n\n"
        f"💡 _{score_result['reason']}_\n\n"
        f"🔗 [View Tool]({url})"
    )


def _fmt_generic(tool: dict) -> str:
    name = tool.get("name") or tool.get("title", "")
    url = tool.get("url") or tool.get("source_url", "")
    desc = (tool.get("description") or "")[:200].strip()
    return (
        f"📋 *TODAY'S TOOL*\n\n"
        f"*{name}*\n\n"
        f"{desc}\n\n"
        f"🔗 [View Tool]({url})\n\n"
        f"🔓 Unlock personalized alerts → /upgrade"
    )


async def _mark_churned(telegram_id: int) -> None:
    await update_user(telegram_id, {"status": "churned"})


async def send_personalized_alert(bot: Bot, user: dict, tool: dict, score_result: dict) -> bool:
    telegram_id = user["telegram_id"]
    score: int = score_result.get("score", 0)
    try:
        if score >= PRIORITY_SCORE_THRESHOLD:
            text = _fmt_priority(tool, score_result)
            alert_type = "priority"
        else:
            text = _fmt_standard(tool, score_result)
            alert_type = "standard"

        alert = await create_alert(telegram_id, tool["id"], score, score_result.get("reason", ""), alert_type)
        await bot.send_message(
            telegram_id, text,
            parse_mode="Markdown",
            reply_markup=_feedback_keyboard(alert["id"]),
            disable_web_page_preview=False,
        )
        await increment_daily_stat("alerts_sent")
        return True
    except TelegramForbiddenError:
        logger.warning(f"User {telegram_id} blocked bot — marking churned")
        await _mark_churned(telegram_id)
        return False
    except Exception as e:
        logger.error(f"send_personalized_alert({telegram_id}): {e}")
        return False


async def send_generic_alert(bot: Bot, user: dict, tool: dict) -> bool:
    telegram_id = user["telegram_id"]
    try:
        alert = await create_alert(telegram_id, tool["id"], 0, "Free tier daily alert", "generic")
        await bot.send_message(
            telegram_id, _fmt_generic(tool),
            parse_mode="Markdown",
            disable_web_page_preview=False,
        )
        await increment_daily_stat("alerts_sent")
        return True
    except TelegramForbiddenError:
        await _mark_churned(telegram_id)
        return False
    except Exception as e:
        logger.error(f"send_generic_alert({telegram_id}): {e}")
        return False


async def dispatch_immediate_alerts(bot: Bot, tool: dict) -> None:
    if not tool.get("is_valid"):
        return

    users = await get_all_active_users()
    semaphore = asyncio.Semaphore(5)

    async def _process(user: dict) -> None:
        async with semaphore:
            profile = await get_user_profile(user["telegram_id"])
            if not profile:
                return
            weights = await get_category_weights(user["telegram_id"])
            score_result = await score_tool_for_user(tool, user, profile, weights)
            if not score_result:
                return
            if score_result.get("score", 0) >= PRIORITY_SCORE_THRESHOLD:
                await send_personalized_alert(bot, user, tool, score_result)

    await asyncio.gather(*[_process(u) for u in users], return_exceptions=True)


async def dispatch_batch_alerts(bot: Bot) -> None:
    tools = await get_recent_confirmed_tools(hours=24)
    users = await get_all_active_users()

    for tool in tools:
        for user in users:
            if user.get("status") not in ("paid", "trial"):
                continue
            if await has_user_received_alert_for_tool(user["telegram_id"], tool["id"]):
                continue
            if await get_user_alert_count_today(user["telegram_id"]) >= PAID_ALERTS_PER_DAY:
                continue
            profile = await get_user_profile(user["telegram_id"])
            if not profile:
                continue
            weights = await get_category_weights(user["telegram_id"])
            score_result = await score_tool_for_user(tool, user, profile, weights)
            if not score_result:
                continue
            score = score_result.get("score", 0)
            if STANDARD_SCORE_THRESHOLD <= score < PRIORITY_SCORE_THRESHOLD:
                await send_personalized_alert(bot, user, tool, score_result)


async def dispatch_generic_alerts(bot: Bot) -> None:
    tools = await get_recent_confirmed_tools(hours=24)
    if not tools:
        return
    free_users = await get_free_users()
    for user in free_users:
        if await get_user_alert_count_today(user["telegram_id"]) >= FREE_ALERTS_PER_DAY:
            continue
        for tool in tools:
            if not await has_user_received_alert_for_tool(user["telegram_id"], tool["id"]):
                await send_generic_alert(bot, user, tool)
                break


async def send_trial_expiry_warning(bot: Bot, user: dict) -> None:
    try:
        await bot.send_message(
            user["telegram_id"],
            "⏰ *Your 7-day free trial ends tomorrow!*\n\n"
            "You've been receiving personalized AI tool alerts scored for your workflow.\n\n"
            "After trial: 1 generic alert/day.\n"
            "With a subscription: up to 3 personalized alerts/day.\n\n"
            "Lock in your access now:",
            parse_mode="Markdown",
            reply_markup=_upgrade_keyboard(),
        )
    except TelegramForbiddenError:
        await _mark_churned(user["telegram_id"])
    except Exception as e:
        logger.error(f"send_trial_expiry_warning({user['telegram_id']}): {e}")
