import { forwardUpdate } from '../lib/backend.js';

// Сообщения в группе обрабатывает бот на PythonAnywhere — пересылаем ему.
export default async function (message, ctx) {
  await forwardUpdate(ctx.update);
}
