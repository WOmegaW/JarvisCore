// Адрес сервера бота. Запросы к нему идут с серверов Telegram, поэтому
// работают, даже если с телефона этот сайт недоступен.
// (TGCLOUD_TEST_BACKEND задают только браузерные тесты — на платформе его нет.)
export const BACKEND = globalThis.TGCLOUD_TEST_BACKEND || 'https://carbonara.pythonanywhere.com';
