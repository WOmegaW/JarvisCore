#!/usr/bin/env bash
# Установка бота на сервер Ubuntu/Debian одной командой.
# Запуск (из папки с ботом):  bash install.sh
set -euo pipefail

cd "$(dirname "$0")"
APP_DIR="$(pwd)"

echo "==> Устанавливаю Python и библиотеки..."
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip libzbar0 nano

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt pyzbar==0.1.9 Pillow==12.3.0

if [ ! -f .env ]; then
    cp .env.example .env
    echo
    read -r -p "Вставьте токен бота от @BotFather и нажмите Enter: " TOKEN
    sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${TOKEN}|" .env
fi

echo "==> Настраиваю автозапуск..."
cat > /etc/systemd/system/expiry-bot.service <<EOF
[Unit]
Description=Telegram-бот учёта сроков годности
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python -m bot.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now expiry-bot
systemctl restart expiry-bot
sleep 3
systemctl --no-pager --lines=5 status expiry-bot || true

echo
echo "Готово! Бот запущен и будет работать постоянно (и после перезагрузки сервера)."
echo "Логи:          journalctl -u expiry-bot -f"
echo "Перезапуск:    systemctl restart expiry-bot"
echo "Настройки:     nano ${APP_DIR}/.env   (потом перезапуск)"
