# Этап 18B: фильтры v2 — черновик архитектора

Редакция 1 (2026-11-21, HEAD `abf1b3a`, миграции до `0011`). Основа: `docs/PLAN.md` (этап 18, 18B), `docs/API.md` (`/rovers/{rover}/photos`, Photo, «Кэширование», «Ограничение частоты»), отчёт `docs/reports/stage-18b-fields.md` (покрытие полей и EXPLAIN на dev-базе, 1 728 301 снимок), код `app/repositories/photos.py`, `app/services/v2.py`, `app/api/v2/{params,rovers,photos}.py`, `app/sources/photo_meta.py`, `app/api/caching.py`, `app/db/models.py`. Сеть не использовалась.

Этап разбит на части для отдельного «ок»: **18B-1** — общий список и диапазоны (только индекс, без новых колонок и без правки данных); **18B-2** — колонки `local_time_s`, `width`, `height` и фильтры по ним; **18B-3** — поиск рядом с точкой (контракт — черновик, утверждать после замера на настоящих стоянках).

## 0. Цель, границы, критерий

**Входит:** 18B-1 — `GET /api/v2/photos` по нескольким марсоходам; `sol_min/sol_max`, `date_min/date_max`, `sort`, `camera` списком, `site_min/site_max`; те же параметры у `/rovers/{rover}/photos`; миграция 0012 (индекс под `sort=taken_at`). 18B-2 — миграция 0013 (колонки и частичные индексы), заполнение при записи и команда `backfill-meta`; `local_time_min/max`, `golden_hour`, `min_width/min_height`.
**Не входит:** v1; `fields`/`include` (ADR-5, вопрос В4); фильтры по углам мачты, вектору камеры, `xyz` (отчёт §6 — строки в `extra`, у MER нет, у Curiosity 49%); перезагрузка истории Perseverance (вопрос В2); keyset-пагинация; фронтенд-страница «все марсоходы» (заметки в §7).
**Готово, когда:** AC 18B-1 (и 18B-2 после своего «ок») зелёные, тело ответов прежних запросов `/rovers/{rover}/photos` не изменилось, сценарии §8 укладываются в p95 ≤ 200 мс на dev-базе, `alembic downgrade` до `0011` работает.

## 1. Факты

| Что | Факт (источник) |
|---|---|
| индексы `photos` | `(rover_id, sol DESC, taken_at DESC, id DESC)`, `(rover_id, sample_type, sol DESC, …)`, `(rover_id, earth_date)`, `(rover_id, camera_id, sol)`, частичный `(rover_id, site, drive) WHERE site IS NOT NULL` (`models.py:88-138`). Индекса с ведущим `taken_at` нет |
| стоимость сейчас | 2-5 марсоходов + диапазон солов + top-N: 4,6-26 мс; дата без `rover_id`: 521 мс; фильтры по выражениям над `extra`: 0,3-9,2 с Seq Scan 2,2 ГБ (отчёт §2) |
| порядок списка | всегда `sol DESC, taken_at DESC, id DESC` (`photos.py:193`); `total` по `sol_summaries`/`camera_sol_counts`, где возможно (`v2.py:397`) |
| `earth_date` | по договору `PhotoRecord` — UTC-дата `taken_at` (`sources/base.py:16`); на всей базе [не проверено] — задача `data-engineer` |
| местное время | Curiosity 49% (LMST), Perseverance 0% (история загружена до правки адаптера), MER 100% (LTST), Ingenuity 100% (LMST) (отчёт §1) |
| размер кадра | Perseverance, MER, Ingenuity — 100%; Curiosity — нет (отчёт §1); разбор — `photo_meta.frame_size` (цифр до 6) |
| неизвестные параметры | FastAPI их игнорирует; `cache_control_for("/api/v2/photos")` → `public, max-age=300` без правок (`caching.py:56`) |
| `rover_waypoints` в dev | 0 строк (отчёт §1) — близость замерена только на синтетике |

## 2. Критерии приёмки

| № | Критерий (вход → результат) | Кто | Слой |
|---|---|---|---|
| | **18B-1** | | |
| AC-1 | `GET /api/v2/photos?rovers=curiosity,perseverance&sol_min=1000&sol_max=1002` → 200, только эти марсоходы и солы 1000-1002, порядок `sol DESC, taken_at DESC, id DESC`, `total` = сумма по марсоходам; ключа `nearest_sols` нет | qa | api |
| AC-2 | без `rovers` → снимки всех пяти марсоходов (в т.ч. `ingenuity`); `rovers=Spirit` = `rovers=spirit`; повтор (`spirit,spirit`) схлопывается | qa | api |
| AC-3 | `rovers=` / `rovers=mars` / `rovers=curiosity,,spirit` / `rovers=curiosity, spirit` (пробел) / строка длиннее 200 символов → `422`, `field: "rovers"`, в `message` допустимые slug | qa | api |
| AC-4 | `sort` = `-sol` (по умолчанию), `sol`, `-taken_at`, `taken_at`: порядок по ключу, при равенстве — `id` в том же направлении (`sol` = `sol, taken_at, id`); иное значение → `422` со списком | qa | api, integration |
| AC-5 | `date_min=2015-05-30&date_max=2015-06-01` → `earth_date` в диапазоне включительно; `date_min > date_max`, `sol_min > sol_max`, `site_min > site_max` → `422`; одно из пары — открытый край | qa | api |
| AC-6 | `sol` вместе с `sol_min`/`sol_max`, `earth_date` с `date_*`, любой из `sol*` с любым из `earth_date`/`date_*` → `422` | qa | api |
| AC-7 | `rovers=curiosity,spirit&camera=NAV_LEFT_B,navcam` → у Curiosity только `NAV_LEFT_B`, у Spirit только `NAVCAM`; код, которого нет ни у одного выбранного марсохода → `422` с кодами по марсоходам; больше 10 кодов → `422` | qa | api |
| AC-8 | `site_min`/`site_max` без ровно одного марсохода (нет `rovers` или их > 1) → `422`; `site` вместе с `site_min`/`site_max` → `422`; снимки без `site` не попадают | qa | api |
| AC-9 | `page × per_page > 10000` на `/photos` → `422`, `field: "page"`; ровно 10000 → `200`. У `/rovers/{rover}/photos` потолка нет | qa | api |
| AC-10 | `/rovers/{rover}/photos` принимает `sol_min/max`, `date_min/max`, `site_min/max`, `sort`, список `camera`; без новых параметров тело байт-в-байт как до этапа (снимок ответа на фикстурах); `nearest_sols` — по-прежнему только для `sol`/`earth_date` | qa | api |
| AC-11 | `/photos`: `Cache-Control: public, max-age=300`, `ETag`/`304`, `HEAD`, `X-RateLimit-*`; ошибки — `no-store`; v1 — прежние тесты зелёные без правок | qa | api |
| AC-12 | `alembic upgrade head` / `downgrade 0011` / `alembic check` чисто; `docs/openapi.json` и `examples/types.ts` пересобраны (тесты актуальности зелёные) | qa, backend-dev | integration |
| AC-13 | сценарии P1-P6 (§8) на dev-базе — p95 ≤ 200 мс, планы без Seq Scan по `photos` | data-engineer | замер |
| | **18B-2** | | |
| AC-14 | upsert заполняет `local_time_s`, `width`, `height` из `extra` на фикстурах каждого марсохода; у Curiosity `width`/`height` = `NULL`; повторная запись того же снимка — без изменения `updated_at` | qa | integration |
| AC-15 | `backfill-meta` на базе с пустыми колонками заполняет их; повторный запуск — 0 изменённых строк; `--rover` ограничивает; после прерывания продолжение с `--from-sol` даёт тот же результат | qa | integration |
| AC-16 | для 100% строк dev-базы колонки совпадают с `photo_meta` по `extra` (SQL-сверка) | data-engineer | замер |
| AC-17 | `local_time_min=17:00&local_time_max=18:00` → `17:00:00 ≤ t < 18:00:00`; `min > max` (`23:00`→`01:00`) — через полночь; `min = max`, `7:00`, `24:00`, `17:60`, `17:00:00` → `422`; снимки без времени не попадают | qa | api |
| AC-18 | `golden_hour=true` = окна `05:30-07:00` и `17:00-18:30`; `false` — без фильтра; вместе с `local_time_*` → `422`; иное значение → `422` | qa | api |
| AC-19 | `min_width=1920` / `min_height=1200` → `width ≥`, `height ≥`; Curiosity не попадает; вне `1..32767` → `422` | qa | api |
| AC-20 | `width`, `height`, `local_time` в ответах не изменились (берутся из `extra`, как раньше) | qa | api |
| AC-21 | сценарии P7-P9 (§8) — p95 ≤ 200 мс, `count` по индексу | data-engineer | замер |

## 3. Решения

| № | Вопрос | Решение | ADR |
|---|---|---|---|
| D1 | путь | `GET /api/v2/photos` (рядом с `/photos/{id}`, конфликта нет); ответ — обычный список Photo | 1 |
| D2 | `rovers` | список slug через запятую; без параметра — явный `rover_id = ANY(все пять)`, чтобы индексы с ведущим `rover_id` работали | 1 |
| D3 | имена диапазонов | семейство `*_min`/`*_max` для фильтров `/photos` (как в `PLAN.md`); `from_sol`/`to_sol` у `manifest`/`location` не трогаем | 2, В5 |
| D4 | сортировка | 4 значения, по умолчанию `-sol` (= сегодняшний порядок); новый индекс `(rover_id, taken_at DESC, id DESC)` — миграция 0012 | 3 |
| D5 | запрос по нескольким марсоходам | `UNION ALL` подзапросов по марсоходам, каждый — по индексу с `LIMIT offset+per_page` и только ключами сортировки, сверху слияние и догрузка строк по `id` | 3 |
| D6 | глубина страниц | на `/photos` `page × per_page ≤ 10000` | 3, В5 |
| D7 | даты | фильтр по `earth_date` + избыточная граница `taken_at` с запасом в сутки (точно, если `earth_date` отличается от UTC-даты `taken_at` не больше чем на сутки; проверяет `data-engineer`) — индекс D4 обслуживает и фильтр, и сортировку | 3 |
| D8 | `camera` | список до 10 кодов; код должен быть хотя бы у одного выбранного марсохода; коды переводятся в `camera_id` до запроса | 4 |
| D9 | `site_min/max` | только при одном марсоходе; `drive_from/to` — по-прежнему только с `site` | 4 |
| D10 | `nearest_sols` | на `/photos` нет | 1 |
| D11 | `fields`/`include` | не делаем | 5, В4 |
| D12 | колонки 18B-2 | физические `local_time_s integer`, `width/height smallint`, заполняются в `upsert_photos` через `photo_meta`, история — командой; не `GENERATED` | 6 |
| D13 | ответы 18B-2 | `width`/`height`/`local_time` в теле по-прежнему из `extra`; колонки — только для фильтров | 6 |
| D14 | близость | отдельный 18B-3 после замера на настоящих `rover_waypoints` | 7, В3 |

## 4. Текст для `API.md`

**18B-1. → «Общие правила v2», границы чисел, дописать:** `sol_min`, `sol_max`, `site_min`, `site_max` — от 0 до 2 147 483 647.

**→ Новый эндпоинт (после `/rovers/{rover}/photos/latest`)**

### `GET /api/v2/photos` (этап 18B)
Снимки нескольких марсоходов одним списком. Параметры (все необязательные):
- `rovers` — slug через запятую без пробелов, без учёта регистра, повторы схлопываются; без параметра — все марсоходы. Пустой элемент, неизвестный slug, строка длиннее 200 символов → `422`;
- `sol` или диапазон `sol_min`/`sol_max` (включительно, любой край можно опустить). Солы у каждого марсохода свои (у `ingenuity` — солы Perseverance);
- `earth_date` или диапазон `date_min`/`date_max` (`YYYY-MM-DD`, включительно) — реальная дата съёмки (UTC);
- солы и даты в одном запросе не сочетаются; `sol` с `sol_min`/`sol_max`, `earth_date` с `date_min`/`date_max` — тоже; `min > max` → `422`;
- `camera` — один или несколько кодов через запятую (до 10), без учёта регистра; у каждого марсохода отбираются его камеры из списка; код, которого нет ни у одного выбранного марсохода, → `422`, в `details` — допустимые коды по марсоходам;
- `sample_type` — как у `/rovers/{rover}/photos`;
- `site` (с `drive_from`/`drive_to`) или диапазон `site_min`/`site_max` — только при ровно одном марсоходе в `rovers`, иначе `422`; `site` с `site_min`/`site_max` → `422`; снимки без `site` не попадают;
- `sort` — `-sol` (по умолчанию: `sol DESC, taken_at DESC, id DESC`), `sol` (`sol, taken_at, id` по возрастанию), `-taken_at` (`taken_at DESC, id DESC`), `taken_at`; иное → `422`. При нескольких марсоходах `-sol` смешивает разные эпохи — для ленты «новое на Марсе» берите `-taken_at`;
- `page`, `per_page` — как везде; `page × per_page` не больше 10 000, иначе `422` (сузьте диапазон).

Ответ — список Photo `{"items": [...], "page": 1, "per_page": 25, "total": 48209}`, без `nearest_sols`. `Cache-Control: public, max-age=300`, лимит частоты действует. `200` · `422 validation_error`.

**→ `/rovers/{rover}/photos`, в список параметров дописать:** «`sol_min`/`sol_max`, `date_min`/`date_max`, `site_min`/`site_max`, `sort`, список в `camera` — как у `/photos` (этап 18B), с теми же правилами сочетания; потолка `page × per_page` нет». Строку «Сортировка всегда: …» заменить на «Сортировка — параметр `sort`, по умолчанию `sol DESC, taken_at DESC, id DESC`». Для старых запросов ничего не меняется.

**18B-2. → `/photos` и `/rovers/{rover}/photos`, дописать параметры:**
- `local_time_min`, `local_time_max` — местное солнечное время съёмки `HH:MM` (00:00-23:59); отбор `min ≤ t < max`; при `min > max` — через полночь (`t ≥ min` или `t < max`); один край — открытый; `min = max` или другой формат → `422`. Шкала — как `local_time.kind` снимка: LMST у Curiosity, Perseverance и Ingenuity, LTST у Spirit и Opportunity (отличается до ~50 мин) — в запросе по нескольким марсоходам одно окно применяется к каждой шкале как есть. Снимки без местного времени не попадают: у Curiosity ~половина, у Perseverance — только снимки, загруженные после доработки v2 (2026-11-20);
- `golden_hour` — `true`/`false` (`1`/`0`), по умолчанию `false`; `true` = окна `05:30-07:00` и `17:00-18:30` местного времени. Упрощение: настоящие восход и закат сдвигаются по сезону на десятки минут. Вместе с `local_time_*` → `422`;
- `min_width`, `min_height` — целые 1-32 767, размер полного кадра не меньше заданного; снимки без размера (Curiosity — все) не попадают.

**18B-3 (черновик, утвердить после замера):** `near=LAT,LON` (градусы, `−90..90`, `−180..180`) и `radius_km` (0,01-50, по умолчанию 1) — снимки, сделанные на стоянках в радиусе (правило привязки — как `location` карточки: стоянка того же `site` с наибольшим `drive` ≤ `drive` снимка); только `curiosity`, `perseverance` — ровно один из них в `rovers`, иначе `422`; снимки без `site`/`drive` (у Curiosity ~50%) не находятся.

## 5. ADR

1. **Отдельная ручка или `rover=all` в старой.** `/rovers/all/photos` ломает правило «`{rover}` — slug» и `404 rover_not_found`; новая ручка `/photos` чище и совпадает с планом. `nearest_sols` на ней не нужен (несколько марсоходов — нет одного «ближайшего сола»). **Рекомендация:** `/photos`.
2. **Имена.** `sol_min/sol_max` против уже существующих `from_sol/to_sol`. Фильтров с диапазоном будет 5 пар — единое `*_min/*_max` читается лучше и уже записано в `PLAN.md`; `from_/to_` остаются у ручек, где они ограничивают выдачу, а не фильтруют. Минус — два стиля в контракте. **Рекомендация:** `*_min/*_max` (В5).
3. **Сортировка по нескольким марсоходам.** Индексы начинаются с `rover_id`, поэтому при `rover_id = ANY(...)` PostgreSQL 16 сортирует всё найденное: без фильтров — 1,7 млн строк, секунды. Варианты: (а) сортировка только с узким диапазоном — сложные правила в контракте; (б) `UNION ALL` по марсоходам с `ORDER BY … LIMIT offset+per_page` в каждом (index-only по ключам) + слияние — до 5 × 10 000 записей индекса в худшем случае; (в) отказаться от `taken_at`. **Рекомендация:** (б) + индекс `(rover_id, taken_at DESC, id DESC)` (~55 МБ [оценка], `CONCURRENTLY`, миграция 0012) + потолок 10 000 (как `max_result_window` поисковиков; глубже — сужайте фильтры). Тот же индекс обслуживает диапазон дат через избыточную границу `taken_at` (D7) — индекс `(earth_date, …)` из отчёта не нужен. `total` — сумма `_total` по марсоходам (шорткаты `sol_summaries` сохраняются; для `sol_min/max` без других фильтров — сумма по `sol_summaries`).
4. **`camera` и `site` при нескольких марсоходах.** Коды камер у марсоходов разные (`NAV_LEFT_B` и `NAVCAM`), поэтому один код почти всегда исключил бы остальных; список кодов с проверкой «есть хоть у одного» сохраняет строгий `422` на опечатки. `site` — номера у каждого марсохода свои, смысла в общем диапазоне нет. **Рекомендация:** D8, D9.
5. **`fields`/`include`.** Плюс — меньше байт (сейчас Photo ~1 КБ, gzip сжимает список в 5-7 раз). Минусы: схема ответа в OpenAPI и `types.ts` становится «все поля необязательны» для всех клиентов; тяжёлых вложенных блоков в списке нет (`location`, `in_sol` — только в карточке); ещё одна ось кэша. **Рекомендация:** не делать в 18B; вернуться с `POST /photos/batch` (18C), если попросят (В4).
6. **Колонки для 18B-2.** Индекс по выражению над `extra` — regexp в индексе и разбор формата NASA в SQL (дубль `photo_meta`); `GENERATED STORED` переписывает 2,2 ГБ под эксклюзивной блокировкой. Физические колонки, заполняемые одной функцией `photo_meta.stored_meta` в `_photo_row`, держат разбор формата в `app/sources/` (репозиторий уже зависит от `sources.base`). Ответы оставляем на разборе `extra` (D13), чтобы окно между миграцией и `backfill-meta` на проде не дало `null` в теле; переход на колонки — позже, отдельной мелкой задачей. **Рекомендация:** колонки.
7. **Близость в 18B-2 или позже.** Схему не меняет (`rover_waypoints` + индекс `site/drive` есть), но в dev стоянок 0, замер только на синтетике, а правило привязки (наибольший `drive` ≤ снимка) дороже точного соединения из отчёта. **Рекомендация:** 18B-3: сначала настоящие стоянки в dev (В3), замер `data-engineer`, потом «ок» контракта. Реализация — `WITH sel AS MATERIALIZED` (стоянки в радиусе, гаверсинус, R = 3389,5 км, без PostGIS) + `LEAD(drive)` по `site` для правой границы.

## 6. Модель данных, миграция, поток

**0012 (18B-1):** `ix_photos_rover_id_taken_at (rover_id, taken_at DESC, id DESC)`, `CREATE/DROP INDEX CONCURRENTLY` в `autocommit_block()` (как 0005). Без блокировок записи; на ноутбуке ~1-2 мин [оценка].

**0013 (18B-2):** `photos.local_time_s integer NULL` (секунды местных суток 0-86 399; шкала — от марсохода), `photos.width smallint NULL`, `photos.height smallint NULL`. `ADD COLUMN` без умолчания — только каталог; в миграции `SET lock_timeout = '5s'`, чтобы не встать в очередь за долгим чтением. Индексы `CONCURRENTLY`: `ix_photos_rover_id_local_time (rover_id, local_time_s) WHERE local_time_s IS NOT NULL` (~17 МБ после заполнения), `ix_photos_rover_id_width (rover_id, width) WHERE width IS NOT NULL` (~7 МБ). Размер > 32 767 или ≤ 0 → обе колонки `NULL` (правило «по отдельности не бывают» сохраняется). `downgrade` — `DROP INDEX CONCURRENTLY`, `DROP COLUMN`.

**Поток 18B-2:** адаптер → `PhotoRecord.extra` (как сейчас) → `upsert_photos` → `_photo_row` вызывает `photo_meta.stored_meta(rover_id, extra) -> StoredMeta(local_time_s, width, height)`; колонки добавляются в `_DATA_COLUMNS` (снимок, перекачанный синхронизацией, дозаполняется сам). Команда `uv run python -m app.cli backfill-meta [--rover R] [--from-sol N] [--batch-sols 50]`: по марсоходу и пачкам солов читает `id` и нужные пути `extra` (не весь JSONB), считает той же функцией, пишет `UPDATE … FROM unnest(...) WHERE … IS DISTINCT FROM …` (идемпотентно), коммит на пачку, прогресс в лог. Сети нет. Затрагивает ~1,33 млн строк (Curiosity 388 тыс. с LMST, Perseverance 580 тыс., MER 342 тыс., Ingenuity 14,5 тыс.); ~10-25 мин на ноутбуке, WAL 2-4 ГБ, таблица вырастет до ~1,5-2 ГБ за счёт старых версий строк — нужно ~3 ГБ свободного диска, после — `VACUUM (ANALYZE) photos` [оценка]. Синхронизацию и API можно не останавливать (обновления по `id`, блокировки строк короткие). Ночной бэкап — до запуска.

## 7. Задания ролям и общие имена

**Общие имена:** ручка `list_all_photos` в `app/api/v2/photos.py`; параметры в `app/api/v2/params.py`: `RoversParam`, `CameraListParam`, `SortParam`, `SolMinParam`/`SolMaxParam`, `DateMinParam`/`DateMaxParam`, `SiteMinParam`/`SiteMaxParam`, (18B-2) `LocalTimeParam`, `GoldenHourParam`, `MinSizeParam`; сервис `v2.search_photos_multi(session, query: MultiPhotoQuery, page)`; `PhotoSort = Literal["-sol", "sol", "-taken_at", "taken_at"]`, `DEFAULT_SORT = "-sol"`, `MAX_RESULT_WINDOW = 10_000`; в `PhotoFilter` — `sol_min`, `sol_max`, `date_min`, `date_max`, `site_min`, `site_max`, (18B-2) `local_time_ranges: tuple[tuple[int, int], ...]`, `min_width`, `min_height`; репозиторий `list_photos_multi(session, filters: Sequence[PhotoFilter], sort, limit, offset)`, `list_photos(..., sort=)` вместо `order="newest"`; `GOLDEN_HOUR_WINDOWS = ((19800, 25200), (61200, 66600))`; `photo_meta.stored_meta`, `StoredMeta`; CLI `backfill-meta`; индексы `ix_photos_rover_id_taken_at`, `ix_photos_rover_id_local_time`, `ix_photos_rover_id_width`.

- `backend-dev`: 18B-1 — миграция 0012, параметры, сервис и репозиторий (D5, D7), `_total` для диапазонов, общая проверка сочетаний для обеих ручек, OpenAPI и `types.ts`. 18B-2 — миграция 0013, `stored_meta`, `_photo_row`/`_DATA_COLUMNS`, `backfill-meta`, фильтры. Сообщения `422` — по-английски, с полем в `details`.
- `qa-engineer`: AC-1…AC-12 и AC-14…AC-20; тесты называть `test_ac<N>_…`, сдать таблицу «AC → тест». Граничные: `rovers` с пробелом/юникодом, `camera` с пустым элементом, равные `taken_at` у соседних снимков (проверка `id`-тайбрейка на каждой из 4 сортировок и через границу страниц при нескольких марсоходах), `page × per_page` = 10 000/10 001, `local_time_max=00:00` с `min` (весь вечер), снимок ровно в `18:00:00` (не входит в `17:00-18:00`), `width` = 32 767 и 32 768 в `extra`.
- `data-engineer`: (1) до кода — `max |earth_date − (taken_at AT TIME ZONE 'UTC')::date|` по марсоходам (D7); (2) после кода — AC-13, AC-16, AC-21 на dev-базе (EXPLAIN ANALYZE, холодный и тёплый кэш), время `backfill-meta` на dev; (3) 18B-3 — замер на настоящих стоянках после В3.
- `frontend-dev` (после «ок» API, отдельной задачей): в браузере снимков (`pages/Photos.tsx`) — диапазон солов/дат вместо одного значения, переключатель сортировки «новые/старые»; 18B-2 — окно местного времени, «Golden hour», «Min resolution» (скрыт у Curiosity, подпись про Perseverance без времени у старых снимков). Страница «All rovers» на `/photos` — по желанию владельца; `designer` нужен только для неё.
- `devops`: на проде — бэкап, `alembic upgrade head`, затем `backfill-meta` (18B-2) и `VACUUM (ANALYZE) photos`; проверить место на диске; в `deploy/` — строка в runbook.

## 8. Проверка и бюджет

- Команды: `uv run ruff check .`, `uv run pytest -q -n 12`, `uv run alembic upgrade head && uv run alembic downgrade 0011 && uv run alembic upgrade head`, `uv run python -m app.cli openapi --output docs/openapi.json`.
- Замеры (dev-база, p95 ≤ 200 мс): P1 `/photos` без параметров, каждый `sort`; P2 то же `page=400`; P3 `date_min/max` на год, все марсоходы, `-taken_at`; P4 `sol_min=0`, `-sol`, `camera` из 3 кодов; P5 `rovers=curiosity&site_min=40&site_max=60`; P6 `rovers=curiosity,perseverance&sort=taken_at&page=200`; P7 `golden_hour=true` все; P8 `rovers=perseverance&min_width=1920`; P9 `local_time_min=23:00&local_time_max=01:00`.
- Ревью: `reviewer`; `security-reviewer` — новая публичная ручка и разбор списков в параметрах.
- Расход: architect — этот черновик ~1%; data-engineer — ~1% (две проверки + замеры); остальные — обычный этап среднего размера.

## 9. Внешние запросы (для «ок» владельца)

18B-1, 18B-2 — нет. 18B-3 — либо 0 (стоянки из ночного бэкапа прода в dev), либо 1-2 запроса к MMGIS (JPL) от `data-engineer` (`LOCATION_*` из настроек). Перезагрузка истории Perseverance (~5,8 тыс. запросов к JPL) — только по В2.

## 10. Вопросы владельцу

- **В1. Разбивка и порядок:** 18B-1 → 18B-2 → 18B-3, «ок» на каждую часть отдельно? *Рекомендация:* да; 18B-1 можно начинать сразу после «ок» на §4 (18B-1).
- **В2. Местное время старых снимков Perseverance:** (а) перезагрузить историю (~5,8 тыс. запросов к JPL, ~1,6 ч при 1 запр/с; колонки заполнятся сами через upsert); (б) вычислять LMST из `taken_at` и долготы Jezero (алгоритм Mars24 — не данные источника, расхождения с JPL возможны в секундах-минутах); (в) оставить `null`. *Рекомендация:* (в) в 18B-2 с пометкой в контракте, (а) — как и решено, на «шлифовке»; (б) не делать.
- **В3. Стоянки для замера близости:** (а) восстановить `rover_waypoints` в dev из ночного бэкапа прода — 0 внешних запросов [заполненность на проде не проверена]; (б) команда `locations` в dev — 1-2 запроса к MMGIS. *Рекомендация:* (а), при пустой таблице на проде — (б).
- **В4. `fields`/`include` и углы мачты** не делаем (ADR-5; мачта — отчёт §6)? *Рекомендация:* да, вычеркнуть из 18B; углы мачты при желании — полем в ответе позже.
- **В5. Контракт 18B-1:** имена `*_min/*_max` (рядом с `from_sol/to_sol` у манифеста), потолок `page × per_page ≤ 10000` только на новой `/photos`, `-sol` по умолчанию у обеих ручек. *Рекомендация:* принять как в §4.
- **В6. Смешение шкал LMST/LTST** в одном окне `local_time_*` при нескольких марсоходах — применять как есть с пометкой в контракте? *Рекомендация:* да (альтернатива — `422` при смешении — неудобна для «золотого часа» по всем).
