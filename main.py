"""Karvon Taxi bot — ishga tushirish nuqtasi."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent, TelegramObject, Update
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import settings
from bot.database.models import async_session_maker, dispose_db, init_db
from bot.handlers.admin import router as admin_router
from bot.handlers.driver import router as driver_router
from bot.handlers.group import router as group_router
from bot.handlers.passenger import router as passenger_router
from bot.relay.admins import admin_cache
from bot.relay.middleware import RelayMiddleware
from bot.relay.service import dedup
from bot.relay.store import load_keywords, warm_dedup
from bot.services.scheduler import setup_scheduler

logger = logging.getLogger(__name__)

_prepared = False
_stopped = False
_SKIP_UPDATES = {"edited_message", "edited_channel_post"}


class KeepAliveSession(AiohttpSession):
    """Bitta ClientSession. force_close yo'q — TCP ulanish qayta ishlatiladi."""

    def __init__(self) -> None:
        super().__init__(limit=100, timeout=20)
        self._connector_init["keepalive_timeout"] = 120
        self._connector_init["force_close"] = False


def _skip_db(event: TelegramObject) -> bool:
    """Oddiy guruh xabari bazani kutmasin. Buyruq va callback sessiyasiz qolmaydi."""
    if not isinstance(event, Update):
        return False
    if event.my_chat_member is not None and event.message is None and event.callback_query is None:
        return True
    message = event.message
    if message is None or message.chat is None:
        return False
    if message.chat.type not in {"group", "supergroup"}:
        return False
    raw = (message.text or message.caption or "").lstrip()
    return not raw.startswith("/")


class DbSessionMiddleware(BaseMiddleware):
    """Har bir update uchun AsyncSession beradi va muvaffaqiyatda commit qiladi."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if _skip_db(event):
            return await handler(event, data)
        async with async_session_maker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


def allowed_updates(dp: Dispatcher) -> list[str]:
    found = list(dp.resolve_used_update_types())
    for name in ("message", "callback_query", "my_chat_member", "chat_member"):
        if name not in found:
            found.append(name)
    return [name for name in found if name not in _SKIP_UPDATES]


async def prepare(bot: Bot, scheduler: AsyncIOScheduler) -> None:
    global _prepared
    if _prepared:
        return
    if settings.is_railway and settings.is_sqlite:
        msg = (
            "Railway'da SQLite ishlatilmoqda — baza har redeployda o'chadi "
            "(haydovchi/foydalanuvchi soni 'nolga' qaytadi).\n"
            "Yechim: Railway Postgres qo'shing va DATABASE_URL ni ulang."
        )
        logger.error(msg)
        try:
            from bot.services.notify import notify_admins

            await notify_admins(bot, f"⛔ Bot ishga tushmadi.\n{msg}")
        except Exception:
            logger.exception("Ishga tushmaslik haqida adminlarga yozilmadi")
        raise SystemExit(1)

    await init_db()
    try:
        await load_keywords()
        await warm_dedup(dedup, settings.dedup_minutes)
        for chat_id in settings.resolved_sweep_group_ids:
            try:
                await admin_cache.refresh(bot, chat_id)
            except Exception:
                logger.exception("Sweep admin kesh yuklanmadi chat=%s", chat_id)
        sources = settings.resolved_source_group_ids
        logger.info(
            "Relay tayyor: main=%s sources=%s sweep=%s dedup_min=%s",
            settings.resolved_main_group_id,
            "ALL" if sources is None else sources,
            sorted(settings.resolved_sweep_group_ids),
            settings.dedup_minutes,
        )
    except Exception:
        logger.exception("Relay holati yuklanmadi, standart kalit so'zlar ishlatiladi")
    if settings.is_sqlite:
        logger.warning(
            "Baza: SQLite (faqat lokal). Production uchun DATABASE_URL (Postgres) kerak."
        )
    else:
        logger.info("Baza: postgresql (doimiy saqlash)")
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Bosh menyu"),
            BotCommand(command="top", description="Haydovchilar reytingi"),
        ]
    )
    await _set_admin_commands(bot)
    if not scheduler.running:
        scheduler.start()
    logger.info("Scheduler ishga tushdi, baza tayyor.")
    me = await bot.get_me()
    logger.info("Bot @%s tayyor", me.username)
    _prepared = True


async def _set_admin_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="admin", description="Admin panel"),
        BotCommand(command="stats", description="Statistika va yo'naltirish"),
        BotCommand(command="groups", description="Ulangan guruhlar"),
        BotCommand(command="addword", description="Kalit so'z qo'shish"),
        BotCommand(command="delword", description="Kalit so'z o'chirish"),
        BotCommand(command="top", description="Haydovchilar reytingi"),
    ]
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            logger.debug("Admin buyruqlari o'rnatilmadi: %s", admin_id, exc_info=True)


async def on_shutdown(scheduler: AsyncIOScheduler) -> None:
    global _stopped
    if _stopped:
        return
    _stopped = True
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await dispose_db()
    logger.info("Bot to'xtatildi.")


async def on_error(event: ErrorEvent) -> None:
    logger.exception("Update ishlovida xato: %s", event.exception)


async def start_health_server() -> None:
    """Cloud'da $PORT ni band qiladi (health check)."""
    port = os.getenv("PORT")
    if not port:
        return

    async def ok(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", ok)
    app.router.add_get("/health", ok)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    logger.info("Health server 0.0.0.0:%s", port)


def _build_bot() -> Bot:
    return Bot(
        token=settings.bot_token,
        session=KeepAliveSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def _install_stop(stop: asyncio.Event) -> None:
    if sys.platform == "win32":
        return
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            return


async def _run_webhook(bot: Bot, dp: Dispatcher, scheduler: AsyncIOScheduler, port: int, base: str) -> None:
    ready = asyncio.Event()
    secret = settings.resolved_webhook_secret()
    path = f"/webhook/{secret}"
    updates = allowed_updates(dp)
    handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        handle_in_background=False,
        secret_token=secret,
    )

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def guarded(request: web.Request) -> web.Response:
        if not ready.is_set():
            try:
                await asyncio.wait_for(ready.wait(), timeout=55)
            except TimeoutError:
                return web.Response(status=503, text="starting")
        return await handler.handle(request)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post(path, guarded)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("HTTP 0.0.0.0:%s", port)
    try:
        await prepare(bot, scheduler)
        ready.set()
        try:
            await bot.set_webhook(
                url=f"{base}{path}",
                allowed_updates=updates,
                drop_pending_updates=False,
                secret_token=secret,
                max_connections=40,
            )
        except Exception:
            logger.exception("Webhook o'rnatilmadi, polling ishlatiladi")
            await bot.delete_webhook(drop_pending_updates=False)
            await dp.start_polling(
                bot,
                allowed_updates=updates,
                polling_timeout=50,
                handle_as_tasks=True,
                close_bot_session=False,
            )
            return
        logger.info("Rejim: webhook %s (polling kutishi yo'q)", base)
        stop = asyncio.Event()
        _install_stop(stop)
        await stop.wait()
    finally:
        if not ready.is_set():
            ready.set()
        await on_shutdown(scheduler)
        await runner.cleanup()


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    await start_health_server()
    updates = allowed_updates(dp)
    logger.info("Rejim: polling timeout=50 updates=%s", ",".join(updates))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(
        bot,
        allowed_updates=updates,
        polling_timeout=50,
        handle_as_tasks=True,
        close_bot_session=False,
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)

    bot = _build_bot()
    dp = Dispatcher(storage=MemoryStorage())
    scheduler = setup_scheduler(bot, async_session_maker)

    # Relay birinchi: yuborish DbSession ochilishidan oldin bajariladi.
    dp.update.outer_middleware(RelayMiddleware())
    dp.update.outer_middleware(DbSessionMiddleware())
    dp.include_router(passenger_router)
    dp.include_router(driver_router)
    dp.include_router(group_router)
    dp.include_router(admin_router)

    dp.startup.register(prepare)
    dp.shutdown.register(on_shutdown)
    dp.errors.register(on_error)
    dp.workflow_data.update(scheduler=scheduler)

    port = os.getenv("PORT")
    base = settings.webhook_base()
    try:
        if port and base:
            await _run_webhook(bot, dp, scheduler, int(port), base)
        else:
            await _run_polling(bot, dp)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
