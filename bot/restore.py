"""Восстановление из резервной копии.

Запуск на сервере:  python -m bot.restore mixalmacen-backup-2026-11-23.zip
"""
import os
import sys

from .backup import restore_from_zip
from .config import load_config


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Укажите архив: python -m bot.restore mixalmacen-backup-ГГГГ-ММ-ДД.zip")
    cfg = load_config()
    photo_dir = os.path.join(os.path.dirname(cfg.db_path), "photos")
    r = restore_from_zip(sys.argv[1], cfg.db_path, photo_dir)
    print(f"Готово: восстановлено товаров — {r['products']}, фото — {r['photos']}.")
    if r["previous"]:
        print(f"Прежняя база сохранена: {r['previous']}")
    print("Теперь нажмите Web → Reload на PythonAnywhere.")


if __name__ == "__main__":
    main()
