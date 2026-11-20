# Mars Rover API

Открытая неофициальная замена архивированного Mars Rover Photos API. Монолит на Python: забирает фото и метаданные марсоходов из фидов JPL, хранит их в PostgreSQL и отдаёт через две версии API: **v1** — совместимая со старым API (клиенты переезжают сменой хоста), **v2** — собственная. Фронтенд — позже, на v2.

## Документы — источник правды
- `docs/LEGACY-API.md` — контракт v1: поведение старого API, восстановленное по его коду, тестовые векторы, допустимые расхождения.
- `docs/API.md` — контракт v2. От обоих контрактов отступать нельзя без моего подтверждения.
- `docs/DATA-SOURCES.md` — источники, их поля и причуды, таблица сопоставления.
- `docs/ARCHITECTURE.md` — слои, структура кода, модель данных, синхронизация, решения.
- `docs/PLAN.md` — этапы, критерии готовности, журнал.

Перед работой прочитай раздел, который касается задачи. Если документ и код расходятся — сообщи, сам не выбирай.

## Стек
Python 3.13+, FastAPI, SQLAlchemy 2.0 (async) + asyncpg, Alembic, httpx + tenacity, APScheduler, pydantic-settings. Тесты: pytest, pytest-asyncio, respx. Инструменты: uv, ruff. БД: PostgreSQL 16 в Docker Compose.

## Команды
- поднять БД: `docker compose up -d db` — Postgres 16 на `localhost:5433`, базы `mars` (разработка) и `mars_test` (тесты)
- зависимости: `uv sync`; добавить пакет: `uv add <pkg>` (dev: `uv add --dev <pkg>`)
- миграции: `uv run alembic upgrade head`; новая: `uv run alembic revision --autogenerate -m "<msg>"`
- сервер: `uv run uvicorn app.main:app --reload`
- тесты: `uv run pytest -q -n 12` (параллельно, у каждого воркера своя БД `mars_test_gwN`, ~3 мин; без `-n` — последовательно, ~16 мин); по слоям: `-m unit`, `-m integration`, `-m api`. Тестовую БД мигрирует сама фикстура `migrated_db` — подключай её в тестах, которым нужна база.
- фронтенд (`frontend/`, Node 26): `npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` → `frontend/dist`, сервер раздаёт его на `/`; `npm run dev` — с прокси на `127.0.0.1:8000`
- линтер: `uv run ruff check .` и `uv run ruff format .`
- синхронизация: `uv run python -m app.cli sync --rover perseverance --from-sol 2029 --to-sol 2031`

Работаем на Windows: в командах — только `uv run ...`, `docker compose ...` и `npm ...` в `frontend/`, без make и bash-специфики.

## Старый проект
Оригинал — `github.com/corincerami/mars-photo-api` (Ruby, GPL-3.0, архив с 2025-10-08). Его можно склонировать рядом (`../mars-photo-api-legacy`) и читать для сверки поведения. Код оттуда не копируем — только поведение, описанное в `LEGACY-API.md`.

## Правила кода
- Слои: роутер → сервис → репозиторий → БД. Формат ответов NASA знает только `app/sources/`.
- Всё async. HTTP к источникам — только через общий клиент из `app/sources/http.py`.
- Настройки — только через `Settings`. Никаких секретов, токенов и URL с ключами в коде.
- Pydantic-схемы ответов отдельно от ORM-моделей.
- Каждая миграция обратима (`downgrade` работает). Автогенерацию проверяй глазами.
- Логи через `logging`, не `print`.
- Язык: код, комментарии, docstring, сообщения коммитов и README — на английском. Документы в `docs/` — на русском.
- Проект неофициальный: никаких логотипов NASA и JPL, никаких формулировок про одобрение NASA. Авторство снимков (`credit`) всегда сохраняется.

## Правила тестов
- Тесты не ходят в сеть: источники мокаются через respx на записанных фикстурах из `tests/fixtures/upstream/`.
- БД в тестах — настоящий Postgres (`TEST_DATABASE_URL`), каждый тест изолирован.
- Маркеры: `unit`, `integration`, `api`.
- Падающий тест не ослабляют, чтобы он позеленел: сначала выясняют, кто не прав — код, тест или контракт.

## Команда ролей
- `/stage <N>` — провести этап из `docs/PLAN.md`: план → моё «ок» → `backend-dev` и `qa-engineer` параллельно → прогон → `reviewer` → итог.
- Роли по отдельности: `@agent-architect` (план этапа, модель данных, черновик контракта, ADR — до разработки), `@agent-backend-dev`, `@agent-qa-engineer`, `@agent-data-engineer` (проверка источников, загрузки, сверка, производительность на полном объёме), `@agent-reviewer`, `@agent-designer` (наброски фронтенда), `@agent-devops` (CI, образ, развёртывание), `@agent-frontend-dev` (фронтенд).

Владение файлами, чтобы параллельная работа не конфликтовала:
- `backend-dev` — `app/`, `migrations/`, `pyproject.toml`, `uv.lock`, `docker-compose.yml`, `.env.example`, `alembic.ini`;
- `qa-engineer` — `tests/`, `scripts/record_fixtures.py`;
- агенты не запускают команды в фоне (`run_in_background`): каждое промежуточное уведомление будит основную сессию и стоит токенов;
- тимлид (основная сессия) — `docs/` (кроме перечисленного ниже), `README.md`, `CLAUDE.md`;
- `architect` — `docs/ARCHITECTURE.md`, `docs/proposals/`;
- `data-engineer` — `docs/DATA-SOURCES.md`, `docs/reports/`, `scripts/` (кроме `record_fixtures.py`);
- `designer` — `design/` (HTML-макеты будущего фронтенда на данных v2);
- `devops` — `Dockerfile`, `.dockerignore`, `.github/`, `deploy/` (образ, CI, продакшен-развёртывание, бэкапы, мониторинг);
- `frontend-dev` — `frontend/` (веб-интерфейс Sol Index на API v2 по макетам из `design/`);
- `reviewer` — ничего не меняет.

## Автономия architect и data-engineer
- Тимлид запускает `architect` и `data-engineer` сам, без моего «ок» на запуск. Мои «ок» на контракты, запросы к внешним сайтам, коммиты и удаление данных остаются в силе.
- Бюджет: один запуск `architect` или `data-engineer` — не больше 5% недельного лимита токенов. Тимлид смотрит недельный процент до запуска и после отчёта, разницу записывает в журнал расхода (память сессии). Это оценка: в разницу попадает и всё, что шло параллельно.
- Если запуск очевидно не уложится в 5%, он идёт только после моего «ок». Агент, который перевалил за 5% во время работы, доводит текущую задачу до отчёта, продолжения в том же запуске не получает.

## Git
Не коммитить и не пушить без моего «ок». В конце этапа предложи сообщение коммита.
