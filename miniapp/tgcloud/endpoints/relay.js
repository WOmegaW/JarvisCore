import { fetch, EndpointError } from 'sdk';
import { BACKEND } from '../lib/config.js';

// Мост между приложением и сервером на PythonAnywhere.
// Телефон обращается только к Telegram, а Telegram уже ходит на сервер.
// Проверку доступа делает сервер: он сверяет подпись initData и членство в группе.
const PATH_RE = /^\/api\/[A-Za-z0-9_\/-]+(\?[A-Za-z0-9=&_-]*)?$/;

export default async function (input, ctx) {
  const { path, method = 'GET', init = '', body } = input;
  if (typeof path !== 'string' || !PATH_RE.test(path) || path.includes('..')) {
    throw new EndpointError('Неверный запрос', { code: 'BAD_PATH' });
  }
  if (method !== 'GET' && method !== 'POST') {
    throw new EndpointError('Неверный запрос', { code: 'BAD_METHOD' });
  }
  let res;
  try {
    res = await fetch(BACKEND + path, {
      method,
      headers: { 'X-Init-Data': String(init), 'Content-Type': 'application/json' },
      body: method === 'POST' ? JSON.stringify(body || {}) : undefined,
    });
  } catch (e) {
    console.error('backend unreachable', e);
    throw new EndpointError('Сервер учёта не отвечает. Попробуйте ещё раз через минуту.', { code: 'BACKEND_DOWN' });
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* не JSON */ }
  if (!res.ok) {
    const msg = (data && typeof data.error === 'string') ? data.error : `Ошибка сервера (${res.status})`;
    throw new EndpointError(msg, { code: 'HTTP_' + res.status });
  }
  return data;
}
