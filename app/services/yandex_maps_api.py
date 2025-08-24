import httpx


async def get_address_from_coords(latitude: float, longitude: float, api_key: str) -> str | None:
    """
    Получает текстовый адрес по координатам через API Яндекс.Геокодер.
    """
    url = "https://geocode-maps.yandex.ru/1.x/"
    params = {
        "geocode": f"{longitude},{latitude}",
        "apikey": api_key,
        "format": "json",
        "results": 1
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()  # Проверка на ошибки HTTP (4xx, 5xx)

            data = response.json()

            # Парсим ответ, чтобы достать адрес
            address = data["response"]["GeoObjectCollection"]["featureMember"][0]["GeoObject"]["metaDataProperty"][
                "GeocoderMetaData"]["text"]
            return address

    except (httpx.RequestError, KeyError, IndexError) as e:
        print(f"Ошибка при запросе к Яндекс.API: {e}")
        return None


async def get_address_from_text(address_text: str, api_key: str) -> str | None:
    """
    Проверяет текстовый адрес и возвращает его стандартизированную версию.
    """
    url = "https://geocode-maps.yandex.ru/1.x/"
    params = {
        "geocode": address_text,
        "apikey": api_key,
        "format": "json",
        "results": 1,
        "bbox": "64.0,56.0~72.0,59.0"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            # Находим первый результат
            feature_member = data["response"]["GeoObjectCollection"]["featureMember"]
            if not feature_member:
                return None  # Адрес не найден
            address = feature_member[0]["GeoObject"]["metaDataProperty"]["GeocoderMetaData"]["text"]
            return address

    except (httpx.RequestError, KeyError, IndexError) as e:
        print(f"Ошибка при запросе к Яндекс.API: {e}")
        return None