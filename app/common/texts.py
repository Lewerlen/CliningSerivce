from app.database.models import OrderStatus

# Словарь для отображения статусов на русском языке
STATUS_MAPPING = {
    OrderStatus.new: "✅ Принят, ищем исполнителя",
    OrderStatus.accepted: "🤝 Исполнитель назначен",
    OrderStatus.on_the_way: "🚀 Исполнитель в пути",
    OrderStatus.in_progress: "🧼 Уборка в процессе",
    OrderStatus.completed: "🎉 Завершен",
    OrderStatus.cancelled: "❌ Отменен",
    OrderStatus.pending_confirmation: "⏳ Ожидает подтверждения изменений исполнителем"
}

# Список месяцев в родительном падеже для красивого вывода
RUSSIAN_MONTHS_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}