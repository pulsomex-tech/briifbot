"""
Supabase-backed aiogram FSM storage.

State + data are serialised as JSON into user_profiles.raw_onboarding_response.
A stub user_profiles row is created (with nulls for not-yet-collected fields)
the moment a new user hits /start so that state can be persisted immediately.

StorageKey.user_id == Telegram user ID (private chats: chat_id == user_id).
"""
import json
import logging
from typing import Any, Dict, Optional

from aiogram.fsm.storage.base import BaseStorage, StorageKey

logger = logging.getLogger(__name__)

_EMPTY = json.dumps({"fsm_state": None, "fsm_data": {}})


class SupabaseStorage(BaseStorage):
    # ── internal helpers ──────────────────────────────────────────────────────

    async def _get_db(self):
        from db.client import get_client
        return await get_client()

    async def _get_user_uuid(self, telegram_id: int) -> Optional[str]:
        from db.client import get_user
        user = await get_user(telegram_id)
        return user["id"] if user else None

    async def _load_raw(self, telegram_id: int) -> dict:
        """Return the parsed JSON from raw_onboarding_response, or empty dict."""
        uid = await self._get_user_uuid(telegram_id)
        if not uid:
            return {}
        try:
            db = await self._get_db()
            result = (
                await db.table("user_profiles")
                .select("id,raw_onboarding_response")
                .eq("user_id", uid)
                .maybe_single()
                .execute()
            )
            row = result.data if result is not None else None
            if not row:
                return {}
            raw = row.get("raw_onboarding_response") or "{}"
            return json.loads(raw)
        except Exception as e:
            logger.error(f"SupabaseStorage._load_raw({telegram_id}): {e}")
            return {}

    async def _save_raw(self, telegram_id: int, payload: dict) -> None:
        uid = await self._get_user_uuid(telegram_id)
        if not uid:
            return
        try:
            db = await self._get_db()
            serialised = json.dumps(payload)
            # Upsert: creates row if missing (stub profile), updates if present
            await db.table("user_profiles").upsert(
                {"user_id": uid, "raw_onboarding_response": serialised},
                on_conflict="user_id",
            ).execute()
        except Exception as e:
            logger.error(f"SupabaseStorage._save_raw({telegram_id}): {e}")

    # ── BaseStorage interface ─────────────────────────────────────────────────

    async def set_state(self, key: StorageKey, state=None) -> None:
        telegram_id = key.user_id
        payload = await self._load_raw(telegram_id)
        payload["fsm_state"] = state.state if state else None
        await self._save_raw(telegram_id, payload)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        telegram_id = key.user_id
        payload = await self._load_raw(telegram_id)
        return payload.get("fsm_state")

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        telegram_id = key.user_id
        payload = await self._load_raw(telegram_id)
        payload["fsm_data"] = data
        await self._save_raw(telegram_id, payload)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        telegram_id = key.user_id
        payload = await self._load_raw(telegram_id)
        return payload.get("fsm_data") or {}

    async def close(self) -> None:
        pass
