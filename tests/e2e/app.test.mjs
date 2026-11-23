// Браузерные тесты мини-приложения (Chromium через Playwright).
// Запуск: cd tests/e2e && npm test   (сервер поднимается сам)
import { test, before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const PORT = 5099;
const BASE = `http://127.0.0.1:${PORT}`;
const PYTHON = process.env.PYTHON || 'python3';
const PHOTO = path.join(HERE, 'photo.jpg');

let server, browser;

async function waitForServer() {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(BASE + '/')).ok) return; } catch { /* ещё стартует */ }
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error('сервер не поднялся');
}

before(async () => {
  if (!fs.existsSync(PHOTO)) {
    execFileSync(PYTHON, ['-c', `from PIL import Image; Image.new('RGB',(900,1200),(240,200,120)).save(r'${PHOTO}')`]);
  }
  server = spawn(PYTHON, [path.join(HERE, 'server.py'), String(PORT)], { stdio: ['ignore', 'ignore', 'pipe'] });
  server.stderr.on('data', (d) => { if (process.env.DEBUG) process.stderr.write(d); });
  await waitForServer();
  browser = await chromium.launch();
});

after(async () => { await browser?.close(); server?.kill(); });

beforeEach(async () => { await fetch(BASE + '/__test/reset', { method: 'POST' }); });

// Поддельный telegram-web-app.js: вместо Telegram — запись вызовов
function tgStub(initData, scheme = 'light') {
  return `window.__tg=[];window.Telegram={WebApp:{initData:${JSON.stringify(initData)},colorScheme:'${scheme}',
    ready(){},expand(){},close(){},onEvent(){},
    setHeaderColor(c){__tg.push('header '+c)},setBackgroundColor(c){__tg.push('bg '+c)},setBottomBarColor(c){__tg.push('bottom '+c)},
    Serverless:{call(n,i,cb){window.__relay(n,i).then(r=>r.err?cb(r.err,null):cb(null,r.result))}},
    MainButton:{_h:null,setText(){},setParams(p){__tg.push('main '+JSON.stringify(p))},onClick(h){this._h=h},offClick(){},show(){},hide(){},showProgress(){},hideProgress(){}},
    BackButton:{show(){},hide(){},onClick(){}},HapticFeedback:{notificationOccurred(){},selectionChanged(){}},
    showAlert(m){__tg.push('alert '+m)},showConfirm(m,cb){cb(true)}}};`;
}

async function openApp({ url = BASE + '/app', scheme = 'light', setup } = {}) {
  const initData = await (await fetch(BASE + '/__test/init')).text();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.errors = [];
  page.on('pageerror', (e) => page.errors.push(e.message));
  await page.route('https://telegram.org/**', (r) => r.fulfill({ contentType: 'text/javascript', body: tgStub(initData, scheme) }));
  if (setup) await setup(page);
  await page.goto(url);
  await page.waitForSelector('#screen-list:not(.hidden)');
  return page;
}

const mainButton = (p) => p.evaluate(() => Telegram.WebApp.MainButton._h());
const alerts = (p) => p.evaluate(() => __tg.filter((x) => x.startsWith('alert ')).map((x) => x.slice(6)));
const iso = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

async function addViaForm(p, { code, name, qty = 1, days = 5, photo = true }) {
  await mainButton(p);
  await p.waitForSelector('#screen-add:not(.hidden)');
  if (photo) { await p.setInputFiles('#photo-input', PHOTO); await p.waitForSelector('#photo-preview:not(.hidden)'); }
  await p.fill('#f-barcode', code);
  await p.dispatchEvent('#f-barcode', 'blur');
  await p.waitForTimeout(250);
  if (name !== undefined) await p.fill('#f-name', name);
  await p.fill('#f-qty', String(qty));
  await p.fill('#f-expiry', iso(days));
  await mainButton(p);
  await p.waitForSelector('#screen-list:not(.hidden)');
  await p.waitForTimeout(150);
}

const titles = (p) => p.$$eval('#list .item .title', (e) => e.map((x) => x.firstChild.textContent));
const shelfText = (p) => p.$eval('#d-kv', (e) => e.innerText.split('\n').find((l) => l.includes('шт')) || '');

test('пустой список, добавление, группы по дням', async () => {
  const p = await openApp();
  assert.match(await p.textContent('#list'), /Пока нет товаров/);
  await addViaForm(p, { code: '4601234567890', name: 'Молоко', qty: 6, days: 1 });
  await addViaForm(p, { code: '4600000000017', name: 'Хлеб', days: 2 });
  await addViaForm(p, { code: '4600000000031', name: 'Сыр', days: 20 });
  assert.deepEqual(await titles(p), ['Молоко', 'Хлеб', 'Сыр']);
  assert.deepEqual(await p.$$eval('.group-title > span:first-child', (e) => e.map((x) => x.textContent)), ['Завтра', 'Через 2 дня', 'Позже']);
  assert.equal(await p.textContent('#c-discount'), '2');
  assert.deepEqual(p.errors, []);
  await p.close();
});

test('автоподстановка по штрихкоду: название и фото из прошлого раза', async () => {
  const p = await openApp();
  await addViaForm(p, { code: '4601234567890', name: 'Молоко 3.2%' });
  await mainButton(p);
  await p.fill('#f-barcode', '4601234567890');
  await p.dispatchEvent('#f-barcode', 'blur');
  await p.waitForFunction(() => document.getElementById('f-name').value !== '');
  assert.equal(await p.inputValue('#f-name'), 'Молоко 3.2%');
  assert.match(await p.textContent('#barcode-hint'), /Уже вносили/);
  await p.fill('#f-expiry', iso(9));
  await mainButton(p);                                  // без нового фото
  await p.waitForSelector('#screen-list:not(.hidden)');
  assert.equal((await titles(p)).length, 2);
  await p.close();
});

test('поиск по 4 последним цифрам поднимает товар наверх и подсвечивает', async () => {
  const p = await openApp();
  await addViaForm(p, { code: '4600000000017', name: 'Хлеб', days: 1 });
  await addViaForm(p, { code: '8410000007890', name: 'Leche', days: 9 });
  await p.fill('#q', '7890');
  assert.deepEqual(await p.$$eval('.item.found .title', (e) => e.map((x) => x.firstChild.textContent)), ['Leche']);
  assert.equal(await p.textContent('.item.found mark'), '7890');
  assert.equal(await p.$$eval('.rest .item', (e) => e.length), 1);
  await p.fill('#q', '9999');
  assert.ok(await p.$('.not-found'));
  await p.click('#q-clear');
  assert.equal(await p.$$eval('.item.found', (e) => e.length), 0);
  await p.close();
});

test('сценарий магазина: 3 шт → скидка → списал 1 → продал 2 (сообщение в группу не ушло)', async () => {
  const p = await openApp();
  await addViaForm(p, { code: '4820000000001', name: 'Rye Crisps', qty: 3, days: 2 });
  await p.click('.item >> nth=0');
  await p.click('#d-actions button:has-text("Скидка")');
  await p.waitForTimeout(300);
  assert.equal(await p.textContent('#d-badge'), 'уценён');
  await p.click('#d-actions button:has-text("Списать")');
  await p.click('#qs-quick .chip >> nth=0');
  await p.click('#qs-ok');
  await p.waitForTimeout(300);
  assert.match(await shelfText(p), /2 шт\., списано 1/);
  await fetch(BASE + '/__test/fail/SendMessage', { method: 'POST' });
  await p.click('#d-actions button:has-text("Продано")');
  await p.click('#qs-quick .chip >> nth=-1');
  await p.click('#qs-ok');
  await p.waitForSelector('#screen-list:not(.hidden)');
  assert.deepEqual(await alerts(p), []);              // никакой «ошибки 500»
  assert.equal((await titles(p)).length, 0);
  await p.close();
});

test('устаревший экран: товар закрыли с другого телефона — приложение объясняет и обновляет список', async () => {
  const p = await openApp();
  await addViaForm(p, { code: '4600000000017', name: 'Хлеб', qty: 2 });
  await p.click('.item >> nth=0');
  const id = await p.evaluate(() => current.id);
  await p.evaluate((id) => fetch(`/api/products/${id}/sold`, { method: 'POST', headers: { 'X-Init-Data': Telegram.WebApp.initData, 'Content-Type': 'application/json' }, body: '{}' }), id);
  await p.click('#d-actions button:has-text("Списать")');
  await p.click('#qs-ok');
  await p.waitForSelector('#screen-list:not(.hidden)');
  assert.match((await alerts(p)).join(), /продан полностью/);
  assert.equal((await titles(p)).length, 0);
  await p.close();
});

test('изменение товара', async () => {
  const p = await openApp();
  await addViaForm(p, { code: '4601234567890', name: 'Молоко', qty: 6 });
  await p.click('.item >> nth=0');
  await p.click('#d-actions button:has-text("Изменить")');
  await p.fill('#f-name', 'Молоко Простоквашино');
  await p.fill('#f-qty', '4');
  await mainButton(p);
  await p.waitForSelector('#screen-detail:not(.hidden)');
  assert.equal(await p.textContent('#d-title'), 'Молоко Простоквашино');
  assert.equal(await shelfText(p), '4 шт.');
  await p.close();
});

test('своя тема окрашивает шапку, фон и нижнюю панель Telegram', async () => {
  for (const scheme of ['light', 'dark']) {
    const p = await openApp({ scheme });
    const calls = await p.evaluate(() => __tg);
    const bg = scheme === 'dark' ? '#0e1411' : '#f3f5f2';
    assert.ok(calls.includes('bg ' + bg) && calls.includes('bottom ' + bg), calls.join());
    assert.ok(calls.some((c) => c.startsWith('main {"color"')));
    assert.equal(await p.getAttribute('html', 'data-theme'), scheme);
    await p.close();
  }
});

test('режим Telegram Serverless: всё через relay, телефон не ходит на сервер напрямую', async () => {
  const pub = path.join(ROOT, 'miniapp', 'public');
  execFileSync('node', [path.join(ROOT, 'miniapp', 'build.mjs')], { env: { ...process.env, FORWARD_SECRET: 'test_webhook_secret_123456' } });
  globalThis.TGCLOUD_TEST_BACKEND = BASE;
  const relay = (await import(path.join(ROOT, 'miniapp/tgcloud/endpoints/relay.js'))).default;
  const direct = [];
  const p = await openApp({
    url: 'https://app1.tgcloud.ai/',
    setup: async (page) => {
      await page.exposeFunction('__relay', async (name, input) => {
        try { return { result: await relay(input, { initData: { user: { id: 7 } } }) }; }
        catch (e) { return { err: { message: e.description || e.message, type: 'ENDPOINT_ERROR' } }; }
      });
      await page.route('https://app1.tgcloud.ai/**', (r) => {
        let u = new URL(r.request().url()).pathname;
        if (u === '/') u = '/index.html';
        const f = path.join(pub, u);
        if (!fs.existsSync(f)) return r.fulfill({ status: 404, body: 'nf' });
        r.fulfill({ body: fs.readFileSync(f), contentType: u.endsWith('.html') ? 'text/html' : 'text/javascript' });
      });
      page.on('request', (r) => { if (r.url().startsWith(BASE)) direct.push(r.url()); });
    },
  });
  await addViaForm(p, { code: '4601234567890', name: 'Молоко', qty: 2, days: 3 });
  await p.waitForFunction(() => (document.querySelector('img.thumb')?.getAttribute('src') || '').startsWith('data:image/jpeg'));
  assert.deepEqual(await titles(p), ['Молоко']);
  const bad = await p.evaluate(() => relayCall('/webhook', 'POST', {}).then(() => 'пропустил', (e) => e.message));
  assert.equal(bad, 'Неверный запрос');
  assert.deepEqual(direct, []);
  await p.close();

  // сообщения группы, пришедшие на Serverless, пересылаются боту
  const forward = (await import(path.join(ROOT, 'miniapp/tgcloud/handlers/message.js'))).default;
  await forward({}, { update: { update_id: 1, message: { message_id: 1, date: 1700000000, chat: { id: -1001234567890, type: 'supergroup' }, from: { id: 7, is_bot: false, first_name: 'Аня' }, text: '/help', entities: [{ type: 'bot_command', offset: 0, length: 5 }] } } });
  const calls = await (await fetch(BASE + '/__test/calls')).json();
  assert.ok(calls.some(([m, t]) => m === 'SendMessage' && /Бот учёта сроков/.test(t || '')));
});
