#!/usr/bin/env python3
"""Развёртывание панели в Timeweb Cloud: кластер PostgreSQL и приложение.

Почему скрипт, а не кнопки в панели: шагов немного, но каждый норовит быть
забытым — приложение должно попасть в ту же приватную сеть, что и база, а
переменные окружения задаются только при создании и потом правятся лишь
руками. Собранный один раз порядок дешевле восстанавливать командой.

Нужен токен API из https://timeweb.cloud/my/api-keys в TIMEWEB_CLOUD_TOKEN.
Команды выполняются по очереди:

    scripts/deploy_timeweb.py catalog
    scripts/deploy_timeweb.py db    --preset ID --zone ZONE [--network ID] [--project ID]
    scripts/deploy_timeweb.py own   --cluster ID
    scripts/deploy_timeweb.py app   --cluster ID --provider ID --repository ID \
                                    --commit SHA --preset ID [--network ID] [--project ID]
    scripts/deploy_timeweb.py status

`catalog` печатает всё, из чего берутся идентификаторы для остальных команд.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.timeweb.cloud/api/v1"
MCP = API + "/mcp/search"


def token() -> str:
    value = os.environ.get("TIMEWEB_CLOUD_TOKEN", "").strip()
    if not value:
        sys.exit("Нет токена: положите его в TIMEWEB_CLOUD_TOKEN")
    return value


def _open(request: urllib.request.Request):
    try:
        return urllib.request.urlopen(request, timeout=120)
    except urllib.error.HTTPError as error:
        sys.exit(f"API ответил {error.code}: {error.read().decode()[:400]}")
    except urllib.error.URLError as error:
        sys.exit(f"Не достучались до API: {error.reason}")


def rest(path: str) -> dict:
    request = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + token()})
    with _open(request) as response:
        return json.load(response)


def mcp(tool: str, namespace: str, arguments: dict) -> dict:
    """Вызов инструмента hosted MCP: он умеет то, чего нет в обычном REST."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "execute_tool",
            "arguments": {"tool_id": tool, "namespace": namespace, "arguments": arguments},
        },
    }
    request = urllib.request.Request(
        MCP,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + token(),
            "Content-Type": "application/json",
            # Ответ приходит потоком событий, одной строкой "data: {...}".
            "Accept": "application/json, text/event-stream",
        },
    )
    with _open(request) as response:
        body = response.read().decode()
    for line in body.splitlines():
        if line.startswith("data: "):
            body = line[6:]
            break
    answer = json.loads(body)
    if "error" in answer:
        sys.exit("MCP вернул ошибку: " + json.dumps(answer["error"], ensure_ascii=False))
    return json.loads(answer["result"]["content"][0]["text"])


def confirmed(tool: str, namespace: str, arguments: dict) -> dict:
    """Платные операции двухшаговые: сначала токен подтверждения, потом действие."""
    first = mcp(tool, namespace, arguments)
    ticket = first.get("confirm_token")
    if not ticket:
        return first
    print(first.get("operation_summary", "Платформа просит подтверждение"))
    return mcp(tool, namespace, dict(arguments, confirm_token=ticket))


def read_env_file(path: str) -> dict:
    """Переменные из .env: без комментариев, пустых строк и внешних кавычек."""
    variables = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            variables[key.strip()] = value.strip().strip('"').strip("'")
    return variables


def database_url(cluster: dict, login: str, password: str, dbname: str, host: str = "") -> str:
    """Строка подключения: приватный адрес предпочтительнее публичного.

    Приложение и кластер живут в одной сети, и внутренний адрес не выставляет
    базу в интернет. Если сети нет, адрес придётся передать руками.
    """
    if not host:
        private = (address["ip"]
                   for network in cluster.get("networks") or []
                   for address in network.get("ips") or []
                   if address.get("type") == "ipv_4")
        host = next(private, "") or cluster.get("host") or ""
    if not host:
        sys.exit("У кластера не видно адреса — передайте --db-host")
    return f"postgresql://{login}:{password}@{host}:{cluster['port']}/{dbname}"


def cluster_credentials(cluster_id: int) -> tuple:
    """Логин, пароль и логическая база кластера — из API, а не из чужих рук."""
    users = rest(f"/databases/{cluster_id}/admins")["admins"]
    instances = rest(f"/databases/{cluster_id}/instances")["instances"]
    if len(users) != 1 or len(instances) != 1:
        sys.exit("В кластере не один пользователь или не одна база — укажите их сами")
    return users[0], instances[0]


def cmd_catalog(args) -> None:
    print("## Проекты")
    for project in rest("/projects")["projects"]:
        print(f"  {project['id']:>10}  {project['name']}")

    print("## Приватные сети")
    for vpc in mcp("list_vpcs", "vpc", {}).get("vpcs", []):
        print(f"  {vpc['id']}  {vpc['name']}  {vpc['cidr']}  {vpc['location']}")

    print("## Тарифы PostgreSQL")
    for preset in rest("/presets/dbs").get("databases_presets", []):
        if str(preset.get("type", "")).startswith("postgres"):
            print(f"  {preset['id']:>6}  {preset['cpu']} CPU  {preset['ram']} МБ  "
                  f"{preset['disk']} МБ  {preset['location']}")

    print("## Тарифы приложений (backend)")
    for preset in rest("/presets/apps").get("backend_presets", []):
        print(f"  {preset['id']:>6}  {preset['cpu']} CPU  {preset['ram']} МБ  "
              f"{preset['disk']} МБ  {preset['location']}")

    print("## Репозитории")
    for provider in rest("/vcs-provider")["providers"]:
        print(f"  провайдер {provider['provider_id']}  {provider['provider']}  {provider['login']}")
        for repo in rest(f"/vcs-provider/{provider['provider_id']}")["repositories"]:
            hook = "вебхуки разрешены" if repo["is_allowed_webhook"] else "вебхуки запрещены"
            print(f"    {repo['id']}  {repo['full_name']}  {hook}")


def cmd_db(args) -> None:
    arguments = {
        "name": args.name,
        "type": args.engine,
        "preset_id": args.preset,
        "admin": {"login": args.login},   # пароль генерирует платформа и не отдаёт
        "instance": {"name": args.instance},
        "availability_zone": args.zone,
    }
    if args.network:
        arguments["network_id"] = args.network
    if args.project:
        arguments["project_id"] = args.project
    print(json.dumps(confirmed("create_database_cluster", "databases", arguments),
                     ensure_ascii=False, indent=2))


def cmd_own(args) -> None:
    """Пользователь должен владеть своей базой, иначе миграции не создадут таблицы.

    В PostgreSQL 15 и новее право на создание в схеме public есть только у
    владельца базы. Платформа же выдаёт пользователя без владения, и первый
    запуск падает на правах.
    """
    user, instance = cluster_credentials(args.cluster)
    print(json.dumps(confirmed("update_database_instance", "databases", {
        "cluster_id": args.cluster,
        "instance_id": instance["id"],
        "owner_id": user["id"],
    }), ensure_ascii=False, indent=2))


def cmd_app(args) -> None:
    cluster = rest(f"/databases/{args.cluster}")["db"]
    user, instance = cluster_credentials(args.cluster)
    variables = read_env_file(args.env_file)
    variables["DATABASE_URL"] = database_url(
        cluster, user["login"], user["password"], instance["name"], args.db_host
    )
    print("Переменные окружения:", ", ".join(sorted(variables)))

    arguments = {
        "type": "backend",
        "name": args.name,
        "language": "docker",
        "framework": "docker",
        "build_cmd": "",
        "run_cmd": "",                 # команда запуска своя, из CMD в Dockerfile
        "preset_id": args.preset,
        "provider_id": args.provider,
        "repository_id": args.repository,
        "branch_name": args.branch,
        "commit_sha": args.commit,
        "is_auto_deploy": args.auto_deploy,
        "health_check": "/health",
        "envs": variables,
    }
    if args.network:
        arguments["network"] = {"id": args.network}
    if args.project:
        arguments["project_id"] = args.project
    print(json.dumps(confirmed("create_app", "apps", arguments), ensure_ascii=False, indent=2))


def cmd_status(args) -> None:
    for app in rest("/apps")["apps"]:
        domains = ", ".join(d["fqdn"] for d in app.get("domains") or []) or "домена ещё нет"
        print(f"{app['id']}  {app['name']}  {app['status']}  {app.get('ip') or '-'}  {domains}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("catalog", help="показать проекты, сети, тарифы и репозитории")

    database = commands.add_parser("db", help="создать кластер PostgreSQL")
    database.add_argument("--name", default="tgnews")
    database.add_argument("--engine", default="postgres17")
    database.add_argument("--preset", type=int, required=True)
    database.add_argument("--zone", required=True, help="зона тарифа, например ams-1")
    database.add_argument("--login", default="tgnews")
    database.add_argument("--instance", default="tgnews", help="имя логической базы")
    database.add_argument("--network")
    database.add_argument("--project", type=int)

    owner = commands.add_parser("own", help="сделать пользователя владельцем базы")
    owner.add_argument("--cluster", type=int, required=True)

    application = commands.add_parser("app", help="создать приложение из репозитория")
    application.add_argument("--cluster", type=int, required=True)
    application.add_argument("--provider", required=True)
    application.add_argument("--repository", required=True)
    application.add_argument("--commit", required=True, help="полный sha из 40 знаков")
    application.add_argument("--preset", type=int, required=True)
    application.add_argument("--name", default="auto-telegram-news")
    application.add_argument("--branch", default="main")
    application.add_argument("--env-file", default=".env")
    application.add_argument("--db-host", default="", help="если адрес базы не виден из API")
    application.add_argument("--network")
    application.add_argument("--project", type=int)
    application.add_argument("--auto-deploy", action="store_true",
                             help="вебхук на пуш; нужен репозиторий, где они разрешены")

    commands.add_parser("status", help="состояние приложений аккаунта")

    args = parser.parse_args()
    {"catalog": cmd_catalog, "db": cmd_db, "own": cmd_own,
     "app": cmd_app, "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()
