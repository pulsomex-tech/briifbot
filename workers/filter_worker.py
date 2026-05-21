import json
import logging

from openai import AsyncOpenAI

from config.settings import OPENAI_API_KEY
from db.client import get_unprocessed_tools, mark_tool_processed, update_tool

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


async def filter_tools() -> int:
    tools = await get_unprocessed_tools()
    if not tools:
        return 0

    processed = 0
    for tool in tools:
        try:
            content = f"Title: {tool['title']}\nDescription: {(tool.get('description') or '')[:400]}"
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
            await update_tool(tool["id"], {
                "is_tool": bool(result.get("is_tool", False)),
                "categories": result.get("categories", []),
                "tags": result.get("tags", []),
                "is_processed": True,
            })
            processed += 1
        except Exception as e:
            logger.error(f"filter_worker error on tool {tool.get('id')}: {e}")
            await mark_tool_processed(tool["id"])

    logger.info(f"Filtered {processed} tools")
    return processed
