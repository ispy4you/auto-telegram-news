"""Разбор .env и сборка строки подключения в скрипте развёртывания.

Сам скрипт ходит в API Timeweb, но эти две функции — чистые, и ошибка в них
стоит дорого: приложение создаётся один раз, переменные потом не исправить.
"""

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "deploy_timeweb.py"
_SPEC = importlib.util.spec_from_file_location("deploy_timeweb", _PATH)
deploy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(deploy)


def test_env_file_keeps_values_and_drops_noise(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# комментарий\n"
        "\n"
        "APP_ENV=production\n"
        'APP_SECRET_KEY="в кавычках"\n'
        "DATABASE_URL=postgresql://user:pa=ss@host:5432/db\n"
        "  SPACED  =  по краям  \n"
        "TIMEWEB_AI_GATEWAY_API_KEY=\n",
        encoding="utf-8",
    )

    variables = deploy.read_env_file(str(path))

    assert variables == {
        "APP_ENV": "production",
        "APP_SECRET_KEY": "в кавычках",
        # Пароль с равенством внутри не должен обрезаться по первому знаку.
        "DATABASE_URL": "postgresql://user:pa=ss@host:5432/db",
        "SPACED": "по краям",
        "TIMEWEB_AI_GATEWAY_API_KEY": "",
    }


def test_database_url_prefers_private_address():
    cluster = {
        "port": 5432,
        "host": "public.example.net",
        "networks": [
            {"ips": [{"type": "ipv_6", "ip": "::1"}, {"type": "ipv_4", "ip": "10.0.0.4"}]}
        ],
    }

    url = deploy.database_url(cluster, "tgnews", "secret", "tgnews")

    assert url == "postgresql://tgnews:secret@10.0.0.4:5432/tgnews"


def test_database_url_falls_back_to_public_host():
    cluster = {"port": 5432, "host": "public.example.net", "networks": []}

    url = deploy.database_url(cluster, "tgnews", "secret", "tgnews")

    assert url == "postgresql://tgnews:secret@public.example.net:5432/tgnews"


def test_database_url_accepts_explicit_host():
    cluster = {"port": 5432, "networks": [{"ips": [{"type": "ipv_4", "ip": "10.0.0.4"}]}]}

    url = deploy.database_url(cluster, "tgnews", "secret", "tgnews", host="10.0.0.2")

    assert url == "postgresql://tgnews:secret@10.0.0.2:5432/tgnews"


def test_database_url_complains_without_address():
    with pytest.raises(SystemExit):
        deploy.database_url({"port": 5432, "networks": []}, "tgnews", "secret", "tgnews")
