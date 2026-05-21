import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from db.fsm_storage import SupabaseStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from config.settings import (
    CATEGORIES,
    INVITEMEMBER_MONTHLY_URL,
    INVITEMEMBER_YEARLY_URL,
    TELEGRAM_BOT_TOKEN,
)
from db.client import (
    create_user,
    get_user,
    get_user_profile,
    get_user_referral_count,
    get_user_stats,
    initialize_category_weights,
    update_user,
    upsert_user_profile,
)
from workers.feedback_engine import process_feedback

logger = logging.getLogger(__name__)
router = Router()

ROLES = [
    "Builder/Developer",
    "Founder/Operator",
    "Marketer",
    "Creator",
    "Other",
]

_SAMPLE_ALERT = (
    "🔧 *NEW TOOL*\n\n"
    "*Cursor AI 2.0* — The AI-first code editor\n\n"
    "💡 _As a developer, this IDE-level AI integration could replace GitHub Copilot in your Python & React workflows._\n\n"
    "🔗 [View Tool](https://cursor.sh)\n\n"
    "_↑ This is what your personalized alerts will look like._"
)


# ── FSM ───────────────────────────────────────────────────────────────────────

class Onboarding(StatesGroup):
    role = State()
    stack = State()
    categories = State()


# ── Keyboard builders ─────────────────────────────────────────────────────────

def _role_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=r)] for r in ROLES],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _category_kb(selected: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for cat in CATEGORIES:
        label = f"✅ {cat}" if cat in selected else cat
        rows.append([InlineKeyboardButton(text=label, callback_data=f"cat:{cat}")])
    rows.append([InlineKeyboardButton(text="✨ Done", callback_data="cat:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _upgrade_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Monthly $29", url=INVITEMEMBER_MONTHLY_URL),
        InlineKeyboardButton(text="📅 Yearly $199", url=INVITEMEMBER_YEARLY_URL),
    ]])


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    try:
        telegram_id = message.from_user.id

        # Parse referral code from deep link: /start ref_XXXXXXXX
        ref_code: str | None = None
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1 and parts[1].startswith("ref_"):
            ref_code = parts[1][4:]

        user = await get_user(telegram_id)

        if user:
            await state.clear()
            profile = await get_user_profile(telegram_id)
            if profile:
                status = user.get("status", "free").title()
                await message.answer(
                    f"👋 Welcome back!\n\n*Status:* {status}\n\nUse /help to see all commands.",
                    parse_mode="Markdown",
                )
            else:
                # Existing user without profile → restart onboarding
                await state.set_state(Onboarding.role)
                await message.answer(
                    "Let's finish your profile setup. What's your role?",
                    reply_markup=_role_kb(),
                )
            return

        # New user
        await create_user(
            telegram_id=telegram_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            referred_by=ref_code,
        )

        await state.set_state(Onboarding.role)
        await message.answer(
            "👋 Welcome to *Briifbot!*\n\n"
            "I monitor AI & tech tool launches globally and send you personalized alerts "
            "scored against your specific workflow.\n\n"
            "🎁 *You're starting a 7-day free trial* — full access, no credit card needed.\n\n"
            "Let's personalise your alerts in 3 quick steps.\n\n"
            "*Step 1 of 3 — What's your role?*",
            parse_mode="Markdown",
            reply_markup=_role_kb(),
        )
    except Exception as e:
        logger.error(f"cmd_start({message.from_user.id}): {e}")
        await message.answer("Something went wrong. Please try /start again.")


# ── Onboarding Step 1: Role ───────────────────────────────────────────────────

@router.message(Onboarding.role)
async def handle_role(message: Message, state: FSMContext) -> None:
    try:
        role = (message.text or "").strip()
        if role not in ROLES:
            await message.answer("Please choose one of the options:", reply_markup=_role_kb())
            return

        await state.update_data(role=role)
        await state.set_state(Onboarding.stack)
        await message.answer(
            f"Got it — *{role}*.\n\n"
            "*Step 2 of 3 — What tools and tech do you use?*\n\n"
            "Examples: _Python, React, Notion, n8n, Midjourney, HubSpot, Figma_\n\n"
            "Type it out (free text):",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception as e:
        logger.error(f"handle_role: {e}")
        await message.answer("Something went wrong. Try /start again.")


# ── Onboarding Step 2: Stack ──────────────────────────────────────────────────

@router.message(Onboarding.stack)
async def handle_stack(message: Message, state: FSMContext) -> None:
    try:
        stack = (message.text or "").strip()
        if len(stack) < 2:
            await message.answer("Please describe your stack or tools (at least a few words):")
            return

        await state.update_data(stack=stack, selected_categories=[])
        await state.set_state(Onboarding.categories)
        await message.answer(
            "*Step 3 of 3 — Which categories interest you?*\n\n"
            "Tap to toggle ✅, then tap *✨ Done* when ready:",
            parse_mode="Markdown",
            reply_markup=_category_kb([]),
        )
    except Exception as e:
        logger.error(f"handle_stack: {e}")
        await message.answer("Something went wrong. Try /start again.")


# ── Onboarding Step 3: Category toggle ───────────────────────────────────────

@router.callback_query(F.data.startswith("cat:"))
async def handle_category_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        if await state.get_state() != Onboarding.categories.state:
            await callback.answer()
            return

        action = callback.data.split(":", 1)[1]

        if action == "done":
            data = await state.get_data()
            selected: list[str] = data.get("selected_categories", [])

            if not selected:
                await callback.answer("Pick at least one category first!", show_alert=True)
                return

            role = data.get("role", "")
            stack = data.get("stack", "")
            telegram_id = callback.from_user.id

            await upsert_user_profile(telegram_id, role, stack, selected)
            await initialize_category_weights(telegram_id, selected)
            await state.clear()

            summary = (
                f"✅ *Profile saved!*\n\n"
                f"*Role:* {role}\n"
                f"*Stack:* {stack}\n"
                f"*Categories:* {', '.join(selected)}"
            )
            await callback.message.edit_text(summary, parse_mode="Markdown")
            await callback.message.answer(_SAMPLE_ALERT, parse_mode="Markdown")
            await callback.message.answer(
                "🎉 *Your 7-day trial has started!*\n\n"
                "You'll receive personalized alerts when new AI tools match your profile.\n\n"
                "Commands: /profile /upgrade /pause /refer /stats /help",
                parse_mode="Markdown",
            )
            await callback.answer("Profile saved!")
            return

        # Toggle category
        cat = action
        data = await state.get_data()
        selected = data.get("selected_categories", []).copy()
        if cat in selected:
            selected.remove(cat)
        else:
            selected.append(cat)

        await state.update_data(selected_categories=selected)
        await callback.message.edit_reply_markup(reply_markup=_category_kb(selected))
        await callback.answer()

    except Exception as e:
        logger.error(f"handle_category_toggle: {e}")
        await callback.answer("Something went wrong. Try again.")


# ── Feedback callbacks ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("feedback:"))
async def handle_feedback(callback: CallbackQuery) -> None:
    try:
        parts = callback.data.split(":", 2)
        if len(parts) != 3:
            await callback.answer()
            return
        _, fb_type, alert_id = parts

        success = await process_feedback(alert_id, callback.from_user.id, fb_type)
        if success:
            text = "✅ Thanks! Showing you more like this." if fb_type == "useful" else "❌ Got it! I'll tune your alerts."
            await callback.answer(text)
            # Remove buttons to prevent double-feedback
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        else:
            await callback.answer("Already recorded!")

    except Exception as e:
        logger.error(f"handle_feedback: {e}")
        await callback.answer("Something went wrong.")


# ── /profile ──────────────────────────────────────────────────────────────────

@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    try:
        user = await get_user(message.from_user.id)
        if not user:
            await message.answer("No account found. Use /start to set up.")
            return
        profile = await get_user_profile(message.from_user.id)
        if not profile:
            await message.answer("Profile incomplete. Use /start to finish setup.")
            return

        status = user.get("status", "free").title()
        paused = user.get("is_paused", False)
        await message.answer(
            f"👤 *Your Profile*\n\n"
            f"*Role:* {profile.get('role', '—')}\n"
            f"*Stack:* {profile.get('stack', '—')}\n"
            f"*Categories:* {', '.join(profile.get('categories') or [])}\n\n"
            f"*Status:* {status}\n"
            f"*Alerts:* {'⏸ Paused' if paused else '▶️ Active'}\n\n"
            f"Manage: /upgrade | /pause | /resume | /refer",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"cmd_profile({message.from_user.id}): {e}")
        await message.answer("Something went wrong. Please try again.")


# ── /upgrade ──────────────────────────────────────────────────────────────────

@router.message(Command("upgrade"))
async def cmd_upgrade(message: Message) -> None:
    try:
        user = await get_user(message.from_user.id)
        if user and user.get("status") == "paid":
            await message.answer("✅ You're already on Briifbot Pro. Enjoy your alerts!")
            return

        await message.answer(
            "🚀 *Upgrade to Briifbot Pro*\n\n"
            "Get up to *3 personalized alerts/day* scored specifically for your workflow.\n\n"
            "📊 *What you unlock:*\n"
            "• 🚨 Priority alerts (score ≥90) sent immediately\n"
            "• 🔧 Batch alerts (score 70-89) at 9am UTC\n"
            "• Feedback-tuned relevance over time\n"
            "• 1 free month per successful referral\n\n"
            "Choose your plan:",
            parse_mode="Markdown",
            reply_markup=_upgrade_kb(),
        )
    except Exception as e:
        logger.error(f"cmd_upgrade({message.from_user.id}): {e}")
        await message.answer("Something went wrong. Please try again.")


# ── /pause & /resume ──────────────────────────────────────────────────────────

@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    try:
        await update_user(message.from_user.id, {"is_paused": True})
        await message.answer("⏸ Alerts paused. Use /resume to turn them back on.")
    except Exception as e:
        logger.error(f"cmd_pause({message.from_user.id}): {e}")
        await message.answer("Something went wrong. Please try again.")


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    try:
        await update_user(message.from_user.id, {"is_paused": False})
        await message.answer("▶️ Alerts resumed! You'll receive alerts again.")
    except Exception as e:
        logger.error(f"cmd_resume({message.from_user.id}): {e}")
        await message.answer("Something went wrong. Please try again.")


# ── /stop ─────────────────────────────────────────────────────────────────────

@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    try:
        await update_user(message.from_user.id, {"status": "churned", "is_paused": True})
        await message.answer(
            "👋 You've been unsubscribed from Briifbot.\n\n"
            "Use /start to come back anytime — your profile is saved."
        )
    except Exception as e:
        logger.error(f"cmd_stop({message.from_user.id}): {e}")
        await message.answer("Something went wrong. Please try again.")


# ── /refer ────────────────────────────────────────────────────────────────────

@router.message(Command("refer"))
async def cmd_refer(message: Message) -> None:
    try:
        user = await get_user(message.from_user.id)
        if not user:
            await message.answer("Use /start first to get your referral link.")
            return

        code = user.get("referral_code", "")
        count = await get_user_referral_count(message.from_user.id)
        link = f"https://t.me/getbriifbot?start=ref_{code}"

        await message.answer(
            f"🎁 *Your Referral Link*\n\n"
            f"`{link}`\n\n"
            f"Share with anyone interested in AI tool alerts.\n"
            f"When they subscribe, you earn *1 free month* automatically.\n\n"
            f"Successful referrals: *{count}* 🏆",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"cmd_refer({message.from_user.id}): {e}")
        await message.answer("Something went wrong. Please try again.")


# ── /stats ────────────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    try:
        user = await get_user(message.from_user.id)
        if not user:
            await message.answer("Use /start to set up your account.")
            return

        stats = await get_user_stats(message.from_user.id)
        count = await get_user_referral_count(message.from_user.id)

        useful = stats.get("useful", 0)
        not_rel = stats.get("not_relevant", 0)
        total = stats.get("total_alerts", 0)
        rated = useful + not_rel
        accuracy = f"{round(useful / rated * 100)}%" if rated > 0 else "N/A"

        await message.answer(
            f"📊 *Your Briifbot Stats*\n\n"
            f"Total alerts received: *{total}*\n"
            f"Marked useful: *{useful}*\n"
            f"Marked not relevant: *{not_rel}*\n"
            f"Relevance accuracy: *{accuracy}*\n\n"
            f"Successful referrals: *{count}* 🏆",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"cmd_stats({message.from_user.id}): {e}")
        await message.answer("Something went wrong. Please try again.")


# ── /help ─────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🤖 *Briifbot Help*\n\n"
        "*Commands:*\n"
        "/start — Set up or restart onboarding\n"
        "/profile — View your current profile\n"
        "/upgrade — Subscribe for personalized alerts\n"
        "/pause — Pause all alerts\n"
        "/resume — Resume alerts\n"
        "/stop — Unsubscribe completely\n"
        "/refer — Get your referral link\n"
        "/stats — View your alert stats\n"
        "/help — Show this message\n\n"
        "*Alert types:*\n"
        "🚨 Priority — Score ≥90, sent immediately\n"
        "🔧 Standard — Score 70-89, batched at 9am UTC\n"
        "📋 Generic — Free tier, 1 per day\n\n"
        "Tap ✅ or ❌ on alerts to tune future relevance.",
        parse_mode="Markdown",
    )


# ── Bot/Dispatcher factory ────────────────────────────────────────────────────

async def create_bot() -> Bot:
    return Bot(token=TELEGRAM_BOT_TOKEN)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=SupabaseStorage())
    dp.include_router(router)
    return dp
