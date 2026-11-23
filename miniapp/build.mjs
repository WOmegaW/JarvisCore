// Собирает папку public/ (страница приложения) и tgcloud/lib/secret.js перед деплоем.
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const webapp = path.join(here, '..', 'webapp');
const out = path.join(here, 'public');

fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(path.join(out, 'app', 'static'), { recursive: true });
fs.copyFileSync(path.join(webapp, 'index.html'), path.join(out, 'index.html'));
for (const f of fs.readdirSync(path.join(webapp, 'static'))) {
  fs.copyFileSync(path.join(webapp, 'static', f), path.join(out, 'app', 'static', f));
}

const secret = process.env.FORWARD_SECRET || '';
if (!/^[A-Za-z0-9_-]{16,256}$/.test(secret)) {
  console.error('FORWARD_SECRET не задан (16+ символов: латиница, цифры, _ или -). ' +
    'Это то же значение, что WEBHOOK_SECRET в .env на PythonAnywhere.');
  process.exit(1);
}
fs.writeFileSync(path.join(here, 'tgcloud', 'lib', 'secret.js'),
  `// Создаётся автоматически при сборке, в git не хранится.\nexport const FORWARD_SECRET = ${JSON.stringify(secret)};\n`);
console.log('build ok:', fs.readdirSync(out).join(', '));
