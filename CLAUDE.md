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
Python 3.12+, FastAPI, SQLAlchemy 2.0 (async) + asyncpg, Alembic, httpx + tenacity, APScheduler, pydantic-settings. Тесты: pytest, pytest-asyncio, respx. Инструменты: uv, ruff. БД: PostgreSQL 16 в Docker Compose.

## Команды
- поднять БД: `docker compose up -d db` — Postgres 16 на `localhost:5433`, базы `mars` (разработка) и `mars_test` (тесты)
- зависимости: `uv sync`; добавить пакет: `uv add <pkg>` (dev: `uv add --dev <pkg>`)
- миграции: `uv run alembic upgrade head`; новая: `uv run alembic revision --autogenerate -m "<msg>"`
- сервер: `uv run uvicorn app.main:app --reload`
- тесты: `uv run pytest -q`; по слоям: `-m unit`, `-m integration`, `-m api`. Тестовую БД мигрирует сама фикстура `migrated_db` — подключай её в тестах, которым нужна база
- линтер: `uv run ruff check .` и `uv run ruff format .`
- синхронизация: `uv run python -m app.cli sync --rover perseverance --from-sol 2029 --to-sol 2031`

Работаем на Windows: в командах — только `uv run ...` и `docker compose ...`, без make и bash-специфики.

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
- Роли по отдельности: `@agent-backend-dev`, `@agent-qa-engineer`, `@agent-reviewer`, `@agent-designer` (наброски фронтенда).

Владение файлами, чтобы параллельная работа не конфликтовала:
- `backend-dev` — `app/`, `migrations/`, `pyproject.toml`, `uv.lock`, `docker-compose.yml`, `.env.example`, `alembic.ini`;
- `qa-engineer` — `tests/`, `scripts/record_fixtures.py`;
- тимлид (основная сессия) — `docs/`, `README.md`, `CLAUDE.md`;
- `designer` — `design/` (HTML-макеты будущего фронтенда на данных v2);
- `reviewer` — ничего не меняет.

## Git
Не коммитить и не пушить без моего «ок». В конце этапа предложи сообщение коммита.
