import asyncio
import httpx
from parser_api import create_and_pay_order

async def main():
    # Параметры заказа (замените на свои)
    URL = "https://www.avito.ru/krasnodar/gotoviy_biznes/torgovlya/gotovyj_biznes_produktovye_magaziny-ASgBAgICAkToDKy2AZ6kFYyFkgM?cd=1&context=H4sIAAAAAAAA_wEmANn_YToxOntzOjE6InkiO3M6MTY6IkM4a2NyWDVGQ0JIaDdSTDUiO31iEeeMJgAAAA&f=ASgBAgECBEToDKy2AfDfD4L59QKepBWMhZIDrqsVlq2SAwJFxpoMHXsiZnJvbSI6MzUwMDAwMCwidG8iOjgwMDAwMDB9oqsVGXsiZnJvbSI6ODAwMDAwLCJ0byI6bnVsbH0&localPriority=1"
    LIMIT = 20
    EMAIL = "polexset@yandex.ru"

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://parser24.online",
            "Referer": "https://parser24.online/",
        },
        timeout=60.0,
        follow_redirects=True
    ) as client:
        order_id, error = await create_and_pay_order(client, URL, LIMIT, EMAIL)
        if error:
            print(f"Ошибка: {error}")
        else:
            print(f"✅ Заказ {order_id} успешно создан и оплачен!")

if __name__ == "__main__":
    asyncio.run(main())