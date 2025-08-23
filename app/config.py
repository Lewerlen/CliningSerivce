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
class ApiKeys:
    """Хранит ключи для внешних API."""
    yandex_api_key: str

@dataclass
class Settings:
    """Класс для хранения всех настроек."""
    bots: Bots
    api_keys: ApiKeys
    admin_id: int

def load_config(path: str = None):
    """
    Загружает конфигурацию из .env файла.
    Ищет .env в корне проекта, на один уровень выше, чем текущий файл.
    """
    # Если путь не указан, формируем путь к .env в корне проекта
    if path is None:
        path = os.path.join(os.path.dirname(__file__), '..', '.env')

    load_dotenv(path)

    admin_id_str = os.getenv("ADMIN_ID")
    if not admin_id_str:
        raise ValueError("Переменная ADMIN_ID не найдена в файле .env")

    return Settings(
        bots=Bots(
            client_bot_token=os.getenv("CLIENT_BOT_TOKEN"),
            executor_bot_token=os.getenv("EXECUTOR_BOT_TOKEN"),
            admin_bot_token=os.getenv("ADMIN_BOT_TOKEN"),
        ),
        api_keys=ApiKeys(
            yandex_api_key=os.getenv("YANDEX_API_KEY")
        ),
        admin_id=int(admin_id_str)
    )