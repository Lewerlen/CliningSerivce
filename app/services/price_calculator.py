# Файл: app/services/price_calculator.py

# Тарифы, основанные на вашем ТЗ
TARIFFS = {
    "🧽 Поддерживающая": {"base": 1000, "per_room": 500, "per_bathroom": 300},
    "🧼 Генеральная": {"base": 1500, "per_room": 700, "per_bathroom": 500},
    "🛠 После ремонта": {"base": 2000, "per_room": 1000, "per_bathroom": 700},
}

# Цены на дополнительные услуги
ADDITIONAL_SERVICE_PRICES = {
    "win": 300,
    "sofa": 1500,
    "chair": 300,
    "plumbing": 500,
    "bedding": 200,
    "kitchen": 600,
    "cabinets": 500,
    "balcony": 700,
    "carpet": 800,
    "pets": 400,
    "fridge": 700,
    "stove": 500,
    "oven": 700,
}

def calculate_preliminary_cost(cleaning_type: str, room_count_str: str, bathroom_count_str: str) -> int:
    """Рассчитывает предварительную стоимость на основе выбранных опций."""

    # Получаем тариф для выбранного типа уборки
    tariff = TARIFFS.get(cleaning_type)
    if not tariff:
        return 0  # Возвращаем 0, если тип уборки не найден

    # Преобразуем строковые значения в числа, отбрасывая "+"
    try:
        room_count = int(room_count_str.replace('+', ''))
        bathroom_count = int(bathroom_count_str.replace('+', ''))
    except ValueError:
        return 0  # В случае ошибки преобразования

    # Рассчитываем стоимость по формуле из ТЗ
    # Базовая цена включает 1 комнату и 1 санузел
    base_price = tariff["base"]

    # Стоимость за дополнительные комнаты
    extra_rooms_cost = (room_count - 1) * tariff["per_room"] if room_count > 1 else 0

    # Стоимость за дополнительные санузлы
    extra_bathrooms_cost = (bathroom_count - 1) * tariff["per_bathroom"] if bathroom_count > 1 else 0

    total_cost = base_price + extra_rooms_cost + extra_bathrooms_cost

    return total_cost


def calculate_total_cost(preliminary_cost: int, selected_services: dict) -> int:
    """
    Рассчитывает итоговую стоимость, добавляя к предварительной стоимости
    цену выбранных дополнительных услуг.
    """
    additional_cost = 0
    if selected_services:
        for service_key, quantity in selected_services.items():
            price = ADDITIONAL_SERVICE_PRICES.get(service_key, 0)
            additional_cost += price * quantity

    return preliminary_cost + additional_cost

def calculate_executor_payment(total_price: float, commission_type: str, commission_value: float) -> float:
    """
    Рассчитывает сумму выплаты исполнителю на основе настроек комиссии.
    """
    if commission_type == "percent":
        # Если комиссия в процентах, вычитаем этот процент
        payment = total_price * (1 - commission_value / 100)
    elif commission_type == "fixed":
        # Если комиссия фиксированная, просто вычитаем сумму
        payment = total_price - commission_value
    else:
        # По умолчанию, если тип не задан, комиссия 0
        payment = total_price

    # Округляем до двух знаков после запятой и гарантируем, что выплата не будет отрицательной
    return max(0, round(payment, 2))