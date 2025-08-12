import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Bots:
    """Хранит токены для всех ботов."""
    client_bot_token: str
    executor_bot_token: str
    admin_bot_token: str


@dataclass
class Settings:
    """Класс для хранения всех настроек."""
    bots: Bots


def load_config(path: str = None):
    """Загружает конфигурацию из .env файла."""
    # Если путь не указан, берем .env в корне проекта
    if path is None:
        path = os.path.join(os.path.dirname(__file__), '..', '.env')

    load_dotenv(path)
    return Settings(
        bots=Bots(
            client_bot_token=os.getenv("CLIENT_BOT_TOKEN"),
            executor_bot_token=os.getenv("EXECUTOR_BOT_TOKEN"),
            admin_bot_token=os.getenv("ADMIN_BOT_TOKEN"),
        )
    )