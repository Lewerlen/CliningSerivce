import asyncio
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message


class AlbumMiddleware(BaseMiddleware):
    """
    Мидлварь для сбора и обработки медиа-групп (альбомов).
    """

    def __init__(self, latency: float = 0.5):
        self.latency = latency
        self.album_tasks: Dict[str, asyncio.Task] = {}

    async def __call__(
            self,
            handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
            event: Message,
            data: Dict[str, Any],
    ) -> Any:
        if not event.media_group_id:
            return await handler(event, data)

        media_group_id = event.media_group_id

        # Если задачи для этой группы еще нет, создаем ее
        if media_group_id not in self.album_tasks:
            # Создаем задачу, которая подождет и вызовет обработчик
            self.album_tasks[media_group_id] = asyncio.create_task(
                self.process_album(handler, event, data)
            )

        # Добавляем сообщение в данные для будущей обработки
        if "album_messages" not in data:
            data["album_messages"] = []
        data["album_messages"].append(event)

    async def process_album(
            self,
            handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
            event: Message,
            data: Dict[str, Any],
    ):
        """
        Ждет завершения "окна" для сбора сообщений и вызывает обработчик.
        """
        await asyncio.sleep(self.latency)

        # Получаем полный список сообщений из данных
        album_messages = data.pop("album_messages", [])

        # Передаем его в основной обработчик
        data["album"] = album_messages
        await handler(event, data)