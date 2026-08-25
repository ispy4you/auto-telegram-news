"""Импорт приложения не должен делать работу.

Раньше main.py на уровне модуля создавал таблицы, мигрировал схему, правил
данные и читал файл сессии. Из-за этого приложение нельзя было импортировать
в тестах без живой БД, а недоступная база означала не деградацию, а несобранный
модуль — контейнер не поднимался вовсе.
"""

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_importing_the_app_does_not_touch_the_database():
    env = {
        **os.environ,
        # Порт, на котором заведомо никого нет: если импорт полезет в БД, он упадёт.
        "DATABASE_URL": "postgresql://nobody:nobody@127.0.0.1:1/nonexistent",
        "APP_ENV": "local",
    }

    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
