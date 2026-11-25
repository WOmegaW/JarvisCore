# Бот учёта сроков годности (Telegram)

Бот для рабочей группы магазина (5 человек). Сотрудники добавляют товар — **фото, штрихкод, срок годности** — а бот сам напоминает:

- 🟠 **за 2 дня до окончания срока** — «Поставить скидку 50%» (с фото товара и кнопкой «✅ Скидка поставлена»);
- 🔴 **в день окончания срока** — «Списать товар» (кнопка «✅ Списано»);
- ⚠️ каждое утро — список просроченных товаров, которые так и не отметили как списанные.

Кто нажал кнопку — записывается в карточку товара, вся группа это видит.

## Как пользоваться

**Добавить товар пошагово:** `/add` → фото → штрихкод → название (или «-») → срок годности.

**Добавить быстро** — одно фото с подписью:
```
4601234567890 25.12.2026 Молоко 3.2% 6шт
```
(штрихкод, дата, название, количество; дату лучше писать с годом). Если товар с этим штрихкодом
уже вносили, название подставится само — его можно не писать.

| Команда | Что делает |
|---|---|
| `/app` | открыть приложение внутри Telegram |
| `/soon` | что нужно уценить или списать |
| `/list` | все товары на учёте |
| `/show 12` | карточка товара №12 с фото |
| `/find 4601234567890` | поиск по штрихкоду |
| `/writeoff 12` | списать весь остаток товара №12 (`/writeoff 12 2` — только 2 шт.) |
| `/sold 12` | отметить, что товар №12 продан (`/sold 12 3` — продано 3 шт.) |
| `/del 12` | удалить ошибочную запись |
| `/check` | проверить сроки прямо сейчас |
| `/backup` | резервная копия всех товаров (в личный чат; настройка — `/backup_here` в личке с ботом) |
| `/cancel` | отменить добавление |
| `/id` | показать ID группы |

## Установка

1. **Создайте бота** у [@BotFather](https://t.me/BotFather): `/newbot` → получите токен.
2. **Отключите режим приватности**, иначе бот не увидит фото и ответы в группе:
   @BotFather → `/mybots` → ваш бот → *Bot Settings* → *Group Privacy* → **Turn off**.
   (Или просто сделайте бота администратором группы.)
3. **Создайте группу** в Telegram, добавьте туда 5 сотрудников и бота.
4. Настройте и запустите:
   ```bash
   cp .env.example .env      # впишите BOT_TOKEN
   pip install -r requirements.txt
   python -m bot.main
   ```
5. Напишите в группе `/id`, впишите полученное число в `ALLOWED_CHAT_IDS` в `.env` и перезапустите бота — теперь посторонние не смогут им пользоваться.

### Через Docker
```bash
docker build -t expiry-bot .
docker run -d --restart=always --env-file .env -v $(pwd)/data:/app/data expiry-bot
```
В Docker-образе включено распознавание штрихкода с фотографии (pyzbar). Без Docker это
необязательно: поставьте `libzbar0` (`sudo apt install libzbar0`) и `pip install pyzbar Pillow`.
Если распознать не удалось — бот попросит ввести цифры вручную.

Бот должен работать постоянно (VPS, домашний ПК/Raspberry Pi и т.п.), иначе он не пришлёт напоминания.

## Установка на PythonAnywhere (бесплатно, можно с iPhone)

На бесплатном тарифе бот работает через webhook: Telegram сам присылает сообщения на ваш сайт
`ЛОГИН.pythonanywhere.com`, а раз в день расписание проверяет сроки и рассылает напоминания.

1. Зарегистрируйтесь на pythonanywhere.com (тариф *Beginner*, бесплатный).
2. **Consoles → Bash** и выполните по очереди (`ТОКЕН` — токен от @BotFather):
   ```bash
   git clone -b claude/telegram-expiry-tracking-bot-6eeciw https://github.com/WOmegaW/JarvisCore
   cd JarvisCore
   python3.12 -m venv ~/venv && ~/venv/bin/pip install -r requirements.txt
   echo "BOT_TOKEN=ТОКЕН" > .env && echo "PROXY_URL=http://proxy.server:3128" >> .env
   ```
3. **Web → Add a new web app → Manual configuration → Python 3.12**. Затем на вкладке Web:
   - **Virtualenv**: `/home/ЛОГИН/venv`
   - откройте **WSGI configuration file**, удалите всё и вставьте:
     ```python
     import sys
     sys.path.insert(0, "/home/ЛОГИН/JarvisCore")
     from flask_app import app as application
     ```
   - нажмите зелёную кнопку **Reload**.
4. Снова в Bash: `cd ~/JarvisCore && ~/venv/bin/python -m bot.set_webhook ЛОГИН`
5. **Tasks** → ежедневное задание на **07:00** (время там по UTC: это 08:00 по Испании зимой и 09:00 летом):
   ```
   cd /home/ЛОГИН/JarvisCore && /home/ЛОГИН/venv/bin/python -m bot.daily
   ```
6. Напишите в группе `/id`, затем в Bash:
   `echo "ALLOWED_CHAT_IDS=ЧИСЛО" >> ~/JarvisCore/.env` и нажмите **Reload** на вкладке Web.

Особенности бесплатного тарифа: сайт и задание нужно периодически продлевать кнопкой на
вкладках Web и Tasks (PythonAnywhere присылает письмо-напоминание); утренние напоминания приходят
один раз в день по расписанию; распознавания штрихкода с фото нет — цифры вводятся вручную.
Если что-то не работает — смотрите **Web → Error log**.

## 📱 Приложение внутри Telegram (Mini App)

Удобный интерфейс поверх бота:
- список товаров, сгруппированный по дням («Завтра», «Через 2 дня», «На этой неделе», «Позже»);
- добавление с фото, сканером штрихкода (камерой или прямо с фото товара), количеством и датой;
- **автоподстановка**: если штрихкод уже вносили — название и фото подставятся сами, видно, сколько такого товара уже на полке;
- карточка товара: полоска срока, фото на весь экран, кнопки «Скидка поставлена», «Продано», «Списать»
  (можно частично — «продано 2 из 6»), «Изменить» (дата, количество, название, фото).

Работает на том же сайте, что и бот (`/app`).

Включение (один раз):
1. @BotFather → ваш бот → **Bot Settings → Configure Mini App → Enable Mini App** и укажите адрес
   `https://ЛОГИН.pythonanywhere.com/app`.
2. Напишите в группе `/app` и закрепите сообщение с кнопкой «📦 Открыть учёт».

Доступ: приложение проверяет подпись Telegram и что пользователь состоит в группе.
Действия из приложения дублируются сообщением в группу. Напоминания получают кнопку «📱 Открыть учёт».

## 🌍 Приложение через серверы Telegram (работает с любого интернета)

В Испании операторы во время матчей LaLiga блокируют многие хостинги, а некоторые сети
просто не пускают на PythonAnywhere. Тогда приложение (`/app`) с мобильного интернета не открывается,
хотя бот в чате работает: сообщения боту доставляет сам Telegram.

Решение: приложение размещается на **Telegram Serverless** (`https://app<номер>.tgcloud.ai`).
Телефон общается только с Telegram, а Telegram уже сам, со своих серверов, ходит на PythonAnywhere.

```
телефон ──► Telegram (страница + endpoint relay) ──► PythonAnywhere (бот, база, напоминания)
```

- `miniapp/` — проект Telegram Serverless: страница (копия `webapp/`), endpoint `relay` (мост к серверу)
  и обработчики, которые пересылают сообщения группы боту на PythonAnywhere.
- Утренние напоминания по-прежнему отправляет задание на PythonAnywhere (у Serverless нет таймера).
- Деплой делает GitHub Actions (`.github/workflows/miniapp.yml`) при каждом изменении `webapp/` или `miniapp/`.

**Настройка (один раз):**
1. PythonAnywhere, Bash:
   ```bash
   cd ~/JarvisCore && git pull && ~/venv/bin/pip install -r requirements.txt
   echo "WEBHOOK_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" >> .env
   ~/venv/bin/python -m bot.set_webhook ЛОГИН
   grep WEBHOOK_SECRET .env        # это значение понадобится в шаге 3
   ```
   и **Web → Reload**.
2. @BotFather → бот → **Serverless** → включить → **CLI Access** → скопировать токен (`app…:…`).
3. GitHub → репозиторий → **Settings → Secrets and variables → Actions → New repository secret**:
   `TGCLOUD_TOKEN` = токен из шага 2, `FORWARD_SECRET` = значение `WEBHOOK_SECRET` из шага 1.
4. GitHub → **Actions → Deploy Mini App to Telegram Serverless → Run workflow**.
   В итогах запуска будет адрес приложения `https://app….tgcloud.ai/`.
5. @BotFather → бот → настройки Mini App → заменить адрес на этот.

После этого Telegram сам доставляет сообщения через Serverless (обработчики пересылают их на
PythonAnywhere). Если когда-нибудь запустить `bot.set_webhook`, бот продолжит работать напрямую —
секрет тот же; вернуть доставку через Serverless можно повторным запуском workflow.

## Настройки (`.env`)

| Параметр | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — | токен от @BotFather |
| `ALLOWED_CHAT_IDS` | пусто (все чаты) | ID группы (через запятую, если несколько) |
| `STORE_TZ` | `Europe/Madrid` | часовой пояс магазина |
| `NOTIFY_TIME` | `09:00` | с какого времени слать утренние напоминания |
| `DISCOUNT_DAYS_BEFORE` | `2` | за сколько дней ставить скидку |
| `DISCOUNT_PERCENT` | `50` | размер скидки |
| `DB_PATH` | `data/expiry.db` | файл базы (SQLite) |
| `WEBHOOK_SECRET` | из токена | секрет для сообщений от Telegram (и от Serverless — `FORWARD_SECRET`) |
| `WEBAPP` | `1` | `0` — выключить приложение (кнопки «Открыть учёт») |
| `PROXY_URL` | пусто | прокси (на PythonAnywhere: `http://proxy.server:3128`) |

Если товар добавили, когда срок уже близко (например, за день до окончания), напоминание приходит сразу.

## 🔄 Автообновление сервера

После настройки сервер обновляется сам: GitHub прогоняет тесты и, если всё зелёное,
делает на PythonAnywhere `git pull` и нажимает **Reload** через API.

1. PythonAnywhere → **Account → API token → Create a new API token**, скопировать.
2. Bash на PythonAnywhere:
   ```bash
   cd ~/JarvisCore && git pull
   echo "DEPLOY_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" >> .env
   grep DEPLOY_SECRET .env
   ```
   и **Web → Reload** (последний раз вручную).
3. GitHub → **Settings → Secrets and variables → Actions**: `PA_API_TOKEN` (из шага 1) и
   `DEPLOY_SECRET` (значение из шага 2).

Ход обновления видно во вкладке **Actions → Tests → «Обновить бота на PythonAnywhere»**.

## 🆘 Восстановление с нуля

Если пропал сервер — пошаговая инструкция в [docs/ВОССТАНОВЛЕНИЕ.md](docs/ВОССТАНОВЛЕНИЕ.md).

## 💾 Резервные копии

Чтобы не сканировать товары заново, если с сервером что-то случится:

1. Напишите боту в **личный чат** (не в группу) команду `/backup_here`.
2. Каждое утро бот будет присылать туда архив `mixalmacen-backup-ГГГГ-ММ-ДД.zip`:
   полная база, таблица `товары.csv` (открывается в Excel) и фото всех товаров.
   Telegram хранит эти файлы бессрочно. `/backup` — сделать копию прямо сейчас.
3. Восстановление: загрузите архив на PythonAnywhere в папку `~/JarvisCore` (Files → Upload a file) и выполните
   `cd ~/JarvisCore && ~/venv/bin/python -m bot.restore mixalmacen-backup-ГГГГ-ММ-ДД.zip`, затем Web → Reload.

## Тесты

```bash
pip install -r requirements-dev.txt
python -m pytest                       # бот, API мини-приложения, база, прокси
cd tests/e2e && npm install && npx playwright install chromium && npm test   # мини-приложение в браузере
```
GitHub Actions запускает оба набора при каждом изменении (вкладка **Actions → Tests**).
Подробности об устройстве проекта — в `CLAUDE.md`.
