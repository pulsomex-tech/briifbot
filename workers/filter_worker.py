import json
import logging

from openai import AsyncOpenAI

from config.settings import OPENAI_API_KEY
from db.client import (
    get_unprocessed_tools,
    mark_tool_processed,
    update_tool,
    update_tool_market_score,
    increment_daily_stat,
)

logger = logging.getLogger(__name__)
_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ── Prompts ───────────────────────────────────────────────────────────────────

FILTER_PROMPT = """You are a tool classifier for Briifbot, an AI tool discovery service.

WHAT BRIIFBOT IS: A Telegram bot that monitors the internet for newly launched AI and tech tools,
scores them against each user's personal workflow profile, and sends personalized alerts.
Users tell Briifbot their tech stack and roles — Briifbot does the discovery for them.

Determine if this item is a legitimate, newly launched AI or tech tool, product, or service
worth alerting developers and founders about.

Title: {title}
Description: {description}
Source: {source}

Respond ONLY with valid JSON:
{{"is_tool": true/false, "is_competitor": true/false, "categories": ["category1"], "tags": ["tag1", "tag2"]}}

Categories must be from: ["AI Models", "Automation", "Video/Image AI", "Dev Tools", "Marketing",
"Sales", "Analytics", "No-code", "Voice/Audio", "Data", "Security", "Productivity", "Tool Discovery"]

Set is_tool to false if:
- It's a blog post, article, or opinion piece (not a product)
- It's a major company's general news (not a product launch)
- It's already a well-known established product
- It's a tutorial, guide, or course
- It's clearly not AI or tech related

Set is_tool to true if:
- It's a new tool, app, API, library, or service
- It's a new feature launch from an AI company
- It's an open-source project with practical applications

Set is_competitor to true if the tool does ANY of the following:
- Monitors, tracks, or aggregates newly launched AI or tech tools
- Sends alerts or notifications about new software/tools
- Curates or recommends tools based on user profiles or roles
- Operates as a tool discovery platform, bot, or newsletter
- Helps users find, evaluate, or compare software tools
- Functions as a Product Hunt alternative or similar launch aggregator
Use category "Tool Discovery" for any tool where is_competitor is true.
Set is_competitor to false for everything else."""

# ── Competitor guard (keyword fallback on top of LLM) ────────────────────────

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
    assigned = {c.lower() for c in categories}
    if assigned & BRIIFBOT_COMPETITOR_CATEGORIES:
        return True
    text = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
    return any(kw in text for kw in BRIIFBOT_COMPETITOR_KEYWORDS)


# ── Market score (signal-based, no LLM) ──────────────────────────────────────

def calculate_market_score(tool: dict) -> int:
    raw = tool.get("raw_data") or {}
    score = 0

    ph_votes    = raw.get("votes", 0) or raw.get("votesCount", 0)
    ph_comments = raw.get("commentsCount", 0) or raw.get("comments", 0)
    score += int(ph_votes) * 4
    score += int(ph_comments) * 2

    gh_stars = raw.get("stars", 0) or raw.get("stargazers_count", 0)
    score += int(gh_stars) // 10

    trending_rank = raw.get("rank") or raw.get("trending_rank")
    if trending_rank:
        score += max(0, int(100 / max(int(trending_rank), 1)))

    sources_seen = raw.get("sources_count", 1)
    if sources_seen > 1:
        score += sources_seen * 25

    rss_score = raw.get("score", 0) or raw.get("points", 0)
    score += int(rss_score)

    return max(0, score)


# ── LLM filter call ───────────────────────────────────────────────────────────

async def _classify(tool: dict) -> tuple[bool, bool, list[str], list[str]]:
    """Returns (is_valid, is_competitor, categories, tags)."""
    try:
        response = await _openai.chat.completions.create(
            model="gpt-4.1-mini",
            response_format={"type": "json_object"},
            messages=[{
                "role": "user",
                "content": FILTER_PROMPT.format(
                    title=tool.get("name") or tool.get("title", ""),
                    description=(tool.get("description") or "")[:300],
                    source=tool.get("source", ""),
                ),
            }],
            max_tokens=150,
            temperature=0,
        )
        result = json.loads(response.choices[0].message.content)
        return (
            bool(result.get("is_tool", False)),
            bool(result.get("is_competitor", False)),
            result.get("categories", []),
            result.get("tags", []),
        )
    except Exception as e:
        logger.error(f"_classify error for {tool.get('name', '?')}: {e}")
        return False, False, [], []


# ── Main filter loop ──────────────────────────────────────────────────────────

async def filter_tools() -> int:
    tools = await get_unprocessed_tools()
    if not tools:
        return 0

    processed = 0
    valid = 0

    for tool in tools:
        try:
            name = tool.get("name") or tool.get("title", "?")
            is_valid, is_competitor, categories, tags = await _classify(tool)

            # Competitor guard: LLM flag OR keyword heuristic
            if is_valid and (is_competitor or is_briifbot_competitor(tool, categories)):
                is_valid = False
                logger.info(f"BLOCKED (competitor): {name} → {categories}")

            # Core update — is_valid / categories / tags (never includes market_score)
            await update_tool(tool["id"], {
                "is_valid": is_valid,
                "categories": categories or tool.get("categories", []),
                "tags": tags,
            })

            # Market score — isolated update; silently skips if column missing
            if is_valid:
                market_score = calculate_market_score(tool)
                await update_tool_market_score(tool["id"], market_score)

            processed += 1
            if is_valid:
                valid += 1
                logger.info(f"VALID: {name} → {categories}")
            else:
                logger.debug(f"INVALID: {name}")

        except Exception as e:
            logger.error(f"filter_worker error on tool {tool.get('id')}: {e}")
            await mark_tool_processed(tool["id"])

    if valid:
        await increment_daily_stat("tools_valid", valid)
    logger.info(f"Filtered {processed} tools ({valid} valid)")
    return processed
