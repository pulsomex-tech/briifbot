import logging
import secrets
import string
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from supabase import create_async_client, AsyncClient

from config.settings import SUPABASE_URL, SUPABASE_SERVICE_KEY, REFERRAL_CODE_LENGTH, TRIAL_DAYS

logger = logging.getLogger(__name__)

_client: Optional[AsyncClient] = None


async def get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


def _gen_code() -> str:
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(REFERRAL_CODE_LENGTH))


def _ms(result) -> Optional[dict]:
    """Safely extract data from a maybe_single() result (returns None in supabase 2.30+)."""
    return result.data if result is not None else None


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_user(telegram_id: int) -> Optional[dict]:
    db = await get_client()
    try:
        result = await db.table("users").select("*").eq("telegram_id", telegram_id).maybe_single().execute()
        return _ms(result)
    except Exception as e:
        logger.error(f"get_user({telegram_id}): {e}")
        return None


async def create_user(
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
    referred_by: Optional[str] = None,
) -> dict:
    db = await get_client()
    code = _gen_code()
    trial_end = (datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)).isoformat()

    referred_by_uuid: Optional[str] = None
    if referred_by:
        referrer = await get_user_by_referral_code(referred_by)
        if referrer:
            referred_by_uuid = referrer["id"]

    result = await db.table("users").insert({
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "referral_code": code,
        "status": "trial",
        "trial_end_date": trial_end,
        "is_paused": False,
        "referred_by": referred_by_uuid,
    }).execute()
    return result.data[0]


async def update_user(telegram_id: int, updates: dict) -> Optional[dict]:
    db = await get_client()
    # Translate legacy field names callers may still use
    _rename = {
        "subscription_status": "status",
        "alerts_paused": "is_paused",
        "trial_ends_at": "trial_end_date",
        "is_active": None,  # column doesn't exist — drop it
    }
    mapped: dict = {}
    for k, v in updates.items():
        new_key = _rename.get(k, k)
        if new_key is not None:
            mapped[new_key] = v
    if not mapped:
        return None
    try:
        result = await db.table("users").update(mapped).eq("telegram_id", telegram_id).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"update_user({telegram_id}): {e}")
        return None


async def get_user_by_referral_code(code: str) -> Optional[dict]:
    db = await get_client()
    try:
        result = await db.table("users").select("*").eq("referral_code", code).maybe_single().execute()
        return _ms(result)
    except Exception as e:
        logger.error(f"get_user_by_referral_code({code}): {e}")
        return None


async def get_all_active_users() -> list[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("users")
            .select("*")
            .in_("status", ["trial", "paid"])
            .eq("is_paused", False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_all_active_users: {e}")
        return []


async def get_free_users() -> list[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("users")
            .select("*")
            .eq("status", "free")
            .eq("is_paused", False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_free_users: {e}")
        return []


async def get_trial_users() -> list[dict]:
    db = await get_client()
    try:
        result = await db.table("users").select("*").eq("status", "trial").execute()
        return result.data or []
    except Exception as e:
        logger.error(f"get_trial_users: {e}")
        return []


async def get_expired_trials() -> list[dict]:
    db = await get_client()
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = (
            await db.table("users")
            .select("*")
            .eq("status", "trial")
            .lt("trial_end_date", now)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_expired_trials: {e}")
        return []


# ── User Profiles ─────────────────────────────────────────────────────────────

async def get_user_profile(telegram_id: int) -> Optional[dict]:
    db = await get_client()
    try:
        user = await get_user(telegram_id)
        if not user:
            return None
        result = (
            await db.table("user_profiles")
            .select("*")
            .eq("user_id", user["id"])
            .maybe_single()
            .execute()
        )
        return _ms(result)
    except Exception as e:
        logger.error(f"get_user_profile({telegram_id}): {e}")
        return None


async def upsert_user_profile(telegram_id: int, role: str, stack: str, categories: list[str]) -> dict:
    db = await get_client()
    user = await get_user(telegram_id)
    if not user:
        raise ValueError(f"User not found: {telegram_id}")
    stack_arr = [s.strip() for s in stack.split(",") if s.strip()] or [stack]
    # Clear FSM state now that onboarding is complete; preserve field via upsert
    result = await db.table("user_profiles").upsert(
        {
            "user_id": user["id"],
            "work_type": role,
            "tech_stack": stack_arr,
            "categories": categories,
            "raw_onboarding_response": None,
        },
        on_conflict="user_id",
    ).execute()
    return result.data[0]


# ── Category Weights ──────────────────────────────────────────────────────────

async def get_category_weights(telegram_id: int) -> dict:
    db = await get_client()
    try:
        user = await get_user(telegram_id)
        if not user:
            return {}
        result = (
            await db.table("category_weights")
            .select("category,weight")
            .eq("user_id", user["id"])
            .execute()
        )
        return {row["category"]: row["weight"] for row in (result.data or [])}
    except Exception as e:
        logger.error(f"get_category_weights({telegram_id}): {e}")
        return {}


async def update_category_weight(telegram_id: int, category: str, delta: float) -> None:
    db = await get_client()
    try:
        user = await get_user(telegram_id)
        if not user:
            return
        uid = user["id"]
        result = (
            await db.table("category_weights")
            .select("id,weight")
            .eq("user_id", uid)
            .eq("category", category)
            .maybe_single()
            .execute()
        )
        row = _ms(result)
        if row:
            new_weight = max(0.1, min(3.0, row["weight"] + delta))
            await db.table("category_weights").update({"weight": new_weight}).eq("id", row["id"]).execute()
        else:
            initial = max(0.1, min(3.0, 1.0 + delta))
            await db.table("category_weights").insert(
                {"user_id": uid, "category": category, "weight": initial}
            ).execute()
    except Exception as e:
        logger.error(f"update_category_weight({telegram_id}, {category}): {e}")


async def initialize_category_weights(telegram_id: int, categories: list[str]) -> None:
    db = await get_client()
    try:
        user = await get_user(telegram_id)
        if not user:
            return
        rows = [{"user_id": user["id"], "category": cat, "weight": 1.0} for cat in categories]
        if rows:
            await db.table("category_weights").upsert(rows, on_conflict="user_id,category").execute()
    except Exception as e:
        logger.error(f"initialize_category_weights({telegram_id}): {e}")


# ── Tools ─────────────────────────────────────────────────────────────────────

async def get_tool_by_url(url: str) -> Optional[dict]:
    db = await get_client()
    try:
        result = await db.table("tools").select("id").eq("url", url).maybe_single().execute()
        return _ms(result)
    except Exception as e:
        logger.error(f"get_tool_by_url: {e}")
        return None


async def create_tool(tool_data: dict) -> dict:
    db = await get_client()
    # Map incoming keys to actual schema
    mapped = {
        "name": tool_data.get("title") or tool_data.get("name", ""),
        "description": tool_data.get("description", ""),
        "url": tool_data.get("source_url") or tool_data.get("url", ""),
        "source": tool_data.get("source", ""),
        "categories": tool_data.get("categories", []),
        "tags": tool_data.get("tags", []),
    }
    result = await db.table("tools").insert(mapped).execute()
    return result.data[0]


async def get_unprocessed_tools() -> list[dict]:
    """Tools where is_valid is NULL (not yet classified by filter_worker)."""
    db = await get_client()
    try:
        result = await db.table("tools").select("*").is_("is_valid", "null").execute()
        return result.data or []
    except Exception as e:
        logger.error(f"get_unprocessed_tools: {e}")
        return []


async def get_recent_confirmed_tools(hours: int = 24) -> list[dict]:
    db = await get_client()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        result = (
            await db.table("tools")
            .select("*")
            .eq("is_valid", True)
            .gte("detected_at", cutoff)
            .order("detected_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_recent_confirmed_tools: {e}")
        return []


async def update_tool(tool_id: str, updates: dict) -> None:
    db = await get_client()
    try:
        # Translate legacy field names
        _rename = {"is_tool": "is_valid", "is_processed": None, "published_at": "detected_at", "title": "name", "source_url": "url"}
        mapped = {}
        for k, v in updates.items():
            new_key = _rename.get(k, k)
            if new_key is not None:
                mapped[new_key] = v
        if mapped:
            await db.table("tools").update(mapped).eq("id", tool_id).execute()
    except Exception as e:
        logger.error(f"update_tool({tool_id}): {e}")


async def mark_tool_processed(tool_id: str) -> None:
    """Mark tool as processed-but-invalid so it won't be re-queued."""
    await update_tool(tool_id, {"is_valid": False})


# ── Alerts ────────────────────────────────────────────────────────────────────

async def create_alert(
    telegram_id: int, tool_id: str, score: int, reason: str, alert_type: str
) -> dict:
    db = await get_client()
    user = await get_user(telegram_id)
    if not user:
        raise ValueError(f"User not found: {telegram_id}")

    is_generic = alert_type == "generic"
    urgency = "immediate" if alert_type == "priority" else ("batch" if alert_type == "standard" else "suppress")

    result = await db.table("alerts").insert({
        "user_id": user["id"],
        "tool_id": tool_id,
        "score": score,
        "reason": reason,
        "is_generic": is_generic,
        "urgency": urgency,
        "status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    # Update daily alert count on the user row
    today = date.today().isoformat()
    if user.get("last_alert_date") == today:
        await db.table("users").update(
            {"daily_alert_count": (user.get("daily_alert_count") or 0) + 1}
        ).eq("id", user["id"]).execute()
    else:
        await db.table("users").update(
            {"daily_alert_count": 1, "last_alert_date": today}
        ).eq("id", user["id"]).execute()

    return result.data[0]


async def get_user_alert_count_today(telegram_id: int) -> int:
    user = await get_user(telegram_id)
    if not user:
        return 0
    today = date.today().isoformat()
    if user.get("last_alert_date") == today:
        return user.get("daily_alert_count") or 0
    return 0


async def has_user_received_alert_for_tool(telegram_id: int, tool_id: str) -> bool:
    db = await get_client()
    try:
        user = await get_user(telegram_id)
        if not user:
            return False
        result = (
            await db.table("alerts")
            .select("id")
            .eq("user_id", user["id"])
            .eq("tool_id", tool_id)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.error(f"has_user_received_alert_for_tool: {e}")
        return False


async def get_alert_by_id(alert_id: str) -> Optional[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("alerts")
            .select("*, tools(*)")
            .eq("id", alert_id)
            .maybe_single()
            .execute()
        )
        return _ms(result)
    except Exception as e:
        logger.error(f"get_alert_by_id({alert_id}): {e}")
        return None


# ── Alert Feedback ────────────────────────────────────────────────────────────

async def record_feedback(alert_id: str, telegram_id: int, feedback: str) -> None:
    db = await get_client()
    try:
        user = await get_user(telegram_id)
        if not user:
            return
        await db.table("alert_feedback").upsert(
            {
                "alert_id": alert_id,
                "user_id": user["id"],
                "feedback": feedback,
            },
            on_conflict="alert_id,user_id",
        ).execute()
    except Exception as e:
        logger.error(f"record_feedback({alert_id}): {e}")


# ── Referrals ─────────────────────────────────────────────────────────────────

async def get_pending_referral(referred_uuid: str) -> Optional[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("referrals")
            .select("*")
            .eq("referred_id", referred_uuid)
            .eq("status", "pending")
            .maybe_single()
            .execute()
        )
        return _ms(result)
    except Exception as e:
        logger.error(f"get_pending_referral({referred_uuid}): {e}")
        return None


async def convert_referral(referred_telegram_id: int) -> Optional[int]:
    """Mark referral converted; return referrer's telegram_id or None."""
    db = await get_client()
    referred = await get_user(referred_telegram_id)
    if not referred:
        return None

    referral = await get_pending_referral(referred["id"])
    if not referral:
        return None

    try:
        await db.table("referrals").update({
            "status": "converted",
            "converted_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", referral["id"]).execute()

        # Look up referrer by their uuid
        referrer_result = (
            await db.table("users")
            .select("telegram_id")
            .eq("id", referral["referrer_id"])
            .maybe_single()
            .execute()
        )
        referrer = _ms(referrer_result)
        return referrer["telegram_id"] if referrer else None
    except Exception as e:
        logger.error(f"convert_referral({referred_telegram_id}): {e}")
        return None


async def get_user_referral_count(telegram_id: int) -> int:
    db = await get_client()
    try:
        user = await get_user(telegram_id)
        if not user:
            return 0
        result = (
            await db.table("referrals")
            .select("id")
            .eq("referrer_id", user["id"])
            .eq("status", "converted")
            .execute()
        )
        return len(result.data or [])
    except Exception as e:
        logger.error(f"get_user_referral_count({telegram_id}): {e}")
        return 0


# ── Webhook Events ────────────────────────────────────────────────────────────

async def record_webhook_event(event_type: str, payload: dict) -> None:
    db = await get_client()
    try:
        await db.table("webhook_events").insert({
            "source": "invitemember",
            "event_type": event_type,
            "payload": payload,
            "processed": True,
        }).execute()
    except Exception as e:
        logger.error(f"record_webhook_event({event_type}): {e}")


# ── Daily Stats ───────────────────────────────────────────────────────────────

_STAT_COLUMNS = {
    "tools_ingested": "tools_ingested",
    "tools_valid": "tools_valid",
    "alerts_sent": "alerts_sent",
    "useful_feedback": "useful_feedback",
    "not_relevant_feedback": "not_relevant_feedback",
    "new_trials": "new_trials",
    "new_paid": "new_paid",
    "churned": "churned",
}


async def increment_daily_stat(stat_key: str, amount: int = 1) -> None:
    col = _STAT_COLUMNS.get(stat_key)
    if not col:
        return
    db = await get_client()
    today = date.today().isoformat()
    try:
        result = (
            await db.table("daily_stats")
            .select(f"id,{col}")
            .eq("date", today)
            .maybe_single()
            .execute()
        )
        row = _ms(result)
        if row:
            await db.table("daily_stats").update(
                {col: (row.get(col) or 0) + amount}
            ).eq("id", row["id"]).execute()
        else:
            await db.table("daily_stats").insert({"date": today, col: amount}).execute()
    except Exception as e:
        logger.error(f"increment_daily_stat({stat_key}): {e}")


# ── User Stats ────────────────────────────────────────────────────────────────

async def get_user_stats(telegram_id: int) -> dict:
    db = await get_client()
    try:
        user = await get_user(telegram_id)
        if not user:
            return {"total_alerts": 0, "useful": 0, "not_relevant": 0}
        uid = user["id"]

        alert_result = await db.table("alerts").select("id").eq("user_id", uid).execute()
        total_alerts = len(alert_result.data or [])

        fb_result = await db.table("alert_feedback").select("feedback").eq("user_id", uid).execute()
        rows = fb_result.data or []
        useful = sum(1 for r in rows if r["feedback"] == "useful")
        not_relevant = sum(1 for r in rows if r["feedback"] == "not_relevant")

        return {"total_alerts": total_alerts, "useful": useful, "not_relevant": not_relevant}
    except Exception as e:
        logger.error(f"get_user_stats({telegram_id}): {e}")
        return {"total_alerts": 0, "useful": 0, "not_relevant": 0}
