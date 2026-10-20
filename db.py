# -*- coding: utf-8 -*-
# db.py
import aiosqlite
import uuid
from typing import Optional, Dict, Any

DB_PATH = "C:\\VPSParser\\orders.db"

async def init_db():
    """Создаёт таблицу orders, если её нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                ads_limit INTEGER NOT NULL,
                email TEXT NOT NULL,
                callback_url TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                summa REAL,
                order_id_parser TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                error_message TEXT
            )
        ''')
        await db.commit()

async def create_order_record(url: str, ads_limit: int, email: str, callback_url: str = None) -> str:
    """Создаёт новую запись заказа и возвращает внутренний order_id (UUID)."""
    order_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO orders (id, url, ads_limit, email, callback_url, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (order_id, url, ads_limit, email, callback_url, 'pending'))
        await db.commit()
    return order_id

async def update_order_status(order_id: str, status: str, summa: float = None,
                              order_id_parser: str = None, error_message: str = None):
    """Обновляет статус заказа и дополнительные поля."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            UPDATE orders
            SET status = ?, summa = ?, order_id_parser = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (status, summa, order_id_parser, error_message, order_id))
        await db.commit()

async def get_order_by_id(order_id: str) -> Optional[Dict[str, Any]]:
    """Возвращает запись заказа в виде словаря или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM orders WHERE id = ?', (order_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None