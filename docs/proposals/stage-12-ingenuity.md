# Этап 12: Ingenuity + три доработки API v2 для карты — черновик архитектора

Редакция 1 (2026-11-20, HEAD `480c70d`, миграции до 0007). Основа: `docs/DATA-SOURCES.md` («Полёты Ingenuity»), `docs/reports/stage-12-ingenuity-and-api-data.md`, `data/ingenuity/` (`flights.csv`, страница 0 фида), `design/NOTES.md` п. 13–15, код `app/`. Раздел 3 — текст для `API.md`, переносит тимлид после «ок» владельца.

## 0. Цель, границы, критерий

**Часть A (этап 12):** Ingenuity — отдельный «марсоход» v2 `ingenuity` (камеры, снимки, манифест, `/cameras`, `/photos`, `/latest`, карточка); журнал 72 полётов в таблице, ручки полётов; разовая загрузка всего фида `category=ingenuity`.
**Часть B (доработки v2):** фильтр `/photos` по `site` и диапазону `drive`; последняя стоянка `/location?latest=true`; упрощённый `/traverse` — **предлагаю не делать** (ADR-B2).
**Не входит:** Ingenuity в v1; положение/маршрут вертолёта; номер полёта у снимка точнее сола; регулярная синхронизация вертолёта.
**Ограничения:** v1 байт-в-байт как раньше (в том числе `/rovers` — 4 марсохода); в v2 только добавления (новый slug, новые ключи, новые необязательные параметры).
**Готово, когда:** `/api/v2/rovers` отдаёт 5 записей, у Ingenuity 14 553 снимка (± то, что источник добавит/уберёт) — сверка `reconcile` без необъяснённых расхождений; `/flights` — 72 полёта, у каждого `total_photos` по его солу; v1-тесты без изменений; `alembic downgrade 0007` работает.

## 1. Факты

| Что | Факт |
|---|---|
| журнал | PDF NASA → `flights.csv`: 72 строки, полёт 1 — сол 58 (2021-04-19), полёт 72 — сол 1035 (2024-01-18); сол уникален; `to` пуст в 27 полётах; дистанция и высота — целые м, скорость — до 2 знаков, длительность — 1 знак; дата — по тихоокеанскому времени (не UTC) |
| фид | `category=ingenuity`, формат и хост как у `mars2020`, `total_results` 14 553, камеры `HELI_NAV`, `HELI_RTE` («Color Camera»); `link` абсолютный; `site` = 0, `drive` = строка `"None"` у всех 100 записей стр. 0; `extended.dimension` `"(640,480)"`, `date_taken_mars` есть; `credit` `NASA/JPL-Caltech`; номера полёта нет |
| солы | номер сола в `imageid` и `sol` — солы Perseverance (сол 1069 = 2024-02-22, как у Perseverance) |
| не проверено | работает ли `latest=true` и фильтр `condition_*:sol` для `category=ingenuity`; нижняя граница солов; что снято в сол 1069 (после конца миссии); дата выгрузки вертолёта на грунт (по памяти 2021-04-03, в фактах нет) |
| миссия | конец — 2024-01-25 (страница миссии) |
| код | `rovers.legacy_id` NOT NULL UNIQUE и служит ключом advisory-блокировки синхронизации; `SYNCABLE_ROVERS` задаёт планировщик, `/health/sync`, admin-sync и `data_source`; `build_adapter` — по `_ADAPTERS`; v1 `list_rovers` берёт все строки `rovers`; `photo_meta` разбирает M20-формат только для `perseverance`; положение — `LOCATION_MISSIONS` (M20, MSL) |
| B: стоянки | точное `(site, drive)` у снимков: Perseverance 99,2%, Curiosity 71,2%; правило `location` (тот же `site`, `drive ≤`) — 99,8% / 99,4%; на стоянке снимают несколько солов; индекса по `site`/`drive` в `photos` нет |
| B: маршрут | Curiosity 27 018 точек, ~816 КБ JSON, gzip ~128 КБ; Perseverance 36 661 точка; ответ кэшируется (`max-age=3600`, ETag); замер 215 мс |

## 2. Решения

| № | Вопрос | Решение | Почему |
|---|---|---|---|
| A1 | v1 | Ingenuity **не видна** в v1: список `LEGACY_ROVERS` (4 slug) в `app/services/legacy.py`; `/rovers` фильтрует, `/rovers/ingenuity/...` и манифест — как неизвестный марсоход, `/photos/{id}` снимка вертолёта — `404` как скрытый | старый API вертолёта не знал; v1 — байт-в-байт |
| A2 | строка `rovers` | `id ingenuity`, `legacy_id 9` (только ключ блокировки, наружу не выходит), `name Ingenuity`, `mission mars2020`, `launch_date 2020-07-30`, `landing_date 2021-02-18`, `end_date 2024-01-25`, `status complete` | ADR-A1 |
| A3 | синхронизация | адаптер есть (`_ADAPTERS`), в `SYNCABLE_ROVERS` **нет**: не в планировщике, не в `/health/sync`, admin-sync → `422`, `data_source: "archive"`; загрузка — `backfill` | миссия закончена; фид заморожен |
| A4 | адаптер | `M2020Adapter` параметризуется (`rover_id`, `category`), `IngenuityAdapter` — наследник; `site` 0 / `drive "None"` → оба `NULL` без предупреждений | ADR-A2 |
| A5 | журнал | таблица `ingenuity_flights`, 72 строки сидом **в миграции** | ADR-A3 |
| A6 | связь | полёт ↔ снимки по солу; `total_photos` полёта — из `sol_summaries`; в карточке снимка Ingenuity — `flight` (номер или `null`) | по времени сузить нельзя (нет времени взлёта) |
| A7 | положение | `/location`, `/traverse` — `available: false`, `location: null` (без кода: Ingenuity нет в `LOCATION_MISSIONS`, `site/drive` пусты) | источника нет; слой вертолёта в MMGIS — вопрос В5 |
| A8 | `width`/`height`, `local_time` | M20-разбор `photo_meta` применяется и к `ingenuity` (`(640,480)`, LMST из `date_taken_mars`) | тот же формат |
| B1 | стоянка | `site` + `drive_from`/`drive_to` в `/photos`, новый индекс | ADR-B1 |
| B2 | упрощённый маршрут | **не делать сейчас** | ADR-B2 |
| B3 | последняя стоянка | `/location?latest=true` | ADR-B3 |

## 3. Текст для `API.md`

**→ «Общие правила v2»**, строка про `{rover}`: `curiosity`, `perseverance`, `opportunity`, `spirit`, `ingenuity`. Ошибки, строка 404: добавить `flight_not_found`.

**→ «Объекты», Rover** — после абзаца про `data_source`:
- `ingenuity` — вертолёт Ingenuity (миссия Mars 2020). Солы — солы Perseverance; `landing_date` — посадка вместе с Perseverance, `end_date` — `2024-01-25`, `status: "complete"`, `data_source: "archive"` (разовая загрузка фида, `last_synced_at` — время загрузки). Камеры `HELI_NAV`, `HELI_RTE`. В v1 Ingenuity нет.

**→ «Объекты», PhotoDetail** — после `in_sol`:
- `flight` — у снимков Ingenuity номер полёта, совершённого в сол снимка, или `null` (в этот сол полёта не было); у остальных марсоходов всегда `null`. Связь только по солу: снимок мог быть сделан до или после полёта в тот же сол. У Ingenuity `location` всегда `null`, `site`/`drive` — `null`.

**→ Новые объект и эндпоинты (после `/traverse`)**

**Flight** (полёт Ingenuity, журнал NASA)
```json
{"flight": 9, "sol": 133, "date": "2021-07-05",
 "distance_m": 625, "max_altitude_m": 10, "max_groundspeed_mps": 5.0, "duration_s": 166.4,
 "from": "Airfield E", "to": "Airfield F", "total_photos": 312}
```
- `date` — дата полёта по тихоокеанскому времени (так в журнале), не UTC; надёжный ключ — `sol`.
- `distance_m` — горизонтальная дистанция, м; `max_altitude_m` — м; `max_groundspeed_mps` — м/с; `duration_s` — с. Значения журнала без пересчёта; футы и мили не отдаются.
- `from` — площадка взлёта; `to` — площадка посадки, `null`, если в журнале не указана (в 27 полётах, обычно — посадка на той же площадке).
- `total_photos` — снимков Ingenuity за `sol` полёта (все типы); снимки — `/rovers/ingenuity/photos?sol={sol}`.

### `GET /api/v2/rovers/{rover}/flights`
Только `ingenuity`. `200 {"items": [Flight, ...]}` — все полёты без пагинации, сортировка `flight ASC`. Другой существующий марсоход → `404 not_found`; неизвестный → `404 rover_not_found`. `Cache-Control: public, max-age=300`.

### `GET /api/v2/rovers/{rover}/flights/{n}`
`n` — целое ≥ 1; нечисловое → `422`; нет такого полёта (в т.ч. `0` и больше 2 147 483 647) → `404 flight_not_found`. `200 Flight` · `404 rover_not_found` / `not_found` (как у списка).

**→ `GET /api/v2/rovers/{rover}/photos`** — в список параметров:
- `site` — целое ≥ 0; `drive_from`, `drive_to` — целые ≥ 0, только вместе с `site` (иначе `422`), включительно, любой можно опустить; `drive_from > drive_to` → `422`. Снимки без `site`/`drive` под эти фильтры не попадают. Сочетаются с остальными параметрами (И).
- Рецепт для стоянки `(S, D)` из `/location`: «снято на стоянке» — `site=S&drive_from=D&drive_to=D`; «как в `location` карточки» (на стоянке и в пути до следующей) — `site=S&drive_from=D&drive_to={drive следующей стоянки того же site} − 1` (у последней стоянки `site` — без `drive_to`). Снимки стоянки могут относиться к нескольким солам.
- В абзаце про `nearest_sols`: «Фильтры `camera`, `sample_type`, `site`, `drive_from`, `drive_to` не учитываются».

**→ `GET /api/v2/rovers/{rover}/location`** — в параметры:
- `latest` — `true` или `false` (также `1`/`0`), по умолчанию `false`; другое значение → `422`. При `true` в `items` — только последняя стоянка (наибольшие `sol`, `site`, `drive`) или `[]`, если стоянок нет; вместе с `sol`, `from_sol` или `to_sol` → `422`. Остальные члены ответа — как без параметра.

**→ «Admin», `POST /admin/sync`**: `422 validation_error` также для `ingenuity` (миссия завершена, снимки загружены разово).

**→ `/health/sync`**: «Spirit, Opportunity и Ingenuity — нет».

## 4. ADR

**ADR-A1. Как держать v1 неизменным.**
- А. `legacy_id` → NULL у Ingenuity, v1 фильтрует `legacy_id IS NOT NULL`. Минус: блокировки синхронизации и положения берут ключ из `legacy_id` — надо менять схему ключей; nullable-колонку надо проверять везде.
- Б. `legacy_id = 9` (внутренний), явный список `LEGACY_ROVERS` в сервисе v1. Плюсы: схема и блокировки не меняются, одна точка правды для v1. Минус: имя `legacy_id` у Ingenuity условно (документируем в `ARCHITECTURE.md`).
- В. Столбец `in_v1 boolean`. Лишняя миграция ради константы.
- **Рекомендация: Б.**

**ADR-A2. Адаптер.** А — копия `m2020.py` (дублирование ~400 строк сложной логики пагинации); Б — параметризовать `M2020Adapter` (`rover_id`, `category` — атрибуты класса, метка в логах и `build_record` — из `rover_id`), `parse_m2020_image(raw, rover_id="perseverance")`. **Рекомендация: Б.** Поведение Perseverance не меняется (тесты адаптера — регрессия). Для Ingenuity: `site == 0` и `drive` не число → оба `None` молча (это заглушки фида, не стоянки); `link` сохраняется как есть.

**ADR-A3. Сид журнала.**
- А. 72 строки литералом в миграции (как `ROVERS` в 0002), в шапке — URL PDF и sha256. Плюсы: прод и тесты получают данные `alembic upgrade`, `migrated_db` — без шагов; журнал заморожен. Минус: правка данных — новой миграцией.
- Б. Команда CLI `import-flights --csv`: `data/` в `.gitignore`, на ноутбуке файла нет — лишний ручной шаг, тестам нужна своя фикстура.
- **Рекомендация: А.**

**ADR-A4. Полёты в API.** Путь `/rovers/{rover}/flights` (а не `/ingenuity/flights`): общий разбор slug и регистра, место рядом с `/location`. Для чужих марсоходов — `404 not_found`, а не пустой список: полётов у них не бывает в принципе. `total_photos` — JOIN `sol_summaries` по `(rover_id='ingenuity', sol)` при чтении, не хранится.

**ADR-A5. `mission` у Ingenuity.** `mars2020` (рекомендую: часть миссии Mars 2020, солы Perseverance) или `ingenuity` (как `mission` в шапке фида). Поле справочное, клиенты его не разбирают.

**ADR-B1. Фото стоянки.**
- А. `site` + `drive` — точное совпадение. Просто, но у Curiosity теряет ~28% снимков, которые карточка относит к стоянке (`exact: false`).
- Б. Сервер сам вычисляет «как в `location`» по `rover_waypoints`. Удобно, но фильтр `/photos` начинает зависеть от данных положения (MER, Ingenuity — без них), смысл параметра неочевиден.
- В. `site` + `drive_from`/`drive_to`: сервер ничего не знает о стоянках, обе семантики (А и Б) получает клиент, у которого список стоянок уже загружен (карта). **Рекомендация: В.**
- Требовать `sol` вместе с `site` нельзя: стоянка длится несколько солов. Нужен индекс: без него выборка по `site` у Curiosity обходит индекс сола по всем ~790 тыс. строкам (сотни мс–секунды). Новый частичный индекс `(rover_id, site, drive) WHERE site IS NOT NULL` — ~1,3 млн строк, оценка 40–60 МБ, строится секунды; в стоянке сотни–тысячи снимков, сортировка и `count` по ним — единицы мс (оценка; EXPLAIN — `data-engineer`).

**ADR-B2. Упрощённый `/traverse`.**
- А. `simplify=<м>` (Дуглас–Пекер на запрос): произвольный параметр ломает кэш (ETag/браузер на каждое значение), CPU на ноутбуке, валидация.
- Б. `detail=low`: упрощение с фиксированным допуском, кэш в памяти по `(rover, changed_at)` — ~60 строк кода, без миграции.
- В. Не делать: gzip уже даёт ~128 КБ, ответ кэшируется браузером на час и `ETag`; отрисовку прореживает фронтенд (он и так строит один `<path>`).
- **Рекомендация: В сейчас; Б — если замер покажет проблему** (порог: gzip > 300 КБ или p95 > 500 мс на ноутбуке, `data-engineer`).

**ADR-B3. Последняя стоянка.** А — `latest: Waypoint` в объекте Rover: лишний запрос во всех `/rovers`, смешение снимков и положения. Б — `latest` член ответа `/location`: без параметра всё равно отдаётся весь список. В — `?latest=true` у `/location`: тот же конверт (`available`, `source_max_sol`), один запрос `ORDER BY sol DESC, site DESC, drive DESC LIMIT 1` по индексу `ix_rover_waypoints_rover_id_sol`. **Рекомендация: В.**

## 5. Модель данных и миграции

**0008 `20261121_0008_ingenuity.py`** (`down_revision "0007"`):
- `INSERT rovers` (A2), `INSERT cameras` `HELI_NAV` (`Navigation Camera`, `sort_order 1`), `HELI_RTE` (`Color Camera`, 2); имена — и в справочник `app/core/cameras.py`.
- `ingenuity_flights`: `flight smallint` PK (`CHECK flight > 0`), `sol integer NOT NULL UNIQUE`, `date date NOT NULL` (PT), `distance_m integer NOT NULL`, `max_altitude_m integer NOT NULL`, `max_groundspeed_mps numeric(5,2) NOT NULL`, `duration_s numeric(6,1) NOT NULL`, `route_from text NOT NULL`, `route_to text NULL`; сид 72 строк. Индекс по `sol` — уникальный ключ (связь со снимками).
- `downgrade`: `DROP TABLE ingenuity_flights`; удалить строки `ingenuity` из `photos`, `camera_sol_counts`, `sol_summaries`, `sol_source_counts`, `sync_runs`, `cameras`, затем `rovers`. Это удаление загруженных снимков — на проде только с «ок» владельца.

**0009 `20261121_0009_photos_site_drive_index.py`**: `ix_photos_rover_id_site_drive (rover_id, site, drive) WHERE site IS NOT NULL`; `downgrade` — `drop_index`. Объёмы: `photos` +14,6 тыс. строк (<1%).

| Запрос | Как | Индекс | Цена |
|---|---|---|---|
| `/flights` | `ingenuity_flights LEFT JOIN sol_summaries` | PK обеих | 72 строки, < 1 мс |
| `flight` в карточке | по `sol`, только для `ingenuity` | UNIQUE `sol` | < 0,1 мс |
| `/photos?site=` | `site = S AND drive BETWEEN`, сортировка как всегда | новый 0009 | оценка — единицы мс |
| `/location?latest=true` | `LIMIT 1` обратным обходом | `ix_rover_waypoints_rover_id_sol` | < 1 мс |

**Поток Ingenuity:** фид JPL `category=ingenuity` → `IngenuityAdapter` (общий `UpstreamClient`, ≤ `UPSTREAM_MAX_RPS`) → `SyncService` в режиме `backfill` (upsert, сводки, `sync_runs`) → таблицы → `v2.py` → роутер. Повтор идемпотентен (`ON CONFLICT`); сбой пачки — `partial`, повторный `backfill` догружает.

**Порядок загрузки (разовый, ~146 страниц уже разрешены):**
1. Проверка фильтра — одна пачка: `backfill --rover ingenuity --from-sol 1000 --to-sol 1099`. Если `sync_runs.source_total` пачки = 14 553 (фильтр по солу не работает) — **стоп**, иначе каждая пачка выкачает весь фид (~146 страниц × 12).
2. Весь диапазон: `backfill --rover ingenuity --from-sol 0 --to-sol 1199` (явный `--to-sol`: `latest=true` для этой категории не проверен). ~12 пачек, ~160–200 запросов, 2–3 мин.
3. `reconcile --rover ingenuity`: сумма = `total_results` источника.

## 6. Задания ролям и общие имена

**Общие имена** (`backend-dev` реализует, `qa-engineer` тестирует параллельно):
- `app/sources/m2020.py`: `M2020Adapter.rover_id`, `M2020Adapter.category` (атрибуты класса); `parse_m2020_image(raw, rover_id: str = "perseverance")`.
- `app/sources/ingenuity.py`: `IngenuityAdapter(M2020Adapter)` (`rover_id = category = "ingenuity"`), `parse_ingenuity_image(raw) -> PhotoRecord | None`.
- `app/sources/registry.py`: `_ADAPTERS["ingenuity"]`; `SYNCABLE_ROVERS` без изменений.
- `app/sources/photo_meta.py`: M20-разбор для `perseverance` и `ingenuity`.
- `app/services/legacy.py`: `LEGACY_ROVERS: frozenset[str]`.
- `app/db/models.py`: `IngenuityFlight` (поля как в 0008).
- `app/repositories/flights.py`: `list_flights(session) -> list[tuple[IngenuityFlight, int]]`, `get_flight(session, n) -> tuple[IngenuityFlight, int] | None`, `flight_by_sol(session, sol) -> int | None`.
- `app/repositories/photos.py`: `PhotoFilter.site: int | None`, `drive_from: int | None`, `drive_to: int | None`.
- `app/repositories/locations.py`: `latest_waypoint(session, rover_id) -> RoverWaypoint | None`.
- `app/services/v2.py`: `FLIGHT_ROVER = "ingenuity"`; `list_flights(session, slug)`, `get_flight(session, slug, n)`; `PhotoDetail.flight: int | None`; `search_photos(..., site=, drive_from=, drive_to=)`.
- `app/services/locations.py`: `get_location(session, slug, ..., latest: bool = False)`.
- `app/core/errors.py`: `FlightNotFound` (`404 flight_not_found`).
- `app/api/v2/schemas.py`: `FlightOut(flight, sol, date, distance_m, max_altitude_m, max_groundspeed_mps, duration_s, from_, to, total_photos)` — `from` через alias; `FlightsResponse(items)`; `PhotoDetailOut.flight` (последним ключом).
- `app/api/v2/rovers.py`: ручки `/rovers/{rover}/flights`, `/rovers/{rover}/flights/{n}`; `Cache-Control` как у списков (`caching.py`).

**`backend-dev`:** всё выше + миграции 0008, 0009; сообщение admin-sync `422` для `ingenuity`; v1 — только `LEGACY_ROVERS` (`list_rovers`, `_find_rover`, `_visible`/`get_photo`). Числа `numeric` в JSON — числами, не строками.

**`qa-engineer`** (`tests/`; фикстура — урезанная `raw_images_ingenuity_p0.json`, 3–5 записей, + запись-миниатюра и запись с `drive` числом, если нужно для граней):
- unit адаптер: `category=ingenuity` в запросе; `site 0`/`drive "None"` → `None`, без предупреждений; абсолютный `link`; камеры; `credit` как в источнике; Perseverance — без изменений (существующие тесты).
- unit `photo_meta`: Ingenuity `(640,480)` и LMST.
- integration: backfill Ingenuity на фикстуре (respx), сводки, `sync_runs`; scheduler не трогает Ingenuity; `/health/sync` без неё; миграции 0008/0009 вверх-вниз, 72 строки сида, полёт 1 и 72 сверены с CSV.
- api v1: `/rovers` байт-в-байт как до этапа; `/rovers/ingenuity`, `/rovers/ingenuity/photos`, `/manifests/ingenuity` — как неизвестный марсоход; `/photos/{id}` снимка Ingenuity → `404`.
- api v2: `/rovers` — 5 записей, Ingenuity (`data_source`, `end_date`, `status`); `/flights` — 72, порядок, `to: null`, `total_photos` (сол со снимками / без); `/flights/1`, `/flights/72`, `/flights/0`, `/flights/73`, `/flights/abc`, `/flights/99999999999` ; `/rovers/curiosity/flights` → `404 not_found`; `/rovers/xyz/flights` → `rover_not_found`; `INGENUITY` в верхнем регистре; карточка снимка: `flight` есть/`null`, у Perseverance `null`, `location: null`; admin-sync `ingenuity` → `422`; `/location`, `/traverse` → `available: false`.
- api B1: `site` один; `site`+`drive_from`=`drive_to`; полуоткрытые диапазоны; `drive_from` без `site` → `422`; `drive_from > drive_to` → `422`; границы (`-1`, `2147483648`, `abc`); с `sol`/`camera`/`sample_type`; снимки без `site` не попадают; `nearest_sols` не зависит от `site`; ETag разный для разных параметров.
- api B3: `latest=true|1` — одна последняя стоянка; `false|0` — как без параметра; `latest=yes` → `422`; с `sol`/`from_sol` → `422`; марсоход без стоянок — `[]`; Spirit — `available: false`.

**`data-engineer`:** EXPLAIN `/photos?site=` после 0009 (самая «тяжёлая» стоянка Perseverance и Curiosity, с `count`); после загрузки — распределение снимков Ingenuity по солам, природа сола 1069, совпадение солов полётов со снимками (сколько полётов без снимков); при желании — замер gzip/p95 `/traverse` (ADR-B2).

**`frontend-dev`:**
- Ingenuity в списке марсоходов и на главной (карточка завершённой миссии, «Imported»); страницы марсохода работают без изменений (сетка, сол, карточка, `width`/`height`, местное время).
- Вкладка «Flights» (вместо «Ingenuity flights · Soon» у Perseverance — ведёт на `/rovers/ingenuity/flights`): таблица 72 полётов (номер, сол, дата PT, дистанция, высота, скорость, длительность, from → to, число снимков → `/photos?sol=`), итоги (сумма дистанций/времени) считает фронтенд. «Route map» у Ingenuity — состояние `available: false`, как у MER.
- Карточка снимка Ingenuity: «Flight N» со ссылкой; при `null` — ничего.
- Карта: кнопка «Photos at this stop» (`site`, `drive_from`, `drive_to` по рецепту раздела 3) рядом с «Photos of sol N»; главная/шапка карты — последняя стоянка через `?latest=true`.
- Макет экрана полётов — `designer` (отдельно, до `frontend-dev`).

**`devops`:** после выкатки `alembic upgrade head` (0009 строит индекс секунды, синхронизацию на это время лучше остановить), затем порядок загрузки из раздела 5 на ноутбуке.

## 7. Вопросы владельцу

**В1. Утвердить раздел 3** (новый slug `ingenuity`, `/flights`, `flight` в карточке, `site`/`drive_from`/`drive_to`, `?latest=true`)? Только добавления в v2, v1 не меняется. *Рекомендую: да.*

**В2. Ingenuity в v1 — нет?** *Рекомендую: нет* (старый API его не знал; клиентам v1 он не нужен).

**В3. Упрощённый `/traverse` — не делать сейчас** (gzip ~128 КБ, кэш на час), вернуться по замеру? *Рекомендую: да, не делать.*

**В4. Даты Ingenuity:** `landing_date` = посадка Perseverance 2021-02-18 (а не выгрузка на грунт — её даты в проверенных фактах нет), `end_date` = 2024-01-25. *Рекомендую: да.*

**В5. Положение вертолёта:** есть ли у MMGIS слой Ingenuity — не проверено; искать (новые запросы, отдельное «ок») или оставить `available: false`? *Рекомендую: оставить, вернуться после этапа.*

**В6. Проверочная первая пачка загрузки** (сол 1000–1099, ~10 запросов) входит в уже разрешённые ~146 страниц? *Рекомендую: да; если фильтр по солу не работает — останавливаемся и приходим с вопросом.*
