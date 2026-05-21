import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import feedparser
from bs4 import BeautifulSoup

from config.settings import GITHUB_TRENDING_URL, PRODUCT_HUNT_API_KEY, RSS_FEEDS
from db.client import create_tool, get_tool_by_url, increment_daily_stat

logger = logging.getLogger(__name__)

AI_KEYWORDS = {
    "ai", "llm", "gpt", "neural", "ml", "machine learning", "deep learning",
    "transformer", "embedding", "inference", "diffusion", "stable", "mistral",
    "claude", "gemini", "anthropic", "openai", "hugging", "langchain", "rag",
    "vector", "generative", "computer vision", "nlp", "copilot",
}

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Briifbot/1.0; +https://t.me/getbriifbot)"}


async def _fetch_rss(session: aiohttp.ClientSession, feed: dict) -> list[dict]:
    items: list[dict] = []
    try:
        async with session.get(feed["url"], timeout=_TIMEOUT, headers=_HEADERS) as resp:
            content = await resp.text()
        parsed = feedparser.parse(content)
        for entry in parsed.entries[:25]:
            url = entry.get("link", "").strip()
            if not url:
                continue
            items.append({
                "name": entry.get("title", "").strip(),
                "description": (entry.get("summary", "") or "")[:600],
                "url": url,
                "source": feed["name"],
                "categories": [],
                "tags": [],
            })
    except Exception as e:
        logger.error(f"RSS fetch failed [{feed['name']}]: {e}")
    return items


async def _fetch_github_trending(session: aiohttp.ClientSession) -> list[dict]:
    items: list[dict] = []
    try:
        async with session.get(GITHUB_TRENDING_URL, timeout=_TIMEOUT, headers=_HEADERS) as resp:
            content = await resp.text()
        soup = BeautifulSoup(content, "lxml")
        for repo in soup.select("article.Box-row")[:30]:
            name_tag = repo.select_one("h2 a")
            if not name_tag:
                continue
            href = name_tag.get("href", "").strip("/")
            if not href:
                continue
            url = f"https://github.com/{href}"
            desc_tag = repo.select_one("p")
            description = desc_tag.get_text(strip=True) if desc_tag else ""
            combined = (href + " " + description).lower()
            if not any(kw in combined for kw in AI_KEYWORDS):
                continue
            items.append({
                "name": href,
                "description": description[:600],
                "url": url,
                "source": "GitHub Trending",
                "categories": [],
                "tags": [],
            })
    except Exception as e:
        logger.error(f"GitHub trending fetch failed: {e}")
    return items


async def _fetch_product_hunt(session: aiohttp.ClientSession) -> list[dict]:
    if not PRODUCT_HUNT_API_KEY:
        return []
    items: list[dict] = []
    query = """
    {
      posts(first: 20, order: VOTES) {
        edges {
          node {
            id name tagline url
            topics { edges { node { name } } }
          }
        }
      }
    }
    """
    try:
        headers = {
            **_HEADERS,
            "Authorization": f"Bearer {PRODUCT_HUNT_API_KEY}",
            "Content-Type": "application/json",
        }
        async with session.post(
            "https://api.producthunt.com/v2/api/graphql",
            json={"query": query},
            headers=headers,
            timeout=_TIMEOUT,
        ) as resp:
            data = await resp.json()
        ai_topics = {"artificial intelligence", "machine learning", "developer tools", "productivity", "automation", "design tools"}
        for edge in data.get("data", {}).get("posts", {}).get("edges", []):
            node = edge["node"]
            topics = {t["node"]["name"].lower() for t in node.get("topics", {}).get("edges", [])}
            if not ai_topics & topics:
                continue
            items.append({
                "name": node.get("name", ""),
                "description": (node.get("tagline", "") or "")[:600],
                "url": node.get("url", ""),
                "source": "Product Hunt",
                "categories": [],
                "tags": [],
            })
    except Exception as e:
        logger.error(f"Product Hunt fetch failed: {e}")
    return items


async def ingest_all() -> int:
    new_count = 0
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_rss(session, feed) for feed in RSS_FEEDS]
        tasks.append(_fetch_github_trending(session))
        tasks.append(_fetch_product_hunt(session))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)

    seen_urls: set[str] = set()
    for item in all_items:
        url = item.get("url", "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        if await get_tool_by_url(url):
            continue

        try:
            await create_tool(item)
            new_count += 1
        except Exception as e:
            logger.error(f"Failed to store tool [{url}]: {e}")

    if new_count:
        await increment_daily_stat("tools_ingested", new_count)
    logger.info(f"Ingestion complete — {new_count} new tools stored")
    return new_count
