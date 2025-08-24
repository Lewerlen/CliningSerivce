import asyncio
import logging
import os
from typing import Callable, Dict, Any, Awaitable

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.config import load_config
from app.handlers import admin, client, executor
from app.database.models import Base
from app.scheduler import check_and_send_reminders

class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_pool: sessionmaker):
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with self.session_pool() as session:
            data["session"] = session
            return await handler(event, data)


# --- НОВЫЙ БЛОК ДЛЯ УНИВЕРСАЛЬНОГО ЛОГИРОВАНИЯ ---

class ContextFilter(logging.Filter):
    """
    Это фильтр, который добавляет в логи данные о пользователе,
    если они есть, или значения по умолчанию, если их нет.
    """

    def filter(self, record):
        record.username = getattr(record, 'username', 'System')
        record.user_id = getattr(record, 'user_id', 'System')
        return True


# Настраиваем основное логирование без defaults
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - @%(username)s (%(user_id)s) - %(message)s"
)

# Добавляем наш фильтр ко всем логгерам
for handler in logging.root.handlers:
    handler.addFilter(ContextFilter())

# --- КОНЕЦ НОВОГО БЛОКА ---

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

async def main():
    config = load_config()
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        logging.error("Не найдена переменная окружения DATABASE_URL")
        return

    engine = create_async_engine(DATABASE_URL, echo=False)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    client_bot = Bot(token=config.bots.client_bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    executor_bot = Bot(token=config.bots.executor_bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    admin_bot = Bot(token=config.bots.admin_bot_token, default=DefaultBotProperties(parse_mode="HTML"))

    client_dp = Dispatcher()
    executor_dp = Dispatcher()
    admin_dp = Dispatcher()

    client_dp.update.middleware(DbSessionMiddleware(session_pool=session_maker))
    executor_dp.update.middleware(DbSessionMiddleware(session_pool=session_maker))
    admin_dp.update.middleware(DbSessionMiddleware(session_pool=session_maker))

    bots = {"client": client_bot, "executor": executor_bot, "admin": admin_bot}
    client_dp["bots"] = bots
    executor_dp["bots"] = bots
    admin_dp["bots"] = bots
    client_dp["config"] = config
    executor_dp["config"] = config
    admin_dp["config"] = config

    client_dp.include_router(client.router)
    executor_dp.include_router(executor.router)
    admin_dp.include_router(admin.router)

    scheduler = AsyncIOScheduler(timezone="Asia/Yekaterinburg")
    scheduler.add_job(
        check_and_send_reminders,
        trigger="interval",
        seconds=60,  # Проверять каждую минуту
        kwargs={"bot": client_bot, "session_pool": session_maker, "admin_id": config.admin_id}
    )
    scheduler.start()

    try:
        await asyncio.gather(
            client_dp.start_polling(client_bot),
            executor_dp.start_polling(executor_bot),
            admin_dp.start_polling(admin_bot),
        )
    finally:
        scheduler.shutdown()
        await client_bot.session.close()
        await executor_bot.session.close()
        await admin_bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Боты остановлены")