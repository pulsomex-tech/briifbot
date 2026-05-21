import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from config.settings import OPENAI_API_KEY, PAID_ALERTS_PER_DAY, FREE_ALERTS_PER_DAY
from db.client import get_category_weights, get_user_alert_count_today, get_user_profile, has_user_received_alert_for_tool

logger = logging.getLogger(__name__)
_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)

_SYSTEM_PROMPT = """\
You are a relevance scoring expert. Score how useful this newly launched tool is for the specific user.

Respond ONLY with a JSON object:
- "score": integer 0-100
  90-100: Must-have; directly addresses the user's primary stack/workflow
  70-89: Very relevant, worth knowing about
  0-69: Not relevant enough for this user
- "reason": 1-2 sentences explaining relevance to THIS user specifically
- "urgency": one of "immediate" (≥90) | "batch" (70-89) | "suppress" (<70)\
"""


async def score_tool_for_user(
    tool: dict,
    user: dict,
    profile: Optional[dict] = None,
    weights: Optional[dict] = None,
) -> Optional[dict]:
    telegram_id = user["telegram_id"]
    status = user.get("status", "free")

    if status == "free":
        return None  # free users get generic alerts only

    if profile is None:
        profile = await get_user_profile(telegram_id)
    if not profile:
        return None

    if weights is None:
        weights = await get_category_weights(telegram_id)

    max_alerts = PAID_ALERTS_PER_DAY if status == "paid" else FREE_ALERTS_PER_DAY
    if await get_user_alert_count_today(telegram_id) >= max_alerts:
        return None

    if await has_user_received_alert_for_tool(telegram_id, tool["id"]):
        return None

    tool_cats: list[str] = tool.get("categories") or []
    user_cats: list[str] = profile.get("categories") or []
    if tool_cats and user_cats and not (set(tool_cats) & set(user_cats)):
        return None

    weight_context = ""
    if weights:
        top = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:5]
        weight_context = f"\nCategory weights (higher = more important): {dict(top)}"

    tech_stack = profile.get("tech_stack") or []
    stack_str = ", ".join(tech_stack) if isinstance(tech_stack, list) else str(tech_stack)

    user_ctx = (
        f"Role: {profile.get('work_type', 'Unknown')}\n"
        f"Tech stack: {stack_str}\n"
        f"Interested categories: {', '.join(user_cats)}"
        f"{weight_context}"
    )
    tool_name = tool.get("name") or tool.get("title", "")
    tool_url = tool.get("url") or tool.get("source_url", "")
    tool_ctx = (
        f"Tool: {tool_name}\n"
        f"Description: {(tool.get('description') or '')[:400]}\n"
        f"Categories: {', '.join(tool_cats)}\n"
        f"Tags: {', '.join(tool.get('tags') or [])}\n"
        f"Source: {tool.get('source', '')}"
    )

    try:
        response = await _openai.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"USER:\n{user_ctx}\n\nTOOL:\n{tool_ctx}"},
            ],
            response_format={"type": "json_object"},
            max_tokens=200,
            temperature=0,
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"score_tool_for_user({telegram_id}, {tool.get('id')}): {e}")
        return None
