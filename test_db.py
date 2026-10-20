# -*- coding: utf-8 -*-
# test_db.py
import asyncio
from db import init_db, create_order_record, update_order_status, get_order_by_id

async def test():
    await init_db()
    order_id = await create_order_record('https://example.com', 20, 'test@test.com', 'https://callback.com')
    print(f'Создан заказ: {order_id}')
    await update_order_status(order_id, 'processing', summa=2.0, order_id_parser='12345')
    order = await get_order_by_id(order_id)
    print(order)

asyncio.run(test())