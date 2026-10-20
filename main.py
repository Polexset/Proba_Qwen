# -*- coding: utf-8 -*-
# main.py
import os
import csv
import asyncio
import uuid
import logging
import json
import time
import shutil
from pathlib import Path
from datetime import datetime, time as dtime
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, HttpUrl, validator, Field
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

# Загружаем переменные из .env (явный путь, чтобы избежать проблем)
ENV_PATH = r'C:\VPSParser\.env'
if os.path.exists(ENV_PATH):
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

# Импортируем наши модули
from db import init_db, create_order_record, update_order_status, get_order_by_id
from parser_api import (
    login, get_balance_and_user_id, create_order as parser_create_order,
    update_order as parser_update_order, pay_order as parser_pay_order,
)

# ------------------------------------------------------------------
# Настройки из .env
# ------------------------------------------------------------------
API_KEY = os.getenv("API_KEY", "").strip()

SCHEDULE_FILE = os.getenv("SCHEDULE_FILE", r"C:\VPSParser\schedule.csv")
SCHEDULE_RUNTIME_FILE = os.getenv(
    "SCHEDULE_RUNTIME_FILE", r"C:\VPSParser\schedule.runtime.csv"
)
SCHEDULE_CHECK_INTERVAL = int(os.getenv("SCHEDULE_CHECK_INTERVAL", "60"))
SCHEDULE_DAILY_RESET_TIME = os.getenv("SCHEDULE_DAILY_RESET_TIME", "00:00")

ORDERS_LOG_DIR = os.getenv("ORDERS_LOG_DIR", r"C:\VPSParser\logs\orders")

TIMEZONE_NAME = os.getenv("TIMEZONE", "Europe/Moscow")
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

# ------------------------------------------------------------------
# Логирование
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('C:\\VPSParser\\logs\\app.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# FastAPI + очередь
# ------------------------------------------------------------------
app = FastAPI(title="VPS Parser Service")
task_queue: asyncio.Queue = asyncio.Queue()


# ------------------------------------------------------------------
# Модель запроса /create_order
# ------------------------------------------------------------------
class OrderCreateRequest(BaseModel):
    url: HttpUrl
    ads_limit: int
    email: str
    callback_url: Optional[HttpUrl] = Field(
        None, description="URL приёмника вебхука. Если не указан — берётся WEBHOOK_URL из .env"
    )
    send_webhook: bool = Field(
        True, description="Отправлять ли вебхук после оплаты (для этого заказа)"
    )

    @validator('ads_limit')
    def validate_limit(cls, v):
        if v < 0:
            raise ValueError('ads_limit must be >= 0')
        return v

    @validator('email')
    def validate_email(cls, v):
        if '@' not in v or '.' not in v:
            raise ValueError('Invalid email format')
        return v


# ------------------------------------------------------------------
# Проверка API-ключа
# ------------------------------------------------------------------
async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """
    Если API_KEY не задан в .env — защита отключена (для отладки).
    Если задан — требуется совпадение заголовка X-API-Key.
    """
    if not API_KEY:
        return True
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return True


# ------------------------------------------------------------------
# Журнал заказов на VDS (orders_log_YYYY-MM-DD.csv)
# ------------------------------------------------------------------
def _orders_log_path(dt: datetime) -> Path:
    """Путь к файлу журнала за текущий день (по МСК)."""
    day = dt.astimezone(TIMEZONE).strftime("%Y-%m-%d")
    return Path(ORDERS_LOG_DIR) / f"orders_{day}.csv"


def log_order_to_csv(
    order_id: str,
    status: str,
    summa: Optional[float],
    parser_order_id: Optional[str],
    url: Optional[str],
    email: Optional[str],
    error_message: Optional[str] = None,
) -> None:
    """
    Дописывает строку в журнал orders_YYYY-MM-DD.csv.
    Пишется ВСЕГДА, независимо от того, шёл вебхук или нет.
    """
    try:
        Path(ORDERS_LOG_DIR).mkdir(parents=True, exist_ok=True)
        now = datetime.now(TIMEZONE)
        log_path = _orders_log_path(now)
        file_exists = log_path.exists()

        with log_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            if not file_exists:
                writer.writerow([
                    "timestamp", "order_id", "parser_order_id",
                    "status", "summa", "url", "email", "error_message"
                ])
            writer.writerow([
                now.strftime("%Y-%m-%d %H:%M:%S"),
                order_id or "",
                parser_order_id or "",
                status or "",
                "" if summa is None else f"{summa:.2f}",
                url or "",
                email or "",
                (error_message or "").replace("\n", " ").replace(";", ","),
            ])
    except Exception as e:
        logger.error(f"Не удалось записать в orders_log: {e}")


# ------------------------------------------------------------------
# Отправка вебхука
# ------------------------------------------------------------------
async def send_webhook(
    callback_url: Optional[str],
    order_id: str,
    status: str,
    summa: Optional[float] = None,
    parser_order_id: Optional[str] = None,
    url: Optional[str] = None,
    ads_limit: Optional[int] = None,
    email: Optional[str] = None,
    send_flag: bool = True,
):
    """
    Отправляет POST на callback_url.
    Логика:
      - если send_flag=False → вообще не отправляем
      - если callback_url задан → шлём туда
      - иначе берём WEBHOOK_URL из .env
      - если и там пусто → пишем ошибку и выходим
    """
    if not send_flag:
        logger.info(f"Вебхук для заказа {order_id} отключён (webhook=0)")
        return

    target_url = callback_url or os.getenv("WEBHOOK_URL")
    if not target_url:
        logger.error(
            f"Вебхук для заказа {order_id} не отправлен: "
            f"callback_url пуст и WEBHOOK_URL не задан в .env"
        )
        return

    payload = {
        "order_id": order_id,
        "status": status,
        "summa": summa,
        "parser_order_id": parser_order_id,
        "timestamp": time.time(),
        "url": url,
        "ads_limit": ads_limit,
        "email": email,
        "callback_url": target_url,
    }
    headers = {"Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.post(str(target_url), json=payload, headers=headers)
            logger.info(f"Вебхук ответ: статус {resp.status_code}")
            logger.info(f"Тело ответа: {resp.text[:500]}")
            if resp.status_code != 200:
                logger.warning(f"Вебхук вернул статус {resp.status_code}")
    except Exception as e:
        logger.error(f"Ошибка отправки вебхука: {e}")


# ------------------------------------------------------------------
# Фоновый воркер (обработка задачи из очереди)
# ------------------------------------------------------------------
async def worker():
    while True:
        try:
            task = await task_queue.get()
            order_id = task['order_id']
            url = task['url']
            ads_limit = task['ads_limit']
            email = task['email']
            callback_url = task.get('callback_url')
            send_flag = task.get('send_webhook', True)

            logger.info(f"Начало обработки заказа {order_id}")

            await update_order_status(order_id, 'processing')

            order_id_parser = None
            summa = None
            error_text = None

            async with httpx.AsyncClient(
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Origin": "https://parser24.online",
                    "Referer": "https://parser24.online/",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                timeout=60.0,
                follow_redirects=True
            ) as client:
                try:
                    if not await login(client):
                        raise Exception("Ошибка авторизации на parser24.online")

                    balance_info = await get_balance_and_user_id(client)
                    if not balance_info:
                        raise Exception("Не удалось получить user_id")
                    balance, user_id = balance_info

                    order_id_parser, summa, error = await parser_create_order(
                        client, user_id, url, ads_limit
                    )
                    if error or not order_id_parser or summa is None:
                        raise Exception(f"Ошибка создания заказа: {error}")

                    if balance < summa:
                        raise Exception(
                            f"Недостаточно средств: баланс {balance}, требуется {summa}"
                        )

                    if not await parser_update_order(client, order_id_parser, email, summa):
                        raise Exception("Ошибка обновления заказа")

                    if not await parser_pay_order(client, order_id_parser):
                        raise Exception("Ошибка оплаты заказа")

                    await update_order_status(
                        order_id, 'paid',
                        summa=summa,
                        order_id_parser=order_id_parser,
                        error_message=None
                    )
                    logger.info(
                        f"Заказ {order_id} успешно оплачен (parser ID: {order_id_parser})"
                    )

                    log_order_to_csv(
                        order_id=order_id,
                        status='paid',
                        summa=summa,
                        parser_order_id=order_id_parser,
                        url=url,
                        email=email,
                    )

                    await send_webhook(
                        callback_url, order_id, 'paid', summa,
                        order_id_parser, url, ads_limit, email,
                        send_flag=send_flag,
                    )
                except Exception as e:
                    error_text = str(e)
                    raise

        except Exception as e:
            logger.exception(f"Ошибка при обработке заказа {order_id}: {e}")
            try:
                await update_order_status(order_id, 'failed', error_message=str(e))
            except Exception:
                pass
            try:
                log_order_to_csv(
                    order_id=order_id,
                    status='failed',
                    summa=None,
                    parser_order_id=order_id_parser,
                    url=url if 'url' in locals() else None,
                    email=email if 'email' in locals() else None,
                    error_message=error_text or str(e),
                )
            except Exception:
                pass
        finally:
            task_queue.task_done()


# ------------------------------------------------------------------
# Чтение / запись schedule.runtime.csv
# ------------------------------------------------------------------
SCHEDULE_FIELDS = [
    "id", "description", "url", "email",
    "limit", "active", "webhook", "webhook_url",
]


def _read_schedule(path: str) -> list[dict]:
    """Читает CSV-расписание (разделитель ';'). Возвращает список словарей."""
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for r in reader:
            rows.append(r)
    return rows


def _write_schedule(path: str, rows: list[dict]) -> None:
    """Атомарно пишет CSV-расписание."""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SCHEDULE_FIELDS, delimiter=";")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in SCHEDULE_FIELDS})
    # Атомарная замена
    os.replace(tmp, p)


def _safe_int(v, default=0) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _pick_next_active(rows: list[dict]) -> Optional[int]:
    """Индекс первой сверху строки с active=1 и непустыми url+email."""
    for i, r in enumerate(rows):
        if _safe_int(r.get("active", "0")) == 1:
            if (r.get("url") or "").strip() and (r.get("email") or "").strip():
                return i
            # битая строка — сразу обнуляем
            r["active"] = "0"
    return None


# ------------------------------------------------------------------
# Планировщик: раз в N секунд берёт одну строку
# ------------------------------------------------------------------
async def scheduler():
    logger.info(
        f"Планировщик запущен: интервал {SCHEDULE_CHECK_INTERVAL} сек, "
        f"файл {SCHEDULE_RUNTIME_FILE}"
    )
    while True:
        try:
            rows = _read_schedule(SCHEDULE_RUNTIME_FILE)
            if rows:
                idx = _pick_next_active(rows)
                if idx is not None:
                    row = rows[idx]
                    # Сразу гасим, чтобы не запустить повторно
                    row["active"] = "0"
                    try:
                        _write_schedule(SCHEDULE_RUNTIME_FILE, rows)
                    except PermissionError:
                        logger.error(
                            "Не удалось записать schedule.runtime.csv — файл занят. "
                            "Строка не будет запущена в этот раз."
                        )
                        await asyncio.sleep(SCHEDULE_CHECK_INTERVAL)
                        continue

                    # Создаём запись и ставим в очередь
                    order_id = await create_order_record(
                        url=row.get("url", "").strip(),
                        ads_limit=_safe_int(row.get("limit", "0")),
                        email=row.get("email", "").strip(),
                        callback_url=(row.get("webhook_url") or "").strip() or None,
                    )

                    task_queue.put_nowait({
                        "order_id": order_id,
                        "url": row.get("url", "").strip(),
                        "ads_limit": _safe_int(row.get("limit", "0")),
                        "email": row.get("email", "").strip(),
                        "callback_url": (row.get("webhook_url") or "").strip() or None,
                        "send_webhook": _safe_int(row.get("webhook", "1"), default=1) == 1,
                    })
                    logger.info(
                        f"Планировщик поставил в очередь заказ {order_id} "
                        f"({row.get('description', '')})"
                    )
        except Exception as e:
            logger.exception(f"Ошибка планировщика: {e}")

        await asyncio.sleep(SCHEDULE_CHECK_INTERVAL)


# ------------------------------------------------------------------
# Суточный сброс: schedule.csv → schedule.runtime.csv, все active=1
# ------------------------------------------------------------------
def _seconds_until_next_reset() -> float:
    """Сколько секунд ждать до следующего HH:MM по МСК."""
    hh, mm = map(int, SCHEDULE_DAILY_RESET_TIME.split(":"))
    now = datetime.now(TIMEZONE)
    target = datetime.combine(now.date(), dtime(hh, mm), tzinfo=TIMEZONE)
    if target <= now:
        # Переносим на следующие сутки
        target = target.replace(day=now.day) + (datetime.combine(now.date(), dtime(0, 0), tzinfo=TIMEZONE) - datetime.combine(now.date(), dtime(0, 0), tzinfo=TIMEZONE))
        # проще: прибавить сутки
        import datetime as _dt
        target = target + _dt.timedelta(days=1)
    return (target - now).total_seconds()


def _do_daily_reset():
    """Копирует schedule.csv → schedule.runtime.csv и ставит всем active=1."""
    if not Path(SCHEDULE_FILE).exists():
        logger.warning(f"Эталонный расписание не найден: {SCHEDULE_FILE}")
        return
    rows = _read_schedule(SCHEDULE_FILE)
    for r in rows:
        # сбрасываем только те, где есть URL и email
        if (r.get("url") or "").strip() and (r.get("email") or "").strip():
            r["active"] = "1"
        else:
            r["active"] = "0"
    _write_schedule(SCHEDULE_RUNTIME_FILE, rows)
    logger.info(
        f"Суточный сброс: {SCHEDULE_FILE} → {SCHEDULE_RUNTIME_FILE}, "
        f"active=1 у {sum(1 for r in rows if r.get('active') == '1')} строк"
    )


async def daily_reset():
    # Сразу при старте — синхронизируем runtime из эталона,
    # чтобы не ждать полуночи при первом запуске.
    try:
        _do_daily_reset()
    except Exception as e:
        logger.exception(f"Ошибка первичного сброса расписания: {e}")

    while True:
        delay = _seconds_until_next_reset()
        logger.info(f"Следующий суточный сброс через {int(delay)} сек")
        await asyncio.sleep(delay)
        try:
            _do_daily_reset()
        except Exception as e:
            logger.exception(f"Ошибка суточного сброса: {e}")


# ------------------------------------------------------------------
# Эндпоинт /create_order
# ------------------------------------------------------------------
@app.post("/create_order", response_model=dict, dependencies=[Depends(verify_api_key)])
async def create_order_endpoint(req: OrderCreateRequest):
    order_id = await create_order_record(
        url=str(req.url),
        ads_limit=req.ads_limit,
        email=req.email,
        callback_url=str(req.callback_url) if req.callback_url else None,
    )
    await task_queue.put({
        "order_id": order_id,
        "url": str(req.url),
        "ads_limit": req.ads_limit,
        "email": req.email,
        "callback_url": str(req.callback_url) if req.callback_url else None,
        "send_webhook": req.send_webhook,
    })
    logger.info(f"Заказ {order_id} добавлен в очередь (API)")
    return {
        "order_id": order_id,
        "status": "pending",
        "message": "Заказ принят в обработку",
    }


# ------------------------------------------------------------------
# Startup
# ------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    await init_db()
    asyncio.create_task(worker())
    asyncio.create_task(scheduler())
    asyncio.create_task(daily_reset())
    logger.info("Приложение запущено, воркер + планировщик + суточный сброс стартовали")


# ------------------------------------------------------------------
# Запуск
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="info")