# Этап 11. Положение марсоходов — план архитектора

Редакция 2 (2026-11-19, HEAD `27c8d66`). Владелец ответил «да» на В1–В6 первой редакции. `data-engineer` записал 4 файла MMGIS (`data/locations/`, отчёт `docs/reports/stage-11-mmgis.md`, раздел «Положение (MMGIS)» в `DATA-SOURCES.md`). Решения тимлида учтены. Раздел 8 — текст для `API.md`, раздел 9 — общие имена для `backend-dev` и `qa-engineer`. Контракт в `API.md` переносит тимлид после «ок» владельца на текст раздела 8.

## 0. Цель, границы, критерий

**Входит:**
- таблицы стоянок (waypoints) и сегментов маршрута (traverse) Perseverance и Curiosity;
- загрузка 4 файлов MMGIS целиком, условным запросом, не чаще раза в сутки — вместе с плановым инкрементом;
- `GET /api/v2/rovers/{rover}/location` и `/traverse`;
- поле `location` в карточке снимка (`PhotoDetail`);
- команда CLI `locations`;
- алерт, если источник не удаётся проверить 3 дня.

**Не входит:**
- погода;
- положение Spirit и Opportunity: источника нет, `available: false`;
- координаты в списках Photo (решение В3);
- пространственные запросы, PostGIS;
- любые изменения v1;
- карта во фронтенде.

**Готово, когда:**
- по Perseverance и Curiosity `/location` отдаёт стоянки до последнего сола источника, а `/traverse` — весь маршрут;
- у снимка с `site`/`drive`, для которого есть стоянка того же `site` с `drive ≤`, в карточке есть координаты;
- v1 отвечает байт-в-байт как раньше.

## 1. Факты источника, на которые опирается план

Подробно — в `DATA-SOURCES.md`.

**Файлы.** URL: `{base}{M20|MSL}/Layers/json/{M20|MSL}_{waypoints|traverse}.json`.

| файл | размер | объекты |
|---|---|---|
| M20 waypoints | 0,6 МБ | 708 `Point` |
| M20 traverse | 1,7 МБ | 559 `LineString` |
| MSL waypoints | 0,67 МБ | 1 389 `Point` |
| MSL traverse | 1,36 МБ | 1 316 `LineString` + 1 `MultiLineString` из 5 частей (первый объект) |

Ответ без сжатия, в нём есть `ETag` и `Last-Modified`. Ответ `304` на условный запрос не проверялся.

**Координаты:**
- везде трёхмерные `[lon, lat, elev]`; `z` отбрасываем — в M20 traverse она местами мусорная;
- высота стоянки — из свойства `elev_geoid`;
- долгота восточная, 0…180; значений больше 180 нет, но защиту оставляем.

**Свойства стоянки:**
- типы чистые: числа — числами, `null`, `"UNK"` и строк-чисел нет;
- пара `(site, drive)` уникальна, файл упорядочен по ней, `sol` не убывает, до 4 точек на сол;
- у M20 есть тяжёлые и мусорные поля: `images` (список панорам у всех 708 точек), `Note`, `final`, `elev_radii`/`radius` (у 96 точек заглушка `-9999.0`). Поэтому берём **явный белый список** полей, `extra` не храним;
- `dist_total_m` по порядку не монотонна.

**Traverse:**
- M20: `sol` есть у каждого сегмента (один сегмент на сол с проездом), плюс `length` в метрах и `fromRMC`/`toRMC`;
- MSL: есть только `Length` в метрах; **ни `sol`, ни `site`/`drive` нет**.

**Свежесть:**
- M20 — сол 2022, файл не менялся с 2026-10-28; у нас снимки до сола 2031;
- MSL — сол 5073, у нас до 5077.

Содержимое может законно стоять на месте неделями — алерт поэтому только на сбой проверки.

**Связь со снимками** (dev-база):

| марсоход | точное совпадение | правило «последняя стоянка того же `site` с `drive ≤`» | без координат |
|---|---|---|---|
| Curiosity | 71,2% | 99,4% | 2 444 снимка |
| Perseverance | 99,2% | 99,8% | 1 120 снимков |

Без координат остаются только `site`, у которых нет стоянки с `drive ≤`. Погрешность правила — длина одного проезда: до 488 м у M20, до 142 м у MSL.

## 2. Модель данных, миграция 0006

Файл `migrations/versions/20261119_0006_rover_locations.py`, `revision = "0006"`, `down_revision = "0005"`.
- Создаёт три таблицы.
- `photos` не меняется, сидов нет.
- `downgrade` — `DROP TABLE` в обратном порядке: `location_datasets`, `rover_traverse_segments`, `rover_waypoints`.

**`rover_waypoints`** — стоянки.

| столбец | тип |
|---|---|
| `rover_id` | `text` FK `rovers.id`, PK (1) |
| `site`, `drive` | `integer`, PK (2), (3) |
| `sol` | `integer NOT NULL` |
| `lat`, `lon` | `double precision NOT NULL`, градусы, `lon` в −180…180 |
| `elev_geoid`, `easting`, `northing`, `roll`, `pitch`, `yaw`, `tilt`, `dist_m`, `dist_total_m` | `double precision NULL` |

Индексы:
- PK `(rover_id, site, drive)` обслуживает поиск стоянки снимка (`site = ? AND drive <= ? ORDER BY drive DESC LIMIT 1`);
- `ix_rover_waypoints_rover_id_sol (rover_id, sol, site, drive)` — для `/location` с фильтром и сортировкой.

Объём — 708 и 1 389 строк.

**`rover_traverse_segments`** — линия маршрута.

| столбец | тип |
|---|---|
| `rover_id` | `text` FK, PK (1) |
| `seq` | `integer`, PK (2), порядок движения с 0 |
| `sol` | `integer NULL`; у Curiosity всегда `NULL` |
| `length_m` | `double precision NULL`; длина из источника (`length`/`Length`) |
| `coordinates` | `jsonb NOT NULL`, `[[lon, lat], ...]`, ≥ 2 точек, 7 знаков |

- Отдельных индексов нет: выдаётся всегда целиком по PK.
- `MultiLineString` при импорте раскладывается на отдельные строки-`LineString` с тем же `sol`; длина источника пишется в первую часть, у остальных частей — `NULL`. Так в API всегда только `LineString`.
- Объём — 559 и 1 321 строка, ~64 тыс. точек.

**`location_datasets`** — состояние каждого файла.

| столбец | тип | |
|---|---|---|
| `rover_id` | `text` FK | PK (1) |
| `kind` | `text`, CHECK `IN ('waypoints','traverse')` | PK (2) |
| `checked_at` | `timestamptz NULL` | последняя попытка |
| `succeeded_at` | `timestamptz NULL` | последняя **успешная проверка**: `304`, или `200` с тем же содержимым, или `200` с принятым новым |
| `changed_at` | `timestamptz NULL` | когда содержимое последний раз менялось (`updated_at` в API) |
| `etag`, `last_modified` | `text NULL` | заголовки последнего принятого `200` для условного запроса |
| `content_sha256` | `text NULL` | хеш тела последнего принятого `200` |
| `items`, `dropped` | `integer NOT NULL DEFAULT 0` | |
| `max_sol` | `integer NULL` | у traverse Curiosity — `NULL` |
| `last_error` | `text NULL` | `NULL` после успеха |

Строка создаётся при первой попытке (`INSERT ... ON CONFLICT DO NOTHING`).

## 3. Решения (ADR)

**ADR-1. Хранение traverse — нормализованные сегменты строками.**
- Отвергнуто: сырой GeoJSON блобом. Формат NASA ушёл бы в контракт, а у M20 в файле лишние свойства и мусорная `z`.
- Отвергнуто: линия по стоянкам. Теряется траектория между ними.

**ADR-1a. `/traverse` без фильтра по солам** (решение тимлида).
- Всегда весь маршрут.
- `properties.sol` может быть `null`: у Curiosity всегда.
- Фильтр по солам клиент делает через `/location`.
- Плюсы: один контракт для обоих марсоходов, нет фильтра, который у Curiosity молча ничего не делает.

**ADR-2. Связь «снимок → стоянка» — запросом в момент чтения**, без денормализации в `photos`.
- Стоянки публикуются позже снимков, и координаты появятся сами.
- Не нужен `UPDATE` 1,7 млн строк.

**ADR-3. Правило совпадения** (решение владельца, В2).
- Берётся стоянка того же `site` с наибольшим `drive`, не большим `drive` снимка; флаг `exact` показывает, совпал ли `drive`.
- Границу `site` не переходим.
- Цифры — раздел 1.

**ADR-4. Spirit и Opportunity** (В4) — `200`, `available: false`, пустые данные; в карточке `location: null`.

**ADR-5. Расписание.**
- В конце `SyncScheduler.run_once()` вызывается `LocationSyncService.refresh_due()`.
- Срок «раз в сутки» отсчитывается от `succeeded_at` в БД, поэтому переживает перезапуски.
- Отдельной задачи APScheduler нет.

**ADR-6. Условный запрос** (решение тимлида).
- Отправляем `If-None-Match` из сохранённого `etag` и `If-Modified-Since` из сохранённого `last_modified`.
- `304` — «не изменилось».
- `200` — сравниваем `sha256` тела: равен — «не изменилось»; иначе разбор и замена.
- Если сервер `304` не поддерживает, просто приходит `200` и работает сравнение хеша. Отдельный живой запрос для проверки `304` не нужен.
- В общем клиенте нужен новый метод (раздел 5): у `get_bytes` нет заголовков запроса и статуса в ответе.

**ADR-7. Приём файла.** Файл принимается, если выполнены все условия:
- корень `FeatureCollection`;
- валидных объектов ≥ 50%;
- строк не меньше `LOCATION_MIN_KEEP_RATIO` (0,9) от сохранённых;
- `max_sol` не уменьшился (если он есть).

Иначе:
- статус `rejected`, старые данные остаются;
- текст в `last_error`;
- `succeeded_at` не обновляется: для алерта это сбой.

Замена — одной транзакцией: `DELETE` строк марсохода, затем пакетный `INSERT`. `--force` в CLI снимает проверку уменьшения.

**ADR-8. Алерт** (решение тимлида, В6).
- Срабатывает, когда `coalesce(succeeded_at, created_at)` старше `LOCATION_STALE_DAYS` (3) у любого файла марсохода с источником: «больше 3 дней без успешной проверки», в том числе на свежей установке (отсчёт от первой попытки). Нет строки состояния — алерта нет. (Уточнено тимлидом по ревью.)
- Неизменное содержимое алертом не считается.
- Проверка — в `AlertMonitor.check()` раз в час.
- Одно сообщение при переходе в «не проверяется» и одно при восстановлении.
- `/health/sync` не меняется.
- Насколько стары сами данные, видно в API по `updated_at` и `source_max_sol`.

## 4. Нормализация (белый список)

**Стоянка** (`Point`):
- `sol`, `site`, `drive` — целые, обязательны;
- `lat`, `lon` — из свойств, обязательны;
- `elev_geoid`, `easting`, `northing`, `roll`, `pitch`, `yaw`, `tilt`, `dist_m`, `dist_total_m` — числа или `None`;
- правила `normalize.py`: `"UNK"`, пустое, `null` → `None`, числа-строки → числа;
- значение `-9999` в любом взятом поле → `None` (защита: в белом списке его сейчас нет);
- остальные свойства игнорируются (`images`, `Note`, `final`, `RMC`, `yaw_rad`, `dist_km`, `dist_mi`, `elev_radii`, `radius`, `drive_type`);
- если нет обязательного поля или геометрия не `Point` → `dropped`;
- повтор `(site, drive)` → остаётся объект с бо́льшим `sol` (при равном — последний), повтор считается в `dropped`;
- долгота больше 180 → вычесть 360.

**Сегмент:**
- `LineString` — один сегмент;
- `MultiLineString` — по сегменту на каждую часть;
- из каждой точки берутся `[lon, lat]`, `z` отбрасывается, округление до 7 знаков, долгота приводится как у стоянок;
- часть короче 2 точек и любая другая геометрия → `dropped`;
- `sol` — свойство `sol`, иначе `None`;
- `length_m` — `length` или `Length`;
- `seq` — сквозной порядок после разложения.

**Итог разбора:** `max_sol` — максимум `sol` (`None`, если ни у одного объекта его нет), `dropped` — число отброшенных.

## 5. Адаптер, клиент, загрузка

**Поток:**
1. MMGIS отдаёт файл.
2. `app/sources/locations.py` скачивает его условным запросом и разбирает.
3. `app/services/location_sync.py` решает, пора ли, проверяет файл по ADR-7, заменяет данные и пишет состояние.
4. Данные лежат в трёх таблицах.
5. `app/repositories/locations.py` их читает.
6. `app/services/locations.py` готовит ответы.
7. Роутеры: `app/api/v2/rovers.py` и `app/api/v2/photos.py`.

**Клиент** (`app/sources/http.py`). Новый метод `UpstreamClient.get_conditional(url, *, max_bytes, allowed_origin=None, etag=None, last_modified=None) -> ConditionalBody`:
- `@dataclass(frozen=True) ConditionalBody(not_modified: bool, body: bytes | None, etag: str | None, last_modified: str | None)`;
- те же ретраи, лимит частоты, `User-Agent`, предел размера и проверка хоста после редиректов, что у `get_bytes`;
- общая часть выносится, не копируется;
- `304` — без чтения тела.

**Адреса.** `Settings.location_base_url` + `{CODE}/Layers/json/{CODE}_{kind}.json`, где `CODE` берётся из `LOCATION_MISSIONS = {"perseverance": "M20", "curiosity": "MSL"}`. `allowed_origin` = `location_base_url`.

**Предел размера.** `LOCATION_MAX_BYTES` = 16 МиБ; сейчас самый большой файл — 1,7 МБ. Больше предела → `UpstreamError`, данные не трогаются.

**Когда.** `refresh_due()` идёт по марсоходам из `LOCATION_MISSIONS` и обоим `kind`:
- `succeeded_at` моложе `LOCATION_REFRESH_HOURS` (24) → пропуск без запроса;
- после сбоя повтор будет в следующем цикле планировщика, раз в 6 ч: в норме 4 запроса в сутки, при сбоях не больше 16;
- `LOCATION_SYNC_ENABLED=false` выключает загрузку (действует только при `SYNC_ENABLED`);
- исключения не выходят наружу: синхронизация снимков от них не страдает.

**Параллельность.** Обработка одного `(rover, kind)` идёт под `pg_try_advisory_xact_lock(LOCATION_LOCK_NAMESPACE, legacy_id * 2 + {waypoints: 0, traverse: 1})`. Если блокировка занята → `skipped`. Блокировку синхронизации снимков не берём.

**CLI:** `uv run python -m app.cli locations [--rover perseverance|curiosity] [--force]`.
- Грузит сразу, без проверки суток. Условный запрос остаётся; `--force` его не отправляет и снимает проверку уменьшения.
- Коды: 0 — всё `changed`, `unchanged` или `skipped`; 1 — есть `rejected` или `failed`; 2 — неверный марсоход, а для Spirit и Opportunity — `no location source`.

**Логи.** Info с итогом каждого файла; warning — при отброшенных объектах и сбоях. В `sync_runs` не пишем.

**Чтение:**
- `/location` — один запрос по `ix_rover_waypoints_rover_id_sol` плюс строка `location_datasets`;
- `/traverse` — все сегменты по PK и сборка GeoJSON (~1,9 тыс. объектов, ~64 тыс. точек), быстрый JSON-ответ; цель — меньше 200 мс локально;
- карточка — новая функция `v2.get_photo_detail()`: фото и поиск стоянки; `v2.get_photo()`, которым пользуется `/image`, не меняется.

**Кэш.** `cache_control_for`: пути `/rovers/{rover}/location` и `/rovers/{rover}/traverse` → `public, max-age=3600`.

## 6. Задания ролям

**Порядок:** «ок» владельца на текст раздела 8 → тимлид переносит его в `API.md` → `backend-dev` и `qa-engineer` работают параллельно → `data-engineer` проверяет живьём (см. ниже).

**`backend-dev`** (`app/`, `migrations/`, `.env.example`):
- миграция 0006: автогенерацию проверить глазами, `alembic check` чистый, `downgrade` до 0005;
- модели;
- `get_conditional` в клиенте;
- адаптер, репозиторий, `LocationSyncService`;
- вызов из `run_once()`: сервис создаётся в lifespan рядом с `SyncService` с тем же `UpstreamClient`;
- сервис чтения;
- две ручки, `Content-Type: application/geo+json` у `/traverse`;
- `location` в карточке, `cache_control_for`;
- CLI `locations`, алерт в `AlertMonitor`, настройки.

Не трогать: `app/api/legacy/`, `app/services/legacy.py`, сериализацию списков Photo v2.

**`qa-engineer`** (`tests/`, `scripts/record_fixtures.py`).

Фикстуры — офлайн из `data/locations/` (раздел 7).

Unit — `tests/unit/test_location_adapter.py`, `tests/unit/test_http_conditional.py`:
- разбор четырёх записанных фикстур: число объектов, типы, `max_sol`, у MSL traverse `sol = None`;
- точка M20 с `images` и `-9999`: в записи только поля белого списка;
- `z` отброшена, округление до 7 знаков;
- `MultiLineString` → 5 сегментов подряд с общим `sol`, `length_m` только у первого;
- синтетика:
  - числа строками и `"UNK"`;
  - точка без `lat`/`site` → `dropped`;
  - повтор `(site, drive)`;
  - долгота больше 180;
  - сегмент из одной точки и `Polygon` → `dropped`;
  - не `FeatureCollection` или не JSON → `LocationFormatError`;
- `get_conditional`:
  - отправляет `If-None-Match`/`If-Modified-Since` только если они есть;
  - `304` → `not_modified`, тело не читается;
  - `200` → тело и заголовки;
  - предел размера;
  - чужой хост после редиректа;
  - ретраи на `5xx`;
- URL адаптера, границы настроек.

Integration — `tests/integration/test_location_sync.py`:
- первая загрузка → строки и состояние (`etag`, `last_modified`, `sha256`, `changed_at`);
- повтор → в запросе условные заголовки:
  - respx `304` → `unchanged`, строки не тронуты, `succeeded_at` обновлён, `changed_at` прежний;
  - respx `200` с тем же телом → `unchanged`;
  - `200` с новым телом → `changed`;
- `succeeded_at` моложе суток → ноль запросов;
- через 24 ч → запрос есть;
- уменьшение больше 10% или уменьшение `max_sol` → `rejected`, старые данные целы, `succeeded_at` прежний; `--force` такой файл принимает;
- `500` или таймаут → `failed`, а синхронизация снимков в `run_once()` всё равно прошла;
- сбой одного файла не мешает остальным;
- занятая блокировка → `skipped`;
- CLI: коды 0/1/2;
- алерт:
  - `succeeded_at` старше 3 дней → одно сообщение;
  - повторная проверка → новых сообщений нет;
  - восстановление → одно сообщение;
  - неизменное содержимое при успешных проверках → сообщений нет;
- миграция 0006 вверх и вниз.

API — `tests/api/v2/test_location.py`, `test_traverse.py`, дополнения к `test_photo_detail.py`:
- `/location`:
  - форма ответа и сортировка;
  - фильтры `sol`, `from_sol`/`to_sol`;
  - `422`: `sol` вместе с диапазоном, `from_sol > to_sol`, выход за границы, не число;
  - `404`: неизвестный марсоход, не латиница;
  - Spirit и Opportunity → `available: false`, пусто, `null`;
  - до первой загрузки → `available: true`, пусто, `null`;
- `/traverse`:
  - валидный GeoJSON, только `LineString`, 2D-координаты;
  - `sol: null` у Curiosity;
  - неизвестные параметры (`from_sol`) игнорируются: ответ тот же;
  - `Content-Type`;
- для обеих ручек — `max-age=3600`, `ETag`/`304`, `HEAD`, gzip;
- карточка:
  - `exact: true`;
  - `exact: false` (снимок в пути и после последней стоянки);
  - `drive` меньше первой стоянки `site` → `null`;
  - `site` без стоянок → `null`;
  - `site`/`drive` `null` → `null`;
  - MER → `null`;
  - стоянка другого марсохода не подхватывается;
  - `location` — последний ключ;
- v1 `photos/{id}`, `rovers/{rover}/photos` и списки Photo v2 со стоянками и без них совпадают байт-в-байт;
- лимит частоты действует;
- обе ручки есть в OpenAPI.

**`data-engineer`** — после разработки, с «ок» владельца, те же 4 запроса:
- живой прогон `locations`;
- повторный прогон: видно, отвечает ли сервер `304` — записать в `DATA-SOURCES.md`;
- время ответа `/traverse` на полном объёме.

**`devops`** — новые переменные в `deploy/laptop`, если они там перечислены явно.

## 7. Фикстуры

Сеть не нужна: `qa-engineer` обрезает полные файлы из `data/locations/` в `tests/fixtures/upstream/mmgis/`. Это офлайн-режим `scripts/record_fixtures.py` (чтение из папки, без сети).
- `m20_waypoints.json` — 10–15 точек:
  - смена `site`;
  - сол с несколькими стоянками;
  - последняя стоянка `(93, 0)`;
  - точка с полным `images` и `elev_radii`/`radius = -9999.0`;
  - **пропуск**: две стоянки одного `site` с разрывом по `drive`, чтобы проверить правило ADR-3.
- `m20_traverse.json` — 5–8 сегментов с `sol`, `fromRMC`/`toRMC`, `length`, `SCLK_*`, `crs`; в одном — мусорная `z`.
- `msl_waypoints.json` — 10–15 точек, включая `site` 43 с первой стоянкой `drive` 216 и последнюю `(125, 2490)`.
- `msl_traverse.json` — **первый объект `MultiLineString` (5 частей) как есть** и 4–6 `LineString` с одним `Length`.

Обрезанные массивы `coordinates` у сегментов допустимы, ≥ 2 точек. Корневые члены (`type`, `name`, `crs`) сохраняются.

Синтетика: `tests/fixtures/upstream/synthetic/mmgis_waypoints_edge.json`, `mmgis_traverse_edge.json`.

Ответы с заголовками (`ETag`, `Last-Modified`, `304`) задаются в respx, отдельных файлов не нужно.

## 8. Текст для `API.md`

Вставить после раздела `GET /api/v2/rovers/{rover}/manifest`. Пункт «Положение» в «Черновиках» удалить. Строку про кэш — добавить в «Кэширование». Текст ниже — дословно.

````markdown
**Waypoint** (стоянка марсохода)
```json
{"sol": 2020, "site": 93, "drive": 1840,
 "lat": 18.4447, "lon": 77.3951, "elev_geoid": -2489.7,
 "easting": 4086431.2, "northing": 1092178.4,
 "roll": 1.2, "pitch": -3.4, "yaw": 142.0, "tilt": 3.6,
 "dist_m": 112.4, "dist_total_m": 33874.9}
```
Источник — слои карт MMGIS (JPL). `lat`, `lon` — градусы, долгота восточная от −180 до 180; `elev_geoid` — высота над ареоидом, м; `easting`/`northing` — м в проекции карты источника; `roll`, `pitch`, `yaw`, `tilt` — градусы; `dist_m` — длина проезда до стоянки, м; `dist_total_m` — пройденное расстояние по данным источника, м (не обязательно растёт от стоянки к стоянке). Любое поле, кроме `sol`, `site`, `drive`, `lat`, `lon`, может быть `null`.

### `GET /api/v2/rovers/{rover}/location`
Стоянки марсохода. Параметры (необязательные): `sol` — целое ≥ 0; `from_sol`, `to_sol` — диапазон включительно. `sol` вместе с `from_sol` или `to_sol` → `422`; `from_sol > to_sol` → `422`; границы — общие. Без пагинации.
```json
{"rover": "perseverance", "available": true,
 "updated_at": "2026-10-28T15:05:06Z", "source_max_sol": 2022,
 "items": [Waypoint, ...]}
```
- Сортировка `sol ASC, site ASC, drive ASC` (порядок движения).
- `available` — есть ли у марсохода источник положения: `true` у Curiosity и Perseverance, `false` у Spirit и Opportunity (источник неизвестен; тогда `items: []`, `updated_at` и `source_max_sol` — `null`).
- `updated_at` — когда стоянки последний раз изменились у нас; `source_max_sol` — последний сол стоянок у источника. Оба `null`, пока данных нет. Источник обновляется нерегулярно (бывает, неделями), поэтому `source_max_sol` может быть заметно меньше `max_sol` марсохода.
- Данные загружаются не чаще раза в сутки.

`200` · `404 rover_not_found` · `422 validation_error`

### `GET /api/v2/rovers/{rover}/traverse`
Весь маршрут марсохода в формате GeoJSON (RFC 7946), `Content-Type: application/geo+json`. Параметров нет (неизвестные игнорируются): маршрут отдаётся всегда целиком; стоянки за нужные солы — через `/location`.
```json
{"type": "FeatureCollection",
 "rover": "perseverance", "available": true,
 "updated_at": "2026-10-28T15:05:03Z", "source_max_sol": 2022,
 "features": [
   {"type": "Feature",
    "geometry": {"type": "LineString", "coordinates": [[77.4509, 18.4447], [77.4511, 18.4449]]},
    "properties": {"sol": 15, "length_m": 23.6}}]}
```
- Каждый объект — `LineString`, координаты `[lon, lat]` в градусах (долгота восточная, от −180 до 180), до 7 знаков после запятой, без высоты. Объекты — в порядке движения.
- `properties.sol` — сол проезда или `null`, если источник его не указывает (у Curiosity — всегда `null`); `properties.length_m` — длина по данным источника, м, или `null`.
- `rover`, `available`, `updated_at` — как у `/location` (дополнительные члены верхнего уровня, RFC 7946 §6.1); `updated_at` — когда маршрут последний раз изменился у нас; `source_max_sol` — наибольший `sol` среди объектов, `null`, если солов нет (Curiosity). У Spirit и Opportunity — `"available": false`, `"features": []`, остальное `null`.

`200` · `404 rover_not_found`

**PhotoDetail**: добавляется поле `location` (последним ключом):
```json
"location": {"sol": 2020, "site": 93, "drive": 1840, "lat": 18.4447, "lon": 77.3951,
             "elev_geoid": -2489.7, "exact": true}
```
- Берётся стоянка того же марсохода с тем же `site` и наибольшим `drive`, не большим `drive` снимка. `exact: true` — `drive` совпал (снимок сделан на этой стоянке); `false` — снимок сделан позже (в пути или после последней опубликованной стоянки), координаты — последней стоянки перед снимком (погрешность — до длины одного проезда, сотни метров).
- `location: null`, если у снимка нет `site` или `drive`, у марсохода нет источника положения (Spirit, Opportunity) или стоянки этого `site` с `drive` не больше снимкового нет.
- Photo в списках не меняется.
````

Строка для раздела «Кэширование» (дописать в пункт `Cache-Control`):

```markdown
`/rovers/{rover}/location` и `/rovers/{rover}/traverse` — `public, max-age=3600` (данные меняются не чаще раза в сутки);
```

**Совместимость:**
- в v2 изменения только добавляющие: две новые ручки и новый ключ в `PhotoDetail`;
- v1 байт-в-байт тот же: у него свой сериализатор `app/api/legacy/schemas.py`, `LEGACY-API.md` не меняется.

## 9. Общие имена для `backend-dev` и `qa-engineer`

Менять только вместе.

**Модули и функции:**
- `app/sources/http.py`:
  - `@dataclass(frozen=True) ConditionalBody(not_modified: bool, body: bytes | None, etag: str | None, last_modified: str | None)`;
  - `UpstreamClient.get_conditional(url, *, max_bytes, allowed_origin=None, etag=None, last_modified=None) -> ConditionalBody`.
- `app/sources/locations.py`:
  - `LOCATION_MISSIONS: dict[str, str]`;
  - `Kind = Literal["waypoints", "traverse"]`;
  - `@dataclass(frozen=True) WaypointRecord(sol, site, drive, lat, lon, elev_geoid=None, easting=None, northing=None, roll=None, pitch=None, yaw=None, tilt=None, dist_m=None, dist_total_m=None)`;
  - `@dataclass(frozen=True) TraverseSegment(seq: int, sol: int | None, length_m: float | None, coordinates: tuple[tuple[float, float], ...])`;
  - `@dataclass ParsedDataset[T](items: list[T], dropped: int, max_sol: int | None)`;
  - `parse_waypoints(body: bytes) -> ParsedDataset[WaypointRecord]`;
  - `parse_traverse(body: bytes) -> ParsedDataset[TraverseSegment]`;
  - `class LocationFormatError(UpstreamError)`;
  - `class LocationAdapter(client: UpstreamClient, settings: Settings)` с методами `url(rover_id, kind) -> str` и `async fetch(rover_id, kind, *, etag=None, last_modified=None) -> ConditionalBody`.
- `app/db/models.py`: `RoverWaypoint`, `RoverTraverseSegment`, `LocationDataset`; таблицы и индексы — раздел 2.
- `app/repositories/locations.py`:
  - `replace_waypoints(session, rover_id, items)`;
  - `replace_traverse(session, rover_id, items)`;
  - `get_dataset(session, rover_id, kind) -> LocationDataset | None`;
  - `save_dataset(session, rover_id, kind, **fields)`;
  - `list_waypoints(session, rover_id, from_sol, to_sol)`;
  - `list_traverse(session, rover_id)`;
  - `find_waypoint_for_photo(session, rover_id, site, drive) -> RoverWaypoint | None`.
- `app/services/location_sync.py`:
  - `LOCATION_LOCK_NAMESPACE`;
  - `@dataclass DatasetResult(rover_id, kind, status: Literal["changed", "unchanged", "skipped", "rejected", "failed"], items: int, dropped: int, error: str | None)`;
  - `class LocationSyncService(engine, client, settings)` с методами `async refresh_due(now=None) -> list[DatasetResult]` и `async refresh(rover_id, *, force=False) -> list[DatasetResult]`.
- `app/services/locations.py`:
  - `get_location(session, slug, sol, from_sol, to_sol) -> LocationData`;
  - `get_traverse(session, slug) -> TraverseData`;
  - `photo_location(session, photo) -> PhotoLocation | None`.
- `app/services/v2.py`: `get_photo_detail(session, photo_id)`.
- `app/api/v2/schemas.py`: `WaypointOut`, `LocationResponse`, `TraverseResponse`, `PhotoLocationOut`; поле `PhotoDetailOut.location: PhotoLocationOut | None` — последним.
- `app/services/alerts.py`: проверка свежести положения внутри `AlertMonitor.check()`; тексты сообщений — на усмотрение `backend-dev`.

**CLI.** Подкоманда `locations` с опциями `--rover` и `--force`, коды 0/1/2.

**Настройки** (`Settings`, `.env.example`):

| имя | значение по умолчанию |
|---|---|
| `location_sync_enabled: bool` | `True` |
| `location_base_url: str` | `"https://mars.nasa.gov/mmgis-maps/"` |
| `location_refresh_hours: int` | `Field(24, ge=1)` |
| `location_max_bytes: int` | `Field(16 * 1024 * 1024, ge=1024)` |
| `location_min_keep_ratio: float` | `Field(0.9, ge=0, le=1)` |
| `location_stale_days: int` | `Field(3, ge=1)` |

**Фикстуры:**
- `tests/fixtures/upstream/mmgis/{m20_waypoints,m20_traverse,msl_waypoints,msl_traverse}.json`;
- `tests/fixtures/upstream/synthetic/mmgis_{waypoints,traverse}_edge.json`.

## 10. Вопросы владельцу

Новых развилок нет. Владелец подтверждает только сам текст раздела 8. В нём видно отличие от первой редакции, принятой «ок»: у `/traverse` нет фильтра по солам, а `sol` бывает `null`.
