"""
Supabase-backed aiogram FSM storage.

State + data are serialised as JSON into user_profiles.raw_onboarding_response.
Uses SELECT-then-INSERT/UPDATE (not upsert) to avoid relying on a UNIQUE
constraint on user_id that may not exist in the live schema.

StorageKey.user_id == Telegram user ID (private chats: chat_id == user_id).
"""
import json
import logging
from typing import Any, Dict, Optional

from aiogram.fsm.storage.base import BaseStorage, StorageKey

logger = logging.getLogger(__name__)


class SupabaseStorage(BaseStorage):

    async def _get_db(self):
        from db.client import get_client
        return await get_client()

    async def _get_user_uuid(self, telegram_id: int) -> Optional[str]:
        from db.client import get_user
        user = await get_user(telegram_id)
        return user["id"] if user else None

    async def _get_profile_row(self, uid: str) -> Optional[dict]:
        """Return the user_profiles row for this UUID, or None."""
        try:
            db = await self._get_db()
            result = (
                await db.table("user_profiles")
                .select("id,raw_onboarding_response")
                .eq("user_id", uid)
                .maybe_single()
                .execute()
            )
            return result.data if result is not None else None
        except Exception as e:
            logger.error(f"SupabaseStorage._get_profile_row({uid}): {e}")
            return None

    async def _load_raw(self, telegram_id: int) -> dict:
        uid = await self._get_user_uuid(telegram_id)
        if not uid:
            return {}
        row = await self._get_profile_row(uid)
        if not row:
            return {}
        try:
            return json.loads(row.get("raw_onboarding_response") or "{}")
        except Exception:
            return {}

    async def _save_raw(self, telegram_id: int, payload: dict) -> None:
        uid = await self._get_user_uuid(telegram_id)
        if not uid:
            return
        try:
            db = await self._get_db()
            serialised = json.dumps(payload)
            row = await self._get_profile_row(uid)

            if row:
                # Row exists — only touch raw_onboarding_response
                await (
                    db.table("user_profiles")
                    .update({"raw_onboarding_response": serialised})
                    .eq("id", row["id"])
                    .execute()
                )
            else:
                # No profile row yet — insert stub so state can be stored.
                # Use "onboarding" as work_type placeholder to satisfy any
                # NOT NULL constraint; cmd_start completion overwrites it.
                await (
                    db.table("user_profiles")
                    .insert({
                        "user_id": uid,
                        "work_type": "onboarding",
                        "tech_stack": [],
                        "categories": [],
                        "raw_onboarding_response": serialised,
                    })
                    .execute()
                )
        except Exception as e:
            logger.error(f"SupabaseStorage._save_raw({telegram_id}): {e}")

    # ── BaseStorage interface ─────────────────────────────────────────────────

    async def set_state(self, key: StorageKey, state=None) -> None:
        tid = key.user_id
        payload = await self._load_raw(tid)
        payload["fsm_state"] = state.state if state else None
        await self._save_raw(tid, payload)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        payload = await self._load_raw(key.user_id)
        return payload.get("fsm_state")

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        tid = key.user_id
        payload = await self._load_raw(tid)
        payload["fsm_data"] = data
        await self._save_raw(tid, payload)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        payload = await self._load_raw(key.user_id)
        return payload.get("fsm_data") or {}

    async def close(self) -> None:
        pass
