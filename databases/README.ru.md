# MeetSync: локальная база данных

PostgreSQL в Docker. Одинаково работает на macOS, Windows и Linux.

Нужно: Docker (подойдёт Docker Desktop) и Python 3.

## 1. Запуск базы

Положите `docker-compose.yml` и `schema.sql` в одну папку, откройте в ней терминал и выполните:

```
docker compose up -d
```

При первом запуске база и все таблицы из `schema.sql` создаются автоматически.

| Действие | Команда |
|---|---|
| Остановить | `docker compose stop` |
| Запустить снова | `docker compose up -d` |
| Сброс (удаляет ВСЕ данные, пересоздаёт таблицы) | `docker compose down -v`, затем `docker compose up -d` |

## 2. Параметры подключения

| Параметр | Значение      |
|----------|---------------|
| Хост     | `localhost`   |
| Порт     | `5432`        |
| База     | `meetsync`    |
| Логин    | `meetsync`    |
| Пароль   | `devpassword` |

DSN: `postgresql://meetsync:devpassword@localhost:5432/meetsync`

Только для разработки. Порт открыт только для этого компьютера.

pgAdmin: правый клик по Servers -> Register -> Server, введите значения выше.

## 3. Подключение из Python

macOS / Linux:

```
python3 -m venv .venv
.venv/bin/python -m pip install "psycopg[binary]"
.venv/bin/python connect_demo.py
```

Windows:

```
py -m venv .venv
.venv\Scripts\python -m pip install "psycopg[binary]"
.venv\Scripts\python connect_demo.py
```

Ожидаемый вывод:

```
Saved and read back:
  test_user in 'Test chat' (active)
Rejected by the database as expected: new row for relation "groups" violates check constraint "groups_telegram_chat_id_check"
```

Два правила для вашего кода:
- Передавайте значения параметрами (`%s` и кортеж), никогда не собирайте SQL через f-строки.
- `with psycopg.connect(...)` — это транзакция: при успехе коммит, при ошибке откат.

## 4. Если что-то пошло не так

| Проблема | Решение |
|---|---|
| `connection refused` / `cannot connect to the Docker daemon` | Запустите Docker Desktop, затем `docker compose up -d` |
| `port is already allocated` | Порт 5432 занят. Остановите то, что его занимает, или замените `5432:5432` на `5433:5432` в `docker-compose.yml` и используйте порт 5433 |
| `relation "users" does not exist` | Таблицы создаются только при первом запуске. Выполните сброс из таблицы выше |
| `password authentication failed` | Проверьте таблицу параметров выше |
