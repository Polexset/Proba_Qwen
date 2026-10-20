// ID вашей таблицы
const SPREADSHEET_ID = '1IeKoKm_9BApas3bR59Na_x7Kt7utPsCkrGOGfw444mY';

function doPost(e) {
  try {
    // Логируем полученные данные
    console.log('=== НОВЫЙ ЗАПРОС ===');
    console.log('Содержимое: ' + e.postData.contents);

    const payload = JSON.parse(e.postData.contents);
    console.log('Распарсенные данные: ' + JSON.stringify(payload));

    // Записываем в таблицу
    const result = handlePaidOrder(payload);

    // Возвращаем успешный ответ
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'ok', received: payload }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (error) {
    console.error('ОШИБКА в doPost: ' + error.toString());
    console.error('Стек: ' + error.stack);
    
    // Возвращаем ошибку в JSON (не HTML)
    return ContentService
      .createTextOutput(JSON.stringify({ 
        error: error.toString(),
        stack: error.stack 
      }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function handlePaidOrder(data) {
  try {
    // Открываем таблицу
    const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
    console.log('Таблица открыта');
    
    // Получаем лист (создаём если нет)
    let sheet = ss.getSheetByName('Лист18');
    if (!sheet) {
      console.log('Лист "Лист18" не найден, создаём...');
      sheet = ss.insertSheet('Лист18');
      // Добавляем заголовки
      sheet.appendRow(['order_id', 'status', 'summa', 'timestamp', 'parser_order_id', 'url', 'email']);
    } else if (sheet.getLastRow() === 0) {
      // Если лист пустой – добавляем заголовки
      sheet.appendRow(['order_id', 'status', 'summa', 'timestamp', 'parser_order_id', 'url', 'email']);
    }
    
    // Преобразуем timestamp в дату
    const date = new Date(data.timestamp * 1000);
    console.log('Дата: ' + date.toISOString());
    
    // Добавляем строку
    const row = [
      data.order_id || '',
      data.status || '',
      data.summa || 0,
      date,
      data.parser_order_id || '',
      data.url || '',
      data.email || ''
    ];
    
    sheet.appendRow(row);
    console.log('✅ Данные записаны: ' + data.order_id);
    
    return { success: true, order_id: data.order_id };
    
  } catch (err) {
    console.error('Ошибка в handlePaidOrder: ' + err.toString());
    throw err;
  }
}