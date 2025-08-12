# Файл: main.py
import asyncio
import logging
import os
from typing import Callable, Dict, Any, Awaitable

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Импортируем созданный нами конфигуратор и обработчики
from app.config import load_config
from app.handlers import admin, client, executor
from app.database.models import Base

# --- КЛАСС MIDDLEWARE ДЛЯ СЕССИЙ БД (ВМЕСТО ОТДЕЛЬНОГО ФАЙЛА) ---
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
# -------------------------------------------------------------

# Настраиваем логирование для отладки
logging.basicConfig(level=logging.INFO)


async def main():
    # Загружаем конфигурацию с токенами
    config = load_config()

    # ---- БЛОК РАБОТЫ С БАЗОЙ ДАННЫХ ----
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        logging.error("Не найдена переменная окружения DATABASE_URL")
        return

    engine = create_async_engine(DATABASE_URL, echo=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # -------------------------------------

    # Инициализируем ботов и диспетчеры для каждого
    client_bot = Bot(token=config.bots.client_bot_token, parse_mode="HTML")
    executor_bot = Bot(token=config.bots.executor_bot_token, parse_mode="HTML")
    admin_bot = Bot(token=config.bots.admin_bot_token, parse_mode="HTML")

    client_dp = Dispatcher()
    executor_dp = Dispatcher()
    admin_dp = Dispatcher()

    # Регистрируем Middleware для сессий в каждый диспетчер
    client_dp.update.middleware(DbSessionMiddleware(session_pool=session_maker))
    executor_dp.update.middleware(DbSessionMiddleware(session_pool=session_maker))
    admin_dp.update.middleware(DbSessionMiddleware(session_pool=session_maker))

    # Регистрируем "роутеры" с обработчиками для каждого бота
    client_dp.include_router(client.router)
    executor_dp.include_router(executor.router)
    admin_dp.include_router(admin.router)

    # Запускаем всех ботов одновременно
    try:
        await asyncio.gather(
            client_dp.start_polling(client_bot),
            executor_dp.start_polling(executor_bot),
            admin_dp.start_polling(admin_bot),
        )
    finally:
        await client_bot.session.close()
        await executor_bot.session.close()
        await admin_bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Боты остановлены")