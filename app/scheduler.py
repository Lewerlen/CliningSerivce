import datetime
from sqlalchemy.future import select
from aiogram import Bot

from app.database.models import Order, OrderStatus, Ticket, TicketStatus
from app.common.texts import RUSSIAN_MONTHS_GENITIVE
from app.handlers.client import TYUMEN_TZ


async def check_and_send_reminders(bots: dict, session_pool, admin_id: int):
    """
    Проверяет заказы и отправляет напоминания за 24 и 2 часа клиентам и исполнителям.
    """
    now_tyumen = datetime.datetime.now(TYUMEN_TZ)
    remind_at_24h_from = now_tyumen + datetime.timedelta(hours=24, minutes=-1)
    remind_at_24h_to = now_tyumen + datetime.timedelta(hours=24)
    remind_at_2h_from = now_tyumen + datetime.timedelta(hours=2, minutes=-1)
    remind_at_2h_to = now_tyumen + datetime.timedelta(hours=2)

    client_bot = bots.get("client")
    executor_bot = bots.get("executor")

    async with session_pool() as session:
        # --- Поиск заказов для 24-часового напоминания ---
        stmt_24h = select(Order).where(
            Order.status.in_([OrderStatus.accepted]),  # Напоминаем только по принятым заказам
            Order.reminder_24h_sent == False
        )
        orders_to_check_24h = await session.execute(stmt_24h)
        for order in orders_to_check_24h.scalars().all():
            try:
                order_time_str = f"{order.selected_date} {order.selected_time.split(' ')[0]}"
                aware_order_datetime = datetime.datetime.strptime(order_time_str, "%Y-%m-%d %H:%M").replace(
                    tzinfo=TYUMEN_TZ)

                if remind_at_24h_from < aware_order_datetime <= remind_at_24h_to:
                    selected_date = datetime.datetime.strptime(order.selected_date, "%Y-%m-%d")
                    formatted_date = f"{selected_date.day} {RUSSIAN_MONTHS_GENITIVE.get(selected_date.month)} {selected_date.year}"

                    # Напоминание клиенту
                    client_text = f"👋 Напоминаем, что завтра, {formatted_date} в {order.selected_time}, у вас запланирована уборка по адресу: {order.address_text}."
                    await client_bot.send_message(chat_id=order.client_tg_id, text=client_text)

                    # Напоминание исполнителю
                    if order.executor_tg_id:
                        executor_text = f"👋 Напоминаем: завтра, {formatted_date} в {order.selected_time}, у вас запланирован заказ №{order.id} по адресу: {order.address_text}."
                        await executor_bot.send_message(chat_id=order.executor_tg_id, text=executor_text)

                    order.reminder_24h_sent = True
            except Exception as e:
                print(f"Ошибка при обработке 24h напоминания для заказа {order.id}: {e}")

        # --- Поиск заказов для 2-часового напоминания ---
        stmt_2h = select(Order).where(
            Order.status.in_([OrderStatus.new, OrderStatus.accepted]),
            Order.reminder_2h_sent == False
        )
        orders_to_check_2h = await session.execute(stmt_2h)
        for order in orders_to_check_2h.scalars().all():
            try:
                order_time_str = f"{order.selected_date} {order.selected_time.split(' ')[0]}"
                aware_order_datetime = datetime.datetime.strptime(order_time_str, "%Y-%m-%d %H:%M").replace(
                    tzinfo=TYUMEN_TZ)

                if remind_at_2h_from < aware_order_datetime <= remind_at_2h_to:
                    if order.status == OrderStatus.accepted:
                        # Напоминание клиенту
                        client_text = f"🕒 Уборка начнется через 2 часа! Наш клинер скоро будет у вас по адресу: {order.address_text}."
                        await client_bot.send_message(chat_id=order.client_tg_id, text=client_text)

                        # Напоминание исполнителю
                        if order.executor_tg_id:
                            executor_text = f"🕒 Уборка по заказу №{order.id} начнется через 2 часа! Не забудьте вовремя нажать '🚀 В пути'."
                            await executor_bot.send_message(chat_id=order.executor_tg_id, text=executor_text)

                    elif order.status == OrderStatus.new:
                        # Если исполнитель НЕ назначен - бьем тревогу админу
                        text = f"⚠️ <b>СРОЧНО!</b> Не найден исполнитель для заказа №{order.id}, который начинается через 2 часа!"
                        await bots["admin"].send_message(chat_id=admin_id, text=text)
                    order.reminder_2h_sent = True
            except Exception as e:
                print(f"Ошибка при обработке 2h напоминания для заказа {order.id}: {e}")

        await session.commit()

async def check_and_auto_close_tickets(bot: Bot, session_pool):
    """
    Проверяет тикеты со статусом 'Ответ получен' и закрывает их, если нет активности.
    """
    now = datetime.datetime.now()
    # Временные рамки
    h24_ago = now - datetime.timedelta(hours=24)
    h48_ago = now - datetime.timedelta(hours=48)

    async with session_pool() as session:
        # 1. Ищем тикеты для отправки 24-часового предупреждения
        stmt_remind = select(Ticket).where(
            Ticket.status == TicketStatus.answered,
            Ticket.updated_at < h24_ago,
            Ticket.autoclose_reminder_sent == False
        )
        tickets_to_remind = await session.execute(stmt_remind)
        for ticket in tickets_to_remind.scalars().all():
            try:
                text = (
                    f"👋 Напоминаем по вашему обращению №{ticket.id}.\n\n"
                    f"Если ваш вопрос не решен, пожалуйста, ответьте на это сообщение. "
                    f"В противном случае, обращение будет автоматически закрыто через 24 часа."
                )
                await bot.send_message(chat_id=ticket.user_tg_id, text=text)
                ticket.autoclose_reminder_sent = True
            except Exception as e:
                print(f"Ошибка при отправке 24ч напоминания по тикету {ticket.id}: {e}")

        # 2. Ищем тикеты для автозакрытия
        stmt_close = select(Ticket).where(
            Ticket.status == TicketStatus.answered,
            Ticket.updated_at < h48_ago
        )
        tickets_to_close = await session.execute(stmt_close)
        for ticket in tickets_to_close.scalars().all():
            try:
                ticket.status = TicketStatus.closed
                ticket.was_autoclosed = True
                text = (
                    f"✅ Ваше обращение №{ticket.id} было автоматически закрыто, "
                    f"так как мы не получили от вас ответа в течение 48 часов. "
                    f"Если проблема осталась, создайте, пожалуйста, новое обращение."
                )
                await bot.send_message(chat_id=ticket.user_tg_id, text=text)
            except Exception as e:
                print(f"Ошибка при автозакрытии тикета {ticket.id}: {e}")

        await session.commit()