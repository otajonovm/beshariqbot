"""Karvon Taxi bot — ishga tushirish nuqtasi."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, Awaitable, Callable

from aiohttp import web
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import settings
from bot.database.models import async_session_maker, dispose_db, init_db
from bot.handlers.admin import router as admin_router
from bot.handlers.driver import router as driver_router
from bot.handlers.group import router as group_router
from bot.handlers.passenger import router as passenger_router
from bot.services.scheduler import setup_scheduler

logger = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    """Har bir update uchun AsyncSession beradi va muvaffaqiyatda commit qiladi."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with async_session_maker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


async def on_startup(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    await init_db()
    backend = "sqlite" if settings.is_sqlite else "postgresql"
    if settings.is_sqlite:
        logger.warning(
            "Baza: SQLite (ephemeral). Heroku'da haydovchi ma'lumotlari dyno "
            "qayta ishga tushganda yo'qoladi. DATABASE_URL (Postgres) ulang."
        )
    else:
        logger.info("Baza: %s (doimiy)", backend)
    await bot.set_my_commands(
        [BotCommand(command="start", description="Bosh menyu")]
    )
    scheduler.start()
    logger.info("Scheduler ishga tushdi, baza tayyor.")
    me = await bot.get_me()
    logger.info("Bot @%s pollingni boshlaydi", me.username)


async def on_shutdown(scheduler: AsyncIOScheduler) -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await dispose_db()
    logger.info("Bot to'xtatildi.")


async def on_error(event: ErrorEvent) -> None:
    logger.exception("Update ishlovida xato: %s", event.exception)


async def start_health_server() -> None:
    """Heroku web dyno $PORT ni band qiladi, aks holda R10 timeout bo'ladi."""
    port = os.getenv("PORT")
    if not port:
        return

    async def ok(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    logger.info("Health server 0.0.0.0:%s", port)


async def run_disconnected() -> None:
    """Telegram o'chirilgan: faqat health server (xabar ketmaydi)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.warning(
        "BOT_ENABLED=false — Telegram bot uzilgan. "
        "Polling/scheduler ishlamaydi, hech kimga xabar ketmaydi. "
        "Qayta yoqish: BOT_ENABLED=true"
    )
    await start_health_server()
    # Dyno/jarayon tirik qolsin, lekin Telegramga ulanishmasin
    await asyncio.Event().wait()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)

    if not settings.bot_enabled:
        await run_disconnected()
        return

    if len(settings.bot_token) < 20:
        raise SystemExit("BOT_ENABLED=true, lekin BOT_TOKEN yo'q yoki juda qisqa.")
    if not settings.admin_id:
        raise SystemExit("BOT_ENABLED=true, lekin ADMIN_ID=0. To'g'ri Telegram ID qo'ying.")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    scheduler = setup_scheduler(bot, async_session_maker)

    dp.update.outer_middleware(DbSessionMiddleware())
    dp.include_router(passenger_router)
    dp.include_router(driver_router)
    dp.include_router(group_router)
    dp.include_router(admin_router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    dp.errors.register(on_error)
    dp.workflow_data.update(scheduler=scheduler)

    await start_health_server()
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
