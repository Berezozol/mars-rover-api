# Архитектура

## Что это

Монолит на Python — открытая неофициальная замена архивированного Mars Rover Photos API. Забирает фото и метаданные марсоходов из фидов JPL (Curiosity, Perseverance — постоянно; Spirit, Opportunity — разовым импортом из архива), хранит их в Postgres и отдаёт через две версии API:
- **v1** — совместимая со старым API (`LEGACY-API.md`), чтобы старые клиенты переезжали сменой хоста;
- **v2** — собственный, более полный API (`API.md`); на нём будет фронтенд.

Почему так, а не прокси «запрос пришёл — сходили в NASA»: официальный API архивирован, а фиды JPL неофициальные, медленные и по-разному устроены (см. `DATA-SOURCES.md`). Своя база даёт стабильный контракт, быстрые фильтры и независимость от падений источника.

## Поток данных

```
 фиды JPL (M20, MSL), слои MMGIS (положение, этап 11)
        │  HTTP: таймауты, ретраи, ≤2 rps
        ▼
 app/sources/     адаптеры → нормализованный PhotoRecord
        ▼
 app/services/sync.py     инкрементальная синхронизация, upsert, сводки по солам
        ▼
 PostgreSQL       rovers · cameras · photos · sol_summaries · camera_sol_counts
                  sol_source_counts · sync_runs
                  rover_waypoints · rover_traverse_segments · location_datasets (этап 11)
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
  cli.py               команды: sync, backfill, reconcile, import-mer, mer-download, locations (этап 11)
  core/
    config.py          Settings (pydantic-settings), все настройки только отсюда
    logging.py         структурные логи
    errors.py          доменные исключения; обработчики отдают их в формате v1 или v2
    legacy_dates.py    earth_date по солу по формуле старого API и обратный перевод дата → сол
    cameras.py         справочник полных имён камер (код → имя) и группы камер Curiosity для v1
    params.py          строгий разбор целых (линейное регулярное выражение, насыщение огромных чисел) — общий для v1 и v2
    legacy_params.py   разбор параметров v1: дата YYYY-M-D, целые, окно пагинации
  db/
    session.py         async engine, фабрика сессий, зависимость get_session
    models.py          ORM-модели SQLAlchemy 2.0
  repositories/        rovers.py, photos.py, sync_runs.py, source_counts.py, locations.py (этап 11) — только SQL, без логики
  sources/
    base.py            протокол SourceAdapter + dataclass PhotoRecord
    normalize.py       общие правила нормализации: "UNK"/null → None, числа-строки, время в UTC
    http.py            общий httpx-клиент: таймауты, ретраи, лимит частоты, User-Agent
    m2020.py           адаптер Perseverance
    msl.py             адаптер Curiosity
    registry.py        какие марсоходы синхронизируются и каким адаптером
    locations.py       адаптер положения (MMGIS): условный запрос, разбор waypoints/traverse (этап 11)
  services/
    sync.py            синхронизация марсохода
    backfill.py        загрузка истории пачками с возобновлением (через SyncService)
    reconcile.py       отчёт сверки
    admin.py           admin-ручки: запуск синхронизации, журнал запусков
    scheduler.py       APScheduler: периодический инкремент по всем марсоходам (+ положение, этап 11)
    location_sync.py   загрузка положения: раз в сутки, проверка файла, замена в транзакции (этап 11)
    locations.py       чтение положения: стоянки, маршрут, стоянка снимка (этап 11)
    legacy.py          логика чтения v1: фото, latest, манифест, группы камер, только полнокадровые
    v2.py              логика чтения v2: марсоходы, камеры со счётчиками, фото с фильтрами, latest, манифест
  api/
    legacy/            v1: rovers.py, photos.py, manifests.py, schemas.py — по LEGACY-API.md;
                       один роутер подключён дважды: /api/v1 и /mars-photos/api/v1
    v2/                rovers.py, photos.py (в т.ч. редирект /image), admin.py, schemas.py, params.py — по API.md
    caching.py         ASGI-middleware: ETag, 304, Cache-Control, HEAD для v1 и v2
                       сессия — зависимость get_session из db/session.py
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
- `camera_sol_counts` — `(rover_id, sol, camera_id)` PK, `total_photos`, `full_photos`. Пересчитывается вместе с `sol_summaries`; из неё `/cameras` берёт счётчики без агрегата по всем фото. Нулевых строк нет.
- `sol_source_counts` — `(rover_id, sol)` PK, `received` (сколько записей прислал источник при последней загрузке сола), `dropped` (из них не сохранено: миниатюры, ошибки нормализации, дубли), `source_total` (итог сола, объявленный источником, если известен), `fetched_at`. Нужна для сверки.
- **Положение (этап 11, миграция 0006).**
  - `rover_waypoints` — стоянки: PK `(rover_id, site, drive)`, `sol`, `lat`, `lon` (градусы, долгота −180…180), `elev_geoid`, `easting`, `northing`, `roll`, `pitch`, `yaw`, `tilt`, `dist_m`, `dist_total_m`; индекс `(rover_id, sol, site, drive)`. Только поля белого списка, без `extra`.
  - `rover_traverse_segments` — маршрут: PK `(rover_id, seq)` (порядок движения), `sol` (`NULL` у Curiosity — источник его не даёт), `length_m`, `coordinates` jsonb `[[lon, lat], ...]` (без высоты, 7 знаков). `MultiLineString` источника разложен на `LineString`.
  - `location_datasets` — состояние файла: PK `(rover_id, kind)` (`waypoints`/`traverse`), `created_at` (первая попытка), `checked_at`, `succeeded_at` (успешная проверка: `304` или принятый `200`), `changed_at`, `etag`, `last_modified`, `content_sha256`, `items`, `dropped`, `max_sol`, `last_error`.
  - Снимок → стоянка — запросом во время чтения, без денормализации: стоянка того же `site` с наибольшим `drive ≤ drive` снимка (`exact`, если равен). `photos` не меняется.
- `sync_runs` — журнал: `id`, `rover_id`, `mode` (`incremental` / `backfill`), `from_sol`, `to_sol`, `status` (`running` / `success` / `partial` / `failed`), `started_at`, `finished_at`, `photos_upserted`, `failed_sols` (массив солов), `source_total` (итог диапазона по данным источника, если известен), `error`. Строки `running`, оставшиеся от убитого процесса, следующий запуск того же марсохода (под его блокировкой) помечает `failed` с ошибкой `abandoned`.

## Синхронизация

- **Протокол адаптера** (`app/sources/base.py`): `latest_sol()` и `fetch_sols(from_sol, to_sol)` — асинхронный итератор пар `(sol, list[PhotoRecord])` по убыванию сола, только для солов, где есть снимки. M20 забирает диапазон одним фильтром `condition_*:sol:gte/lte` постранично, MSL — по солу со всеми страницами (`DATA-SOURCES.md`). Синхронизация работает с диапазонами, а не с отдельными солами.
  - Если источник прислал солы не по порядку, один сол может прийти повторно с дополнительными снимками — потребитель сливает (upsert), а не заменяет.
  - Упор в `UPSTREAM_MAX_PAGES` — исключение `PageLimitExceeded`: недокачанный сол не отдаётся, уже отданные остаются верными. Для синхронизации это сбой сола → статус `partial`. Лимит у M20 общий на диапазон (200 страниц × 100 ≈ 20 000 снимков), поэтому backfill режется на куски.
  - Диапазон проходится кусками по `SYNC_CHUNK_SOLS` солов (по умолчанию 10), каждый сол коммитится сразу. Сбой куска — его ещё не отданные солы считаются сбойными, синхронизация идёт к следующему куску. Итог: без сбоев — `success`, со сбоями и с загруженными данными — `partial`, ничего не загружено — `failed`.
  - Первый инкремент на пустой базе начинается с `последний сол − SYNC_OVERLAP_SOLS`; полная история — только backfill.
  - Пустая страница M20 раньше, чем набрано `total_results`, — тоже сбой (решение владельца, 2026-11-17): недокачанный сол не отдаётся, синхронизация ставит `partial`.
  - Лимит частоты действует на экземпляр `UpstreamClient`: в процессе (планировщик, CLI) — один общий клиент.

- **Инкремент:** узнать последний сол у источника и пройти солы от `min(rovers.max_sol, последний сол, сбойные солы прошлого инкремента) − SYNC_OVERLAP_SOLS` до последнего. Сбойные солы запуска хранятся в `sync_runs.failed_sols`; если последний завершённый инкремент с известным диапазоном был не `success`, следующий начинается с его самого раннего сбойного сола — дыры закрываются сами. Инкремент с известным диапазоном, который аварийно оборвался (отменён, упал или брошен как `abandoned`), записывает в `failed_sols` весь свой диапазон: прошлые дыры в него входили, поэтому они не теряются. Сбои backfill инкремент не подбирает, их перезапускают backfill-ом. Перекрытие нужно, потому что снимки за сол доходят до Земли несколько дней.
- **Backfill:** `python -m app.cli backfill --rover X [--from-sol] [--to-sol] [--batch-sols] [--no-resume]` (`app/services/backfill.py`).
  - Пачки на фиксированной сетке `[k·N, k·N+N−1]` (N = `BACKFILL_BATCH_SOLS`, по умолчанию 100) сверху вниз, каждая — отдельный запуск `backfill` в `sync_runs`.
  - Возобновление по покрытию: сол загружен, если последний завершённый backfill, покрывающий его, — `success` или `partial` без этого сола в `failed_sols`. Пачка, где все солы загружены, пропускается.
  - Занята блокировка марсохода — ждать по `BACKFILL_LOCK_RETRY_SECONDS` до `BACKFILL_LOCK_MAX_WAIT_SECONDS`, потом выход с кодом 2. `BACKFILL_MAX_FAILED_BATCHES` сбойных пачек подряд (источник лежит) — остановка, код 1.
  - В конце сбойные солы этого вызова перезапрашиваются один раз. Коды выхода: 0 — всё загружено, 1 — остались сбойные солы, 2 — блокировка или неверный ввод, 130 — Ctrl+C (повторный запуск продолжит).
- **Сверка:** `python -m app.cli reconcile --rover X [--offline] [--legacy-manifest PATH] [--output PATH]` (`app/services/reconcile.py`). Сравнивает фото, сводки и счётчики камер между собой, `received − dropped` с сохранённым по каждому солу, итоги источника по солам и по запускам, классифицирует солы без данных (сбой / у источника пусто / не запрашивался) и при наличии манифеста старого API сверяет полнокадровые счётчики (у нас ≥ старого — ожидаемо). Код 0 — необъяснённых расхождений нет.
- **Идемпотентность:** `INSERT ... ON CONFLICT (rover_id, source_id) DO UPDATE`. Повторный запуск не плодит дубли.
- **Одна синхронизация на марсоход за раз:** `pg_try_advisory_lock` по марсоходу. Занято → `409 sync_in_progress` (или пропуск в планировщике).
- **Частичные сбои:** упал один сол — записать в журнал, идти дальше, итоговый статус `partial`.
- **Расписание:** APScheduler внутри приложения, интервал `SYNC_INTERVAL_MINUTES`, выключается `SYNC_ENABLED=false` (в тестах — всегда выключен).

## Положение марсоходов (этап 11)

- Источник — 4 GeoJSON-файла MMGIS (`DATA-SOURCES.md`): waypoints и traverse для Perseverance (`M20`) и Curiosity (`MSL`). У Spirit и Opportunity источника нет — API отвечает `available: false`.
- `SyncScheduler.run_once()` после снимков вызывает `LocationSyncService.refresh_due()`: файл, успешно проверенный меньше `LOCATION_REFRESH_HOURS` назад, не запрашивается (срок в БД — переживает перезапуски). Сбой не мешает синхронизации снимков; повтор — в следующем цикле.
- Запрос условный (`If-None-Match`/`If-Modified-Since` из сохранённых `ETag`/`Last-Modified`) через общий клиент (`UpstreamClient.get_conditional`, предел `LOCATION_MAX_BYTES`, только хост `LOCATION_BASE_URL`): `304` или `200` с тем же `sha256` — «не изменилось».
- Новый файл принимается, если разобрался, строк не меньше `LOCATION_MIN_KEEP_RATIO` от прежних и `max_sol` не уменьшился; замена — одной транзакцией под `pg_try_advisory_xact_lock` на `(rover, kind)`. Иначе старые данные остаются, причина — в `location_datasets.last_error`.
- Алерт в Telegram — если файл не удаётся успешно проверить дольше `LOCATION_STALE_DAYS` (отсчёт от `succeeded_at`, а если успеха не было — от `created_at`; нет строки состояния — нет алерта); неизменное содержимое — не сбой (источник обновляется нерегулярно). `/health/sync` не меняется.
- CLI: `python -m app.cli locations [--rover X] [--force]`.

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

`DATABASE_URL`, `TEST_DATABASE_URL`, `SYNC_ENABLED`, `SYNC_INTERVAL_MINUTES`, `SYNC_OVERLAP_SOLS` (по умолчанию 7), `SYNC_CHUNK_SOLS` (по умолчанию 10), `BACKFILL_BATCH_SOLS` (100), `BACKFILL_LOCK_RETRY_SECONDS` (30), `BACKFILL_LOCK_MAX_WAIT_SECONDS` (600), `BACKFILL_MAX_FAILED_BATCHES` (3), `UPSTREAM_TIMEOUT_SECONDS`, `UPSTREAM_MAX_RPS`, `UPSTREAM_USER_AGENT`, `LOCATION_SYNC_ENABLED` (true), `LOCATION_BASE_URL`, `LOCATION_REFRESH_HOURS` (24), `LOCATION_MAX_BYTES` (16 МиБ), `LOCATION_MIN_KEEP_RATIO` (0,9), `LOCATION_STALE_DAYS` (3) — этап 11, `ADMIN_TOKEN` (не задан или пуст → admin-ручки отключены; иначе не короче 16 символов, иначе приложение не стартует), `CORS_ORIGINS`, `LOG_LEVEL`. Шаблон — `.env.example`, реальный `.env` в git не попадает.
