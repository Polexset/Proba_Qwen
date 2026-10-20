# -*- coding: utf-8 -*-
"""
Модуль для асинхронной работы с API parser24.online.
Использует httpx.AsyncClient.
Все функции принимают клиент и возвращают результат или кортеж (данные, ошибка).
"""

import os
import re
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple, Any

import httpx
from dotenv import load_dotenv

# Настройка логгера для этого модуля
logger = logging.getLogger(__name__)

# Загружаем переменные из .env (файл в той же папке)
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# Конфигурация
LOGIN = os.getenv("PARSER_LOGIN")
PASSWORD = os.getenv("PARSER_PASSWORD")

# Константы API (обычно не меняются)
FIELDS = "owner_name,header,text,price,address,cat_text,url,date"
FORMAT_PHONE = "plus7"
FILE_TYPE = "0"
SAVE_TAG = "0"
PARSE_BY_DATE = ""
FORM_NO = "1"
LOCAL_PRIORITY = "1"
VIP = "0"

# Настройки повторных попыток
MAX_RETRIES = 3
REQUEST_TIMEOUT = 60.0


# -------------------------------------------------------------------
# 1. Логин
# -------------------------------------------------------------------
async def login(client: httpx.AsyncClient) -> bool:
    """
    Выполняет вход на parser24.online.
    Возвращает True при успехе, иначе False.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Origin": "https://parser24.online",
        "Referer": "https://parser24.online/login",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    login_url = "https://parser24.online/login"
    payload = {"login": LOGIN, "pass": PASSWORD}

    try:
        resp = await client.post(
            login_url,
            data=payload,
            headers=headers,
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT
        )
        if resp.status_code not in (200, 302):
            logger.info(f"  Ошибка HTTP {resp.status_code}")
            return False

        # Проверяем, что мы попали на главную – косвенный признак успеха
        if "balance" in resp.text or "parser24.online" in resp.text:
            logger.info("  Вход выполнен успешно")
            return True
        else:
            logger.info("  Не удалось войти: неверный ответ сервера")
            return False
    except httpx.HTTPError as e:
        logger.info(f"  Ошибка авторизации: {e}")
        return False


# -------------------------------------------------------------------
# 2. Получение user_id и баланса
# -------------------------------------------------------------------
async def get_user_id_from_main_page(client: httpx.AsyncClient) -> Optional[str]:
    """
    Извлекает global_user_id из HTML главной страницы.
    """
    try:
        resp = await client.get("https://parser24.online/", timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            match = re.search(r'var\s+global_user_id\s*=\s*["\']?(\d+)', resp.text)
            if match:
                user_id = match.group(1)
                logger.info(f"  Извлечён user_id: {user_id}")
                return user_id
    except Exception as e:
        logger.info(f"  Не удалось извлечь user_id: {e}")
    return None


async def get_balance_and_user_id(client: httpx.AsyncClient) -> Tuple[Optional[float], Optional[str]]:
    """
    Возвращает (баланс, user_id) или (None, None) в случае ошибки.
    Если user_id не удалось извлечь, используется fallback 22837.
    """
    try:
        resp = await client.get(
            "https://parser24.online/lk/get_balance.php",
            timeout=REQUEST_TIMEOUT
        )
        if resp.status_code != 200:
            logger.info(f"  Ошибка HTTP {resp.status_code}")
            return None, None

        data = resp.json()
        if isinstance(data, list) and data:
            item = data[0]
            if item.get("status") == 1:
                balance = float(item.get("balance", 0))
                user_id = await get_user_id_from_main_page(client)
                if not user_id:
                    logger.info("  Не удалось получить user_id, используем fallback 22837")
                    user_id = "22837"
                logger.info(f"  Баланс: {balance:.2f} руб., user_id: {user_id}")
                return balance, user_id
            else:
                error = item.get("error", "Неизвестная ошибка")
                logger.info(f"  Ошибка получения баланса: {error}")
                return None, None
    except Exception as e:
        logger.info(f"  Ошибка при запросе баланса: {e}")
        return None, None


# -------------------------------------------------------------------
# 3. Создание заказа (order_create.php)
# -------------------------------------------------------------------
async def create_order(
    client: httpx.AsyncClient,
    user_id: str,
    url: str,
    limit: int
) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """
    Создаёт заказ.
    Возвращает (order_id, summa, error_message).
    При успехе error_message = None.
    Если сумма не пришла, вычисляется как limit * 0.10.
    """
    logger.info(f"Создаём заказ: url={url}, limit={limit}")

    data = {
        "url": url,
        "fields": FIELDS,
        "limit": str(limit),
        "format": FORMAT_PHONE,
        "user_id": user_id,
        "file_type": FILE_TYPE,
        "save_tag": SAVE_TAG,
        "parse_by_date": PARSE_BY_DATE,
        "form_no": FORM_NO,
        "local_priority": LOCAL_PRIORITY,
        "vip": VIP,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post(
                "https://parser24.online/order_create.php",
                data=data,
                timeout=REQUEST_TIMEOUT
            )
            if resp.status_code != 200:
                error_msg = f"HTTP {resp.status_code}"
                logger.info(f"  Попытка {attempt} – {error_msg}")
                if attempt == MAX_RETRIES:
                    return None, None, error_msg
                continue

            # Парсим ответ
            text = resp.text.strip()
            logger.debug(f"  Ответ сервера (первые 500 символов): {text[:500]}")  # для отладки

            # 1) Пытаемся как JSON
            try:
                json_data = resp.json()
                if isinstance(json_data, list) and json_data:
                    item = json_data[0]
                    if item.get("status") == 1:
                        order_id = str(item.get("zakaz_id"))
                        summa = item.get("summa")
                        if summa is None:
                            # Сумма не пришла – вычисляем сами
                            summa = limit * 0.10
                            logger.warning(f"  Предупреждение: сумма не получена, рассчитана как {summa:.2f} руб.")
                        else:
                            summa = float(summa)
                        if order_id:
                            logger.info(f"  Заказ создан: ID={order_id}, сумма={summa:.2f}")
                            return order_id, summa, None
                        else:
                            return None, None, "Отсутствует zakaz_id"
                    else:
                        error = item.get("error", "Неизвестная ошибка")
                        return None, None, error
            except (json.JSONDecodeError, ValueError):
                pass

            # 2) Если не JSON – ищем в HTML
            match = re.search(r'zakaz_id["\']?\s*[:=]\s*["\']?(\d+)', text)
            if match:
                order_id = match.group(1)
                # Суммы нет – вычисляем сами
                summa = limit * 0.10
                logger.warning(f"  Предупреждение: сумма не получена (из HTML), рассчитана как {summa:.2f} руб.")
                logger.info(f"  Заказ создан (из HTML): ID={order_id}")
                return order_id, summa, None

            # 3) Если ничего не нашли – ошибка
            return None, None, f"Не удалось распарсить ответ: {text[:200]}"

        except httpx.HTTPError as e:
            logger.info(f"  Попытка {attempt} – сетевая ошибка: {e}")
            if attempt == MAX_RETRIES:
                return None, None, f"Сетевая ошибка: {e}"
            # Задержка перед следующей попыткой
            await asyncio.sleep(2 ** (attempt - 1))

    return None, None, "Не удалось создать заказ после нескольких попыток"


# -------------------------------------------------------------------
# 4. Обновление заказа (update_order.php)
# -------------------------------------------------------------------
async def update_order(
    client: httpx.AsyncClient,
    order_id: str,
    email: str,
    summa: float
) -> bool:
    """
    Обновляет заказ – передаёт email, сумму, параметры.
    Возвращает True при успехе.
    """
    logger.info(f"Обновляем заказ {order_id}...")

    data = {
        "order_id": order_id,
        "email": email,
        "type": "3",                 # полный тариф
        "phone_dubli": "0",
        "phone_anonym": "0",
        "ads_dubli": "0",
        "only_new": "1",             # только новые объявления
        "only_new_phone": "0",
        "summa": f"{summa:.2f}",
        "fields": FIELDS,
        "format": FORMAT_PHONE,
        "only_with_phone": "0",
        "seller_dubli": "0",         # не удалять дубли продавцов
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post(
                "https://parser24.online/update_order.php",
                data=data,
                timeout=REQUEST_TIMEOUT
            )
            if resp.status_code == 200:
                logger.info("  Заказ обновлён")
                return True
            else:
                logger.info(f"  Попытка {attempt} – HTTP {resp.status_code}")
                if attempt == MAX_RETRIES:
                    return False
                await asyncio.sleep(2 ** (attempt - 1))
        except httpx.HTTPError as e:
            logger.info(f"  Попытка {attempt} – ошибка: {e}")
            if attempt == MAX_RETRIES:
                return False
            await asyncio.sleep(2 ** (attempt - 1))

    return False


# -------------------------------------------------------------------
# 5. Оплата заказа (pay_ls.php)
# -------------------------------------------------------------------
async def pay_order(client: httpx.AsyncClient, order_id: str) -> bool:
    """
    Оплачивает заказ.
    Возвращает True при успехе.
    """
    logger.info(f"Оплачиваем заказ {order_id}...")
    data = {"order_id": order_id}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post(
                "https://parser24.online/pay_ls.php",
                data=data,
                timeout=REQUEST_TIMEOUT
            )
            if resp.status_code == 200:
                if "spasibo" in resp.text.lower():
                    logger.info("  Оплата прошла успешно")
                    return True
                else:
                    logger.info("  Ответ без 'spasibo', но статус 200 – считаем успехом")
                    return True
            else:
                logger.info(f"  Попытка {attempt} – HTTP {resp.status_code}")
                if attempt == MAX_RETRIES:
                    return False
                await asyncio.sleep(2 ** (attempt - 1))
        except httpx.HTTPError as e:
            logger.info(f"  Попытка {attempt} – ошибка: {e}")
            if attempt == MAX_RETRIES:
                return False
            await asyncio.sleep(2 ** (attempt - 1))

    return False


# -------------------------------------------------------------------
# 6. Полный цикл создания и оплаты заказа
# -------------------------------------------------------------------
async def create_and_pay_order(
    client: httpx.AsyncClient,
    url: str,
    limit: int,
    email: str
) -> Tuple[Optional[str], Optional[str]]:
    """
    Выполняет полный цикл: логин, получение user_id,
    создание, обновление, оплата.
    Возвращает (order_id, error_message) – при успехе error_message = None.
    """
    # 0. Сначала выполняем вход
    if not await login(client):
        return None, "Не удалось авторизоваться на parser24.online"

    # 1. Получаем баланс и user_id
    balance, user_id = await get_balance_and_user_id(client)
    if not user_id:
        return None, "Не удалось получить user_id"

    # 2. Создаём заказ
    order_id, summa, err = await create_order(client, user_id, url, limit)
    if err:
        return None, f"Ошибка создания заказа: {err}"
    if not order_id or summa is None:
        return None, "Не удалось создать заказ (нет ID или суммы)"

    # 3. Проверяем баланс
    if balance is not None and balance < summa:
        return None, f"Недостаточно средств: баланс {balance:.2f}, требуется {summa:.2f}"

    # 4. Обновляем заказ
    if not await update_order(client, order_id, email, summa):
        return None, "Ошибка обновления заказа"

    # 5. Оплачиваем
    if not await pay_order(client, order_id):
        return None, "Ошибка оплаты заказа"

    return order_id, None
