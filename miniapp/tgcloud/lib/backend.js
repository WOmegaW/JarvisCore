import { fetch } from 'sdk';
import { BACKEND } from './config.js';
import { FORWARD_SECRET } from './secret.js';

// Пересылает обновление Telegram (сообщение, нажатие кнопки) боту на PythonAnywhere —
// тем же способом, каким его прислал бы сам Telegram.
export async function forwardUpdate(update) {
  let res;
  try {
    res = await fetch(BACKEND + '/webhook', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Telegram-Bot-Api-Secret-Token': FORWARD_SECRET,
      },
      body: JSON.stringify(update),
    });
  } catch (e) {
    console.error('Сервер бота недоступен', e);
    return;
  }
  if (!res.ok) console.error('Сервер бота ответил', res.status, await res.text());
}
