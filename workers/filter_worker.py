import json
import logging

from openai import AsyncOpenAI

from config.settings import OPENAI_API_KEY
from db.client import get_unprocessed_tools, mark_tool_processed, update_tool, increment_daily_stat

logger = logging.getLogger(__name__)
_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)

_SYSTEM_PROMPT = """\
You are a tool classification expert. Given a title and description of a web item, decide if it is a real, newly launched AI or tech tool/product.

Respond ONLY with a JSON object containing:
- "is_tool": boolean — true only for real new product/tool launches
- "categories": array of strings from exactly this list:
  ["AI Models","Automation","Video/Image AI","Dev Tools","Marketing","Sales","Analytics","No-code","Voice/Audio","Productivity"]
  (include 1-3 most relevant; empty array if is_tool is false)
- "tags": array of 2-5 short keyword strings (empty if is_tool is false)

Exclude: blog posts, tutorials, opinion pieces, job listings, funding news, or general tech news.\
"""

# ── Competitor guard ──────────────────────────────────────────────────────────

BRIIFBOT_COMPETITOR_CATEGORIES = {
    "tool discovery",
    "ai tool monitoring",
    "tool tracking",
    "tool alerts",
    "newsletter aggregation",
    "product launch monitoring",
    "tech radar",
    "ai newsletter",
    "tool curation",
    "launch tracker",
    "producthunt alternative",
    "saas discovery",
}

BRIIFBOT_COMPETITOR_KEYWORDS = [
    "discover new tools",
    "track new ai tools",
    "ai tool alerts",
    "tool launches",
    "product hunt",
    "new saas tools",
    "tool recommendations",
    "ai newsletter",
    "tool monitoring",
    "launch notifications",
    "tool aggregator",
    "ai radar",
    "tool digest",
    "stack discovery",
    "tool finder",
    "new tools alert",
    "software discovery",
]


def is_briifbot_competitor(tool: dict, categories: list[str]) -> bool:
    """True if the tool competes with or could replace Briifbot."""
    assigned = {c.lower() for c in categories}
    if assigned & BRIIFBOT_COMPETITOR_CATEGORIES:
        return True
    text = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
    return any(kw in text for kw in BRIIFBOT_COMPETITOR_KEYWORDS)


# ── Main filter loop ──────────────────────────────────────────────────────────

async def filter_tools() -> int:
    tools = await get_unprocessed_tools()
    if not tools:
        return 0

    processed = 0
    valid = 0
    for tool in tools:
        try:
            name = tool.get("name") or tool.get("title", "")
            content = f"Title: {name}\nDescription: {(tool.get('description') or '')[:400]}"
            response = await _openai.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                response_format={"type": "json_object"},
                max_tokens=200,
                temperature=0,
            )
            result = json.loads(response.choices[0].message.content)
            categories = result.get("categories", [])
            is_valid = bool(result.get("is_tool", False))

            # Competitor guard — runs after LLM classification
            if is_valid and is_briifbot_competitor(tool, categories):
                is_valid = False
                logger.info(f"BLOCKED (competitor): {name} → {categories}")

            await update_tool(tool["id"], {
                "is_valid": is_valid,
                "categories": categories,
                "tags": result.get("tags", []),
            })
            processed += 1
            if is_valid:
                valid += 1
        except Exception as e:
            logger.error(f"filter_worker error on tool {tool.get('id')}: {e}")
            await mark_tool_processed(tool["id"])

    if valid:
        await increment_daily_stat("tools_valid", valid)
    logger.info(f"Filtered {processed} tools ({valid} valid)")
    return processed
