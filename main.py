import asyncio
import logging
import sys

import aiohttp.web as web

from bot import create_bot, create_dispatcher
from config.settings import WEBHOOK_PORT
from webhook_server import create_webhook_app
from workers.scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    bot = await create_bot()
    dp = create_dispatcher()

    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started")

    app = create_webhook_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook server listening on port {WEBHOOK_PORT}")

    logger.info("Bot polling started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Shutting down…")
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
