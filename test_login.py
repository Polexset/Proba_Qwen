# -*- coding: utf-8 -*-
import asyncio
import httpx
from parser_api import login, get_balance_and_user_id

async def main():
    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
            "Origin": "https://parser24.online",
            "Referer": "https://parser24.online/",
        },
        timeout=30.0,
        follow_redirects=True
    ) as client:
        # Сначала логинимся
        if not await login(client):
            print("Не удалось войти")
            return
        # Затем получаем баланс и user_id
        balance, user_id = await get_balance_and_user_id(client)
        if balance is not None:
            print(f"Баланс: {balance} руб., user_id: {user_id}")
        else:
            print("Не удалось получить баланс")

if __name__ == "__main__":
    asyncio.run(main())