import { forwardUpdate } from '../lib/backend.js';

// Нажатия кнопок под напоминаниями — тоже на PythonAnywhere.
export default async function (query, ctx) {
  await forwardUpdate(ctx.update);
}
