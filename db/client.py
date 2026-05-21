import logging
import secrets
import string
from datetime import datetime, timezone, timedelta
from typing import Optional

from supabase import create_async_client, AsyncClient

from config.settings import (
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
    REFERRAL_CODE_LENGTH,
    TRIAL_DAYS,
)

logger = logging.getLogger(__name__)

_client: Optional[AsyncClient] = None


async def get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


def _generate_referral_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(REFERRAL_CODE_LENGTH))


# ── Users ────────────────────────────────────────────────────────────────────

async def get_user(telegram_id: int) -> Optional[dict]:
    db = await get_client()
    try:
        result = await db.table("users").select("*").eq("telegram_id", telegram_id).maybe_single().execute()
        return result.data
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
    code = _generate_referral_code()
    trial_ends_at = (datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)).isoformat()

    result = await db.table("users").insert({
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "referral_code": code,
        "subscription_status": "trial",
        "trial_ends_at": trial_ends_at,
        "alerts_paused": False,
        "is_active": True,
    }).execute()
    user = result.data[0]

    if referred_by:
        await record_referral(referred_by, telegram_id)

    return user


async def update_user(telegram_id: int, updates: dict) -> Optional[dict]:
    db = await get_client()
    try:
        result = await db.table("users").update(updates).eq("telegram_id", telegram_id).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"update_user({telegram_id}): {e}")
        return None


async def get_all_active_users() -> list[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("users")
            .select("*")
            .in_("subscription_status", ["trial", "paid"])
            .eq("is_active", True)
            .eq("alerts_paused", False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_all_active_users: {e}")
        return []


async def get_all_active_paid_users() -> list[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("users")
            .select("*")
            .eq("subscription_status", "paid")
            .eq("is_active", True)
            .eq("alerts_paused", False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_all_active_paid_users: {e}")
        return []


async def get_trial_users() -> list[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("users")
            .select("*")
            .eq("subscription_status", "trial")
            .eq("is_active", True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_trial_users: {e}")
        return []


async def get_free_users() -> list[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("users")
            .select("*")
            .eq("subscription_status", "free")
            .eq("is_active", True)
            .eq("alerts_paused", False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_free_users: {e}")
        return []


async def get_expired_trials() -> list[dict]:
    db = await get_client()
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = (
            await db.table("users")
            .select("*")
            .eq("subscription_status", "trial")
            .lt("trial_ends_at", now)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_expired_trials: {e}")
        return []


async def get_user_by_referral_code(code: str) -> Optional[dict]:
    db = await get_client()
    try:
        result = await db.table("users").select("*").eq("referral_code", code).maybe_single().execute()
        return result.data
    except Exception as e:
        logger.error(f"get_user_by_referral_code({code}): {e}")
        return None


# ── User Profiles ─────────────────────────────────────────────────────────────

async def get_user_profile(telegram_id: int) -> Optional[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("user_profiles")
            .select("*")
            .eq("telegram_id", telegram_id)
            .maybe_single()
            .execute()
        )
        return result.data
    except Exception as e:
        logger.error(f"get_user_profile({telegram_id}): {e}")
        return None


async def upsert_user_profile(
    telegram_id: int, role: str, stack: str, categories: list[str]
) -> dict:
    db = await get_client()
    result = await db.table("user_profiles").upsert(
        {
            "telegram_id": telegram_id,
            "role": role,
            "stack": stack,
            "categories": categories,
        },
        on_conflict="telegram_id",
    ).execute()
    return result.data[0]


# ── Category Weights ──────────────────────────────────────────────────────────

async def get_category_weights(telegram_id: int) -> dict:
    db = await get_client()
    try:
        result = (
            await db.table("category_weights")
            .select("category,weight")
            .eq("telegram_id", telegram_id)
            .execute()
        )
        return {row["category"]: row["weight"] for row in (result.data or [])}
    except Exception as e:
        logger.error(f"get_category_weights({telegram_id}): {e}")
        return {}


async def update_category_weight(telegram_id: int, category: str, delta: float) -> None:
    db = await get_client()
    try:
        result = (
            await db.table("category_weights")
            .select("id,weight")
            .eq("telegram_id", telegram_id)
            .eq("category", category)
            .maybe_single()
            .execute()
        )
        if result.data:
            new_weight = max(0.1, min(3.0, result.data["weight"] + delta))
            await db.table("category_weights").update({"weight": new_weight}).eq("id", result.data["id"]).execute()
        else:
            initial = max(0.1, min(3.0, 1.0 + delta))
            await db.table("category_weights").insert({
                "telegram_id": telegram_id,
                "category": category,
                "weight": initial,
            }).execute()
    except Exception as e:
        logger.error(f"update_category_weight({telegram_id}, {category}): {e}")


async def initialize_category_weights(telegram_id: int, categories: list[str]) -> None:
    db = await get_client()
    try:
        rows = [{"telegram_id": telegram_id, "category": cat, "weight": 1.0} for cat in categories]
        if rows:
            await db.table("category_weights").upsert(rows, on_conflict="telegram_id,category").execute()
    except Exception as e:
        logger.error(f"initialize_category_weights({telegram_id}): {e}")


# ── Tools ─────────────────────────────────────────────────────────────────────

async def get_tool_by_url(url: str) -> Optional[dict]:
    db = await get_client()
    try:
        result = await db.table("tools").select("id").eq("source_url", url).maybe_single().execute()
        return result.data
    except Exception as e:
        logger.error(f"get_tool_by_url: {e}")
        return None


async def create_tool(tool_data: dict) -> dict:
    db = await get_client()
    result = await db.table("tools").insert(tool_data).execute()
    return result.data[0]


async def get_unprocessed_tools() -> list[dict]:
    db = await get_client()
    try:
        result = await db.table("tools").select("*").eq("is_processed", False).execute()
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
            .eq("is_tool", True)
            .gte("published_at", cutoff)
            .order("published_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"get_recent_confirmed_tools: {e}")
        return []


async def update_tool(tool_id: str, updates: dict) -> None:
    db = await get_client()
    try:
        await db.table("tools").update(updates).eq("id", tool_id).execute()
    except Exception as e:
        logger.error(f"update_tool({tool_id}): {e}")


async def mark_tool_processed(tool_id: str) -> None:
    await update_tool(tool_id, {"is_processed": True})


# ── Alerts ────────────────────────────────────────────────────────────────────

async def create_alert(
    telegram_id: int, tool_id: str, score: int, reason: str, alert_type: str
) -> dict:
    db = await get_client()
    result = await db.table("alerts").insert({
        "telegram_id": telegram_id,
        "tool_id": tool_id,
        "score": score,
        "reason": reason,
        "alert_type": alert_type,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    return result.data[0]


async def get_user_alert_count_today(telegram_id: int) -> int:
    db = await get_client()
    try:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        result = (
            await db.table("alerts")
            .select("id", count="exact")
            .eq("telegram_id", telegram_id)
            .gte("sent_at", today_start)
            .execute()
        )
        return result.count or 0
    except Exception as e:
        logger.error(f"get_user_alert_count_today({telegram_id}): {e}")
        return 0


async def has_user_received_alert_for_tool(telegram_id: int, tool_id: str) -> bool:
    db = await get_client()
    try:
        result = (
            await db.table("alerts")
            .select("id")
            .eq("telegram_id", telegram_id)
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
        return result.data
    except Exception as e:
        logger.error(f"get_alert_by_id({alert_id}): {e}")
        return None


# ── Alert Feedback ────────────────────────────────────────────────────────────

async def record_feedback(alert_id: str, telegram_id: int, feedback: str) -> None:
    db = await get_client()
    try:
        await db.table("alert_feedback").upsert(
            {
                "alert_id": alert_id,
                "telegram_id": telegram_id,
                "feedback": feedback,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="alert_id,telegram_id",
        ).execute()
    except Exception as e:
        logger.error(f"record_feedback({alert_id}): {e}")


# ── Referrals ─────────────────────────────────────────────────────────────────

async def record_referral(referrer_code: str, referred_telegram_id: int) -> None:
    db = await get_client()
    try:
        referrer = await get_user_by_referral_code(referrer_code)
        if not referrer:
            return
        await db.table("referrals").insert({
            "referrer_telegram_id": referrer["telegram_id"],
            "referred_telegram_id": referred_telegram_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f"record_referral({referrer_code}): {e}")


async def get_pending_referral(referred_telegram_id: int) -> Optional[dict]:
    db = await get_client()
    try:
        result = (
            await db.table("referrals")
            .select("*")
            .eq("referred_telegram_id", referred_telegram_id)
            .eq("status", "pending")
            .maybe_single()
            .execute()
        )
        return result.data
    except Exception as e:
        logger.error(f"get_pending_referral({referred_telegram_id}): {e}")
        return None


async def convert_referral(referred_telegram_id: int) -> Optional[int]:
    db = await get_client()
    referral = await get_pending_referral(referred_telegram_id)
    if not referral:
        return None
    try:
        await db.table("referrals").update({
            "status": "converted",
            "converted_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", referral["id"]).execute()
        return referral["referrer_telegram_id"]
    except Exception as e:
        logger.error(f"convert_referral({referred_telegram_id}): {e}")
        return None


async def get_user_referral_count(telegram_id: int) -> int:
    db = await get_client()
    try:
        result = (
            await db.table("referrals")
            .select("id", count="exact")
            .eq("referrer_telegram_id", telegram_id)
            .eq("status", "converted")
            .execute()
        )
        return result.count or 0
    except Exception as e:
        logger.error(f"get_user_referral_count({telegram_id}): {e}")
        return 0


# ── Webhook Events ────────────────────────────────────────────────────────────

async def record_webhook_event(event_type: str, payload: dict) -> None:
    db = await get_client()
    try:
        await db.table("webhook_events").insert({
            "event_type": event_type,
            "payload": payload,
            "received_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f"record_webhook_event({event_type}): {e}")


# ── Daily Stats ───────────────────────────────────────────────────────────────

async def increment_daily_stat(stat_key: str, amount: int = 1) -> None:
    db = await get_client()
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        result = (
            await db.table("daily_stats")
            .select("id,value")
            .eq("date", today)
            .eq("stat_key", stat_key)
            .maybe_single()
            .execute()
        )
        if result.data:
            await db.table("daily_stats").update(
                {"value": result.data["value"] + amount}
            ).eq("id", result.data["id"]).execute()
        else:
            await db.table("daily_stats").insert(
                {"date": today, "stat_key": stat_key, "value": amount}
            ).execute()
    except Exception as e:
        logger.error(f"increment_daily_stat({stat_key}): {e}")


async def get_user_stats(telegram_id: int) -> dict:
    db = await get_client()
    try:
        alert_result = (
            await db.table("alerts")
            .select("id", count="exact")
            .eq("telegram_id", telegram_id)
            .execute()
        )
        total_alerts = alert_result.count or 0

        fb_result = (
            await db.table("alert_feedback")
            .select("feedback")
            .eq("telegram_id", telegram_id)
            .execute()
        )
        rows = fb_result.data or []
        useful = sum(1 for r in rows if r["feedback"] == "useful")
        not_relevant = sum(1 for r in rows if r["feedback"] == "not_relevant")

        return {"total_alerts": total_alerts, "useful": useful, "not_relevant": not_relevant}
    except Exception as e:
        logger.error(f"get_user_stats({telegram_id}): {e}")
        return {"total_alerts": 0, "useful": 0, "not_relevant": 0}
