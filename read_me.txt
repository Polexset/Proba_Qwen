 Документация проекта VPS Parser Service
1. Общая архитектура
Система представляет собой автоматизированный сервис для приёма заказов на парсинг Avito через API parser24.online.
Заказ принимается через REST API (FastAPI), обрабатывается асинхронно, после оплаты отправляет вебхук в Google Apps Script, который записывает данные в Google Таблицу.

Стек: Python 3.11+, FastAPI, SQLite, httpx, asyncio, Google Apps Script, Google Sheets.

2. Структура проекта на VPS
text
C:\VPSParser\
│
├── main.py              # Основной сервер FastAPI, воркер, эндпоинты
├── db.py                # Работа с SQLite (orders)
├── parser_api.py        # Клиент для parser24.online (логин, заказ, оплата)
├── .env                 # Переменные окружения (WEBHOOK_URL)
├── start.bat            # Ручной запуск сервера
├── venv\                # Виртуальное окружение Python
├── logs\                # Папка с логами (app.log)
│   └── app.log
└── orders.db            # Файл базы данных SQLite (создаётся автоматически)
3. Описание файлов
3.1. main.py – основной сервер
Назначение:

Запускает FastAPI-приложение на порту 8000.

Обрабатывает POST-запросы /create_order.

Ставит заказы в очередь asyncio.Queue.

Фоновый воркер выполняет заказы по очереди: логин в parser24, создание заказа, оплата, отправка вебхука.

Читает WEBHOOK_URL из .env, если в запросе не передан callback_url.

Ключевые функции:

create_order_endpoint – приём заказа, запись в БД, добавление в очередь.

worker – фоновый воркер.

send_webhook – отправка POST на GAS с JSON-данными заказа.

Логирование: все события пишутся в файл C:\VPSParser\logs\app.log и в консоль.

3.2. db.py – работа с базой данных
Назначение:

Асинхронное управление SQLite через aiosqlite.

Таблица orders содержит все заказы и их статусы.

Функции:

init_db() – создаёт таблицу при запуске.

create_order_record() – добавляет новый заказ со статусом pending.

update_order_status() – обновляет статус, сумму, ID заказа в parser24, сообщение об ошибке.

get_order_by_id() – возвращает данные заказа по внутреннему order_id (UUID).

Путь к БД: C:\VPSParser\orders.db (задаётся в DB_PATH внутри db.py).

3.3. parser_api.py – клиент parser24.online
Назначение:

Асинхронное взаимодействие с сайтом parser24.online.

Использует httpx.AsyncClient с нужными заголовками.

Функции:

login(client) – POST на /login, сохраняет сессию (cookies).

get_balance_and_user_id(client) – GET /lk/get_balance.php для получения баланса и user_id.

create_order(client, user_id, url, ads_limit) – POST /order_create.php.

update_order(client, order_id_parser, email, summa) – POST /update_order.php.

pay_order(client, order_id_parser) – POST /pay_ls.php.

Примечание: В ответе от parser24 иногда не приходит сумма, тогда используется расчёт 2.00 руб. (как в тестах). Это допустимо.

3.4. .env – переменные окружения
Назначение: хранение конфигурации, не зашитой в код.

Содержание:

ini
# URL вашего веб-приложения Google Apps Script
WEBHOOK_URL=https://script.google.com/macros/s/.../exec

# Секрет для HMAC (если решите использовать подпись)
WEBHOOK_SECRET=super-secret-key
Важно: файл должен находиться в C:\VPSParser\.env.
При изменении URL вебхука нужно обновить .env и перезапустить сервис.

3.5. start.bat – ручной запуск
bat
@echo off
cd /d C:\VPSParser
call venv\Scripts\activate
python main.py
pause
Используется для запуска сервера вручную из командной строки.

4. Google Apps Script (GAS)
4.1. Назначение
Принимает POST-запрос от main.py, парсит JSON и записывает данные в Google Таблицу.

4.2. Файл Code.gs (единственный файл в проекте GAS)
javascript
const SPREADSHEET_ID = '1IeKoKm_9BApas3bR59Na_x7Kt7utPsCkrGOGfw444mY';

function doPost(e) {
  try {
    Logger.log('=== НОВЫЙ ЗАПРОС ===');
    const payload = JSON.parse(e.postData.contents);
    Logger.log('Распарсенные данные: ' + JSON.stringify(payload));

    if (payload.status === 'paid') {
      handlePaidOrder(payload);
    }

    return ContentService.createTextOutput(JSON.stringify({ status: 'ok' }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (error) {
    Logger.log('ОШИБКА: ' + error.toString());
    return ContentService.createTextOutput(JSON.stringify({ error: error.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function handlePaidOrder(data) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  let sheet = ss.getSheetByName('Лист18');
  if (!sheet) {
    sheet = ss.insertSheet('Лист18');
    sheet.appendRow(['order_id', 'status', 'summa', 'timestamp', 'parser_order_id', 'url', 'email']);
  } else if (sheet.getLastRow() === 0) {
    sheet.appendRow(['order_id', 'status', 'summa', 'timestamp', 'parser_order_id', 'url', 'email']);
  }
  sheet.appendRow([
    data.order_id || '',
    data.status || '',
    data.summa || '',
    new Date(data.timestamp * 1000),
    data.parser_order_id || '',
    data.url || '',
    data.email || ''
  ]);
  Logger.log('✅ Данные записаны: ' + data.order_id);
}
4.3. Настройка развертывания
Тип: Веб-приложение

Доступ: Все (или «Все, у кого есть ссылка»)

Версия: создавайте новую при изменении кода.

URL: копируйте после развертывания и вставляйте в .env.

5. Инструкции по ручному запуску и остановке (аварийные ситуации)
5.1. Обычный запуск (вручную)
Откройте командную строку (cmd) от имени администратора.

Выполните:

cmd
C:\VPSParser\start.bat
Убедитесь, что в логах появилось:

text
Приложение запущено, воркер стартовал
Uvicorn running on http://0.0.0.0:8000
5.2. Остановка вручную
В окне, где запущен сервер, нажмите Ctrl + C.

Подтвердите завершение (если запросит).

5.3. Аварийная остановка (если процесс завис)
Откройте диспетчер задач (Ctrl + Shift + Esc).

Найдите процесс python.exe (или python), который потребляет CPU/память, и завершите его.

Либо через командную строку:

cmd
taskkill /F /IM python.exe
(Завершит все процессы Python – будьте осторожны).

5.4. Проверка статуса сервиса (если настроен NSSM)
Если служба уже установлена через NSSM:

Запустите services.msc.

Найдите службу VPSParserService (или заданное имя).

Проверьте статус: Running / Stopped.

Для ручного старта/остановки используйте кнопки в консоли служб или команды:

cmd
net start VPSParserService
net stop VPSParserService
5.5. Просмотр логов
Логи сервера пишутся в:

text
C:\VPSParser\logs\app.log
Можно открыть блокнотом или использовать tail -f (если установлен Git Bash).

6. Работа с файлами (регулярные операции)
6.1. Смена URL вебхука (GAS)
Внесите изменения в GAS-скрипт, создайте новое развертывание.

Скопируйте новый URL (заканчивается на /exec).

Откройте C:\VPSParser\.env в текстовом редакторе.

Замените значение WEBHOOK_URL на новый URL.

Перезапустите сервер (или службу NSSM).

6.2. Обновление Python-кода
Остановите сервер (Ctrl+C или через службу).

Замените нужные файлы (например, main.py, parser_api.py).

Запустите заново.

6.3. Проверка базы данных
Откройте C:\VPSParser\orders.db с помощью любого SQLite-браузера (например, DB Browser for SQLite).

Таблица orders содержит все заказы с их статусами.

6.4. Тестирование нового заказа (вручную)
Используйте PowerShell на VPS:

powershell
$body = @{
    url = "https://www.avito.ru/..."
    ads_limit = 20
    email = "polexset@yandex.ru"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/create_order" -Method Post -Body $body -ContentType "application/json"
В ответе вы получите JSON с order_id и статусом pending. Через некоторое время (после обработки воркером) в таблице появится запись.

