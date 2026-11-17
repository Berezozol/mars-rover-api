# Архитектура

## Что это

Монолит на Python — открытая неофициальная замена архивированного Mars Rover Photos API. Забирает фото и метаданные марсоходов из фидов JPL (Curiosity, Perseverance — постоянно; Spirit, Opportunity — разовым импортом из архива), хранит их в Postgres и отдаёт через две версии API:
- **v1** — совместимая со старым API (`LEGACY-API.md`), чтобы старые клиенты переезжали сменой хоста;
- **v2** — собственный, более полный API (`API.md`); на нём будет фронтенд.

Почему так, а не прокси «запрос пришёл — сходили в NASA»: официальный API архивирован, а фиды JPL неофициальные, медленные и по-разному устроены (см. `DATA-SOURCES.md`). Своя база даёт стабильный контракт, быстрые фильтры и независимость от падений источника.

## Поток данных

```
 фиды JPL (M20, MSL)
        │  HTTP: таймауты, ретраи, ≤2 rps
        ▼
 app/sources/     адаптеры → нормализованный PhotoRecord
        ▼
 app/services/sync.py     инкрементальная синхронизация, upsert, сводки по солам
        ▼
 PostgreSQL       rovers · cameras · photos · sol_summaries · sync_runs
        ▲
 app/repositories/   запросы к БД
        ▲
 app/services/       логика чтения (фото, манифест)
        ▲
 app/api/legacy/   (v1)        app/api/v2/
 старый формат                  собственный формат
        ▲                              ▲
 старые клиенты           новые клиенты / фронтенд
```

## Структура кода

```
app/
  main.py              create_app(), lifespan, подключение роутеров, обработчики ошибок
  cli.py               команды: sync, backfill
  core/
    config.py          Settings (pydantic-settings), все настройки только отсюда
    logging.py         структурные логи
    errors.py          доменные исключения; обработчики отдают их в формате v1 или v2
    legacy_dates.py    earth_date по солу по формуле старого API и обратный перевод дата → сол
    cameras.py         справочник полных имён камер (код → имя) и группы камер Curiosity для v1
    legacy_params.py   разбор параметров v1: дата YYYY-M-D, целые, окно пагинации
  db/
    session.py         async engine, фабрика сессий, зависимость get_session
    models.py          ORM-модели SQLAlchemy 2.0
  repositories/        rovers.py, photos.py, sync_runs.py — только SQL, без логики
  sources/
    base.py            протокол SourceAdapter + dataclass PhotoRecord
    normalize.py       общие правила нормализации: "UNK"/null → None, числа-строки, время в UTC
    http.py            общий httpx-клиент: таймауты, ретраи, лимит частоты, User-Agent
    m2020.py           адаптер Perseverance
    msl.py             адаптер Curiosity
  services/
    sync.py            синхронизация марсохода
    scheduler.py       APScheduler: периодический инкремент по всем марсоходам
    legacy.py          логика чтения v1: фото, latest, манифест, группы камер, только полнокадровые
    (v2 — этап 5)      выборки и манифест для v2
  api/
    legacy/            v1: rovers.py, photos.py, manifests.py, schemas.py — по LEGACY-API.md;
                       один роутер подключён дважды: /api/v1 и /mars-photos/api/v1
    v2/                rovers.py, photos.py, admin.py, schemas.py — по API.md
    deps.py            общие зависимости: сессия, admin-токен, пагинация
migrations/            Alembic
tests/
  unit/                адаптеры, нормализация, утилиты — без БД и сети
  integration/         синхронизация и репозитории — с тестовой БД, сеть замокана (respx)
  api/                 HTTP-тесты ручек: legacy/ по LEGACY-API.md, v2/ по API.md
  fixtures/upstream/   записанные ответы источников (обрезанные до 3–5 элементов)
scripts/               record_fixtures.py и прочие утилиты
docs/
```

**Правило слоёв:** роутер → сервис → репозиторий → БД. Роутеры не ходят в БД напрямую, репозитории не содержат бизнес-логики, никто кроме `sources/` не знает формат ответов NASA.

## Модель данных

- `rovers` — `id` (slug: `curiosity`, `perseverance`, `opportunity`, `spirit`), `legacy_id` (smallint, уникальный; числовой `id` для v1, где `rover.id` и `camera.rover_id` — числа: Curiosity 5, Opportunity 6, Spirit 7, Perseverance 8), `name`, `mission` (`msl`, `mars2020`, `mer`), `launch_date`, `landing_date`, `status`, кэш-поля `max_sol`, `max_date`, `total_photos`, `last_synced_at` и те же счётчики только по полнокадровым снимкам для v1: `full_max_sol`, `full_max_date`, `full_total_photos`.
- `cameras` — `id`, `rover_id`, `code` (как в источнике, верхний регистр), `name` (полное имя из справочника в коде, иначе = code), `sort_order` (порядок оригинала — по нему сортирует v1). Уникальность `(rover_id, code)`. Неизвестная камера создаётся автоматически в конце порядка.
- `photos` — `id` (bigserial), `rover_id`, `camera_id`, `source_id`, `sol`, `earth_date`, `taken_at`, `received_at`, `sample_type`, `img_full`, `img_small`, `img_medium`, `img_large`, `title`, `caption`, `credit`, `source_link`, `site`, `drive`, `extra` (jsonb), `created_at`, `updated_at`.
  Уникальность `(rover_id, source_id)`. Индексы под запросы API: `(rover_id, sol DESC, taken_at DESC)`, `(rover_id, earth_date)`, `(rover_id, camera_id, sol)`, частичный `(rover_id, sol, camera_id, id) WHERE sample_type = 'full'` — под v1.
- `sol_summaries` — `(rover_id, sol)` PK, `earth_date`, `total_photos`, `cameras` (массив кодов), `full_photos`, `full_cameras` (только полнокадровые — для манифеста v1). `earth_date` — самая ранняя реальная дата съёмки за сол; массивы камер — уникальные коды по алфавиту. Пересчитывается синхронизацией для затронутых солов — манифест читается без тяжёлых агрегатов.

**Две разные `earth_date`.** В таблице `photos.earth_date` — реальная дата съёмки по UTC (её отдаёт v2). Легаси-дата v1 не хранится: она однозначно вычисляется из сола и даты посадки (`app/core/legacy_dates.py`), а фильтр v1 по дате переводится в фильтр по солу.
- `sync_runs` — журнал: `id`, `rover_id`, `mode` (`incremental` / `backfill`), `from_sol`, `to_sol`, `status` (`running` / `success` / `partial` / `failed`), `started_at`, `finished_at`, `photos_upserted`, `failed_sols` (массив солов), `error`. Строки `running`, оставшиеся от убитого процесса, следующий запуск того же марсохода (под его блокировкой) помечает `failed` с ошибкой `abandoned`.

## Синхронизация

- **Протокол адаптера** (`app/sources/base.py`): `latest_sol()` и `fetch_sols(from_sol, to_sol)` — асинхронный итератор пар `(sol, list[PhotoRecord])` по убыванию сола, только для солов, где есть снимки. M20 забирает диапазон одним фильтром `condition_*:sol:gte/lte` постранично, MSL — по солу со всеми страницами (`DATA-SOURCES.md`). Синхронизация работает с диапазонами, а не с отдельными солами.
  - Если источник прислал солы не по порядку, один сол может прийти повторно с дополнительными снимками — потребитель сливает (upsert), а не заменяет.
  - Упор в `UPSTREAM_MAX_PAGES` — исключение `PageLimitExceeded`: недокачанный сол не отдаётся, уже отданные остаются верными. Для синхронизации это сбой сола → статус `partial`. Лимит у M20 общий на диапазон (200 страниц × 100 ≈ 20 000 снимков), поэтому backfill режется на куски.
  - Диапазон проходится кусками по `SYNC_CHUNK_SOLS` солов (по умолчанию 10), каждый сол коммитится сразу. Сбой куска — его ещё не отданные солы считаются сбойными, синхронизация идёт к следующему куску. Итог: без сбоев — `success`, со сбоями и с загруженными данными — `partial`, ничего не загружено — `failed`.
  - Первый инкремент на пустой базе начинается с `последний сол − SYNC_OVERLAP_SOLS`; полная история — только backfill.
  - Пустая страница M20 раньше, чем набрано `total_results`, — тоже сбой (решение владельца, 2026-11-17): недокачанный сол не отдаётся, синхронизация ставит `partial`.
  - Лимит частоты действует на экземпляр `UpstreamClient`: в процессе (планировщик, CLI) — один общий клиент.

- **Инкремент:** узнать последний сол у источника и пройти солы от `min(rovers.max_sol, последний сол, сбойные солы прошлого инкремента) − SYNC_OVERLAP_SOLS` до последнего. Сбойные солы запуска хранятся в `sync_runs.failed_sols`; если последний завершённый инкремент с известным диапазоном был не `success`, следующий начинается с его самого раннего сбойного сола — дыры закрываются сами. Инкремент с известным диапазоном, который аварийно оборвался (отменён, упал или брошен как `abandoned`), записывает в `failed_sols` весь свой диапазон: прошлые дыры в него входили, поэтому они не теряются. Сбои backfill инкремент не подбирает, их перезапускают backfill-ом. Перекрытие нужно, потому что снимки за сол доходят до Земли несколько дней.
- **Backfill:** CLI-команда с диапазоном солов и паузами между запросами. Полная история — это миллионы записей, её грузим постепенно.
- **Идемпотентность:** `INSERT ... ON CONFLICT (rover_id, source_id) DO UPDATE`. Повторный запуск не плодит дубли.
- **Одна синхронизация на марсоход за раз:** `pg_try_advisory_lock` по марсоходу. Занято → `409 sync_in_progress` (или пропуск в планировщике).
- **Частичные сбои:** упал один сол — записать в журнал, идти дальше, итоговый статус `partial`.
- **Расписание:** APScheduler внутри приложения, интервал `SYNC_INTERVAL_MINUTES`, выключается `SYNC_ENABLED=false` (в тестах — всегда выключен).

## Отдача изображений

Бинарники не храним. `GET /api/v2/photos/{id}/image` отвечает `302` на URL NASA нужного размера. Потоковое проксирование с кэшем — отдельный этап, если понадобится.

## Решения

| Что | Выбор | Почему |
|---|---|---|
| Веб-фреймворк | FastAPI | async, OpenAPI-схема для будущего фронта, pydantic |
| ORM и миграции | SQLAlchemy 2.0 (async) + asyncpg, Alembic | стандарт, типизация, автогенерация миграций |
| HTTP к источникам | httpx + tenacity | async, таймауты, ретраи с backoff |
| Планировщик | APScheduler в процессе + advisory lock | монолит без отдельного воркера; лок защищает от дублей |
| Тесты | pytest, pytest-asyncio, respx, отдельная тестовая БД | без сети, реальный Postgres вместо моков БД |
| Пакеты и линтер | uv, ruff | быстро, один инструмент на зависимости и запуск |
| БД локально | Postgres 16 в Docker Compose | одинаковое окружение, легко пересоздать |

## Настройки (переменные окружения)

`DATABASE_URL`, `TEST_DATABASE_URL`, `SYNC_ENABLED`, `SYNC_INTERVAL_MINUTES`, `SYNC_OVERLAP_SOLS` (по умолчанию 7), `SYNC_CHUNK_SOLS` (по умолчанию 10), `UPSTREAM_TIMEOUT_SECONDS`, `UPSTREAM_MAX_RPS`, `UPSTREAM_USER_AGENT`, `ADMIN_TOKEN` (не задан → admin-ручки отключены), `CORS_ORIGINS`, `LOG_LEVEL`. Шаблон — `.env.example`, реальный `.env` в git не попадает.
