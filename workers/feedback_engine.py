import logging

from db.client import get_alert_by_id, record_feedback, update_category_weight

logger = logging.getLogger(__name__)

_WEIGHT_DELTA = {"useful": 0.3, "not_relevant": -0.2}


async def process_feedback(alert_id: str, telegram_id: int, feedback: str) -> bool:
    if feedback not in _WEIGHT_DELTA:
        return False

    try:
        await record_feedback(alert_id, telegram_id, feedback)

        alert = await get_alert_by_id(alert_id)
        if not alert:
            return True  # feedback stored, no tool to adjust weights for

        tool = alert.get("tools") or {}
        categories: list[str] = tool.get("categories") or []
        delta = _WEIGHT_DELTA[feedback]

        for category in categories:
            await update_category_weight(telegram_id, category, delta)

        logger.info(f"Feedback '{feedback}' processed — alert {alert_id}, user {telegram_id}")
        return True

    except Exception as e:
        logger.error(f"process_feedback({alert_id}, {telegram_id}): {e}")
        return False
