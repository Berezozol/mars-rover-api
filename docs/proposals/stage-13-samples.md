# Этап 13: образцы Perseverance — черновик архитектора

Редакция 1 (2026-11-20, HEAD `6046e38`, миграции до 0009). Основа: `docs/DATA-SOURCES.md` («Образцы Perseverance»), `docs/reports/stage-13-samples.md`, сохранённая страница `data/samples/rock-samples.html` (+ `.headers`), разбор её таблицы `html.parser` (без сети), dev-БД `mars` (снимки и сводки Perseverance), код `app/`. Раздел 3 — текст для `API.md`, переносит тимлид после «ок» владельца.

## 0. Цель, границы, критерий

**Входит:** таблица `rover_samples` из HTML-таблицы страницы NASA «Mars Rock Samples»; регулярная проверка страницы (раз в сутки, как положение в этапе 11) и CLI; ручки `/api/v2/rovers/{rover}/samples` и `/samples/{number}`; связь образца со стоянкой и снимками его сола; `samples` в карточке снимка; алерт о долгом сбое проверки.
**Не входит:** v1; образцы после № 30 и witness-трубки (их нет в источнике), ручной ввод; фото образцов со страницы; разбор PDF-досье PDS (только ссылка); координаты точки бурения (есть только у стоянки).
**Готово, когда:** после `uv run python -m app.cli samples` в БД 30 образцов, поля совпадают с таблицей страницы (контрольные значения — раздел 6); у 28 образцов с солом есть `total_photos`, у тех, чей сол покрыт стоянками, — `location`; повторный запуск — `unchanged`; смена разметки (фикстура) → данные не тронуты, `last_error`, алерт по сроку; `alembic downgrade 0009` работает; v1 и прежние тесты v2 без изменений.

## 1. Факты

| Что | Факт |
|---|---|
| страница | `https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/`, 200, ~495 КБ, `text/html; charset=UTF-8`; **нет `ETag` и `Last-Modified`** (только `cache-control: max-age=300, must-revalidate`) — условный запрос ничего не даст; в HTML есть `nonce` (2 вхождения) — sha256 всего тела, вероятно, меняется от запроса к запросу |
| свежесть | `<meta property="article:modified_time" content="2026-06-20T12:45:46-04:00">`; последний сол в таблице 1441 (№ 28), сейчас сол ~2030 |
| таблица | ровно один `<table>` (в `figure.wp-block-table`), `thead` + 30 строк `tbody`, 11 `th`: `Sample No.`, `Date Sealed`, `Sol Sealed`, `Sample Name`, `Feature Name`, `Sampling Location`, `Current Location`, `Science Notes&nbsp;` (внутри ссылка), `Sample Type`, `Sample Height<br>cm inch`, `Rock Type` |
| номера | 1…30 без пропусков; солы 28 образцов уникальны (164…1441); № 29, 30 — `TBD` в дате и соле |
| даты | AP-стиль: `Aug. 6, 2021`, `Sept. 6, 2021`, `Nov.`, `Dec.`, `Jan.`, `Oct.`, `March`, `June`, `July`; часовой пояс не указан |
| высота | `5.98` + 9×`&nbsp;` + `2.35` (см, дюймы); № 25 — `6.2`; № 1 — `n/a … n/a`; № 29 — `Est. 5-7 cm&nbsp;(2-2.8 in)`; № 30 — `1.91 cm (0.75 inches)` |
| досье | ячейка `Science Notes` — текст `PDF` и ссылки: № 1–18 — две (PDS `…/initial_reports_volumeN.pdf#page=K` и зеркало `mars.nasa.gov/internal_resources/…`), № 19–25 — одна (PDS), № 26–30 — ячейка пуста |
| текст | `Ha'ahóni (aka "Hahonih")`, `Atsá (aka "Atsah")`; `Octavia E. Butler Landing / Ch’ał outcrop` (типографский апостроф); `South Séítah`; `Rock Type` у № 1 — `n/a` |
| перечисления | `Current Location`: `Perseverance Rover` 21, `Sample Depot` 9; `Sample Type`: `Rock Core` 27, `Regolith` 2 (№ 17, 18), `Atmospheric` 1 (№ 1) |
| снимки сола (dev-БД) | у всех 28 солов есть снимки (31…671, сумма ~7,9 тыс.); **во всех 28 солах снимки с `site`/`drive` дают ровно одну пару** (стоял на месте); `CACHECAM` (камера пробирок) — в 27 из 28 солов (нет у сола 377, № 9) |
| стоянки | в dev-БД `rover_waypoints` пуст — совпадение солов образцов со стоянками [не проверено], задача `data-engineer` на проде |
| код | положение: `LocationSyncService` (`refresh_due`, `_check`, `pg_try_advisory_xact_lock`, `location_datasets` с `CHECK kind IN ('waypoints','traverse')`), `UpstreamClient.get_conditional(max_bytes, allowed_origin)`, `AlertMonitor._check_locations`, `SyncScheduler.run_once → _refresh_locations`; `find_waypoint_for_photo` (тот же `site`, наибольший `drive ≤`); кэш — `caching.py` (`_LOCATION_SEGMENTS`, `LOCATION_CACHE_CONTROL` 3600) |

## 2. Решения

| № | Вопрос | Решение | Почему |
|---|---|---|---|
| D1 | загрузка | периодическая проверка страницы раз в сутки + CLI `samples [--force]`, **без сида** | ADR-1 |
| D2 | парсер | stdlib `html.parser`, без новых зависимостей | ADR-2 |
| D3 | «изменилось ли» | sha256 канонического JSON разобранных строк, а не тела; условные заголовки шлём, если когда-нибудь появятся | `nonce` в HTML, валидаторов нет |
| D4 | приёмка | всё или ничего: заголовки, номера, солы, даты — строго; перечисления и высота — мягко | ADR-3 |
| D5 | модель | `rover_samples` (сырые строки + разобранные значения) и `sample_datasets` (состояние) | ADR-4 |
| D6 | связь со стоянкой | при чтении: пара `(site, drive)` снимков сола → правило карточки снимка; иначе — по солу | ADR-5 |
| D7 | API | список и карточка, без фильтров; чужой марсоход — `404 not_found`, как `/flights`; `max-age=3600` | ADR-6 |
| D8 | карточка снимка | `samples: [номера]` последним ключом | ADR-7 |
| D9 | свежесть | `/health/sync` не меняется; алерт, если страница не проверяется успешно `SAMPLES_STALE_DAYS` | как положение |

## 3. Текст для `API.md`

**→ «Общие правила v2», ошибки, строка 404:** добавить `sample_not_found`.

**→ «Объекты», PhotoDetail** — после `flight`:
- `samples` (этап 13, последним ключом) — номера образцов Perseverance, запечатанных в сол снимка, по возрастанию; `[]`, если в этот сол образцов нет, и у остальных марсоходов. Связь только по солу: снимок не обязательно изображает образец (пробирку снимает камера `CACHECAM`).

**→ Новые объект и эндпоинты (после `/flights`)**

**Sample** (образец Perseverance; данные страницы NASA «Mars Rock Samples»)
```json
{"number": 25, "name": "Sapphire Canyon", "feature_name": "Cheyava Falls",
 "sampling_location": "Neretva Vallis",
 "status": "on_rover", "current_location": "Perseverance Rover",
 "sample_type": "rock_core", "sample_type_label": "Rock Core", "rock_type": "Sedimentary",
 "sealed_on": "2024-07-21", "sol": 1215, "height_cm": 6.2, "height_note": null,
 "dossier_url": "https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_sample_dossier/initial_reports/initial_reports_volume4.pdf#page=120",
 "total_photos": 289,
 "location": {"sol": 1212, "site": 56, "drive": 0, "lat": 18.4447, "lon": 77.3951,
              "elev_geoid": -2489.7, "match": "exact"}}
```
(координаты в примере условные)
- `number` — порядковый номер образца на странице (не серийный номер пробирки). Строки — как на странице (имена с диакритикой и псевдонимами, `(aka "…")`, не разбираются).
- `feature_name`, `sampling_location`, `rock_type` — текст страницы или `null` (пусто, `n/a`).
- `status` — `on_rover` (на борту) или `depot` (в депо на поверхности); `null`, если на странице незнакомое значение; `current_location` — текст страницы всегда.
- `sample_type` — `rock_core`, `regolith`, `atmospheric` или `null` (незнакомое значение); `sample_type_label` — текст страницы.
- `sealed_on` — дата запечатывания как на странице (часовой пояс не указан), `sol` — сол; оба `null`, пока на странице `TBD`. Надёжный ключ — `sol`.
- `height_cm` — высота керна, см, или `null` (нет или только оценка); `height_note` — текст страницы, если высоту нельзя дать числом (`"Est. 5-7 cm (2-2.8 in)"`), иначе `null`.
- `dossier_url` — PDF научного досье в PDS или `null`.
- `total_photos` — снимков Perseverance за `sol` (все типы); `null` при `sol: null`. Снимки сола — `/rovers/perseverance/photos?sol={sol}`, пробирка — `&camera=CACHECAM`.
- `location` — стоянка, где стоял марсоход в сол запечатывания, или `null` (нет `sol`, нет стоянок): поля как у `location` в PhotoDetail, вместо `exact` — `match`: `exact` — `site`/`drive` снимков этого сола совпали со стоянкой; `nearest_before` — стоянка того же `site` с ближайшим меньшим `drive`; `by_sol` — снимков с `site`/`drive` в этот сол нет (или пар несколько), взята последняя стоянка с `sol` не больше сола образца.

### `GET /api/v2/rovers/{rover}/samples`
Только `perseverance`. Параметров нет (неизвестные игнорируются). Все образцы без пагинации, сортировка `number ASC`.
```json
{"rover": "perseverance",
 "source_url": "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/",
 "source_updated_at": "2026-06-20T16:45:46Z", "updated_at": "2026-11-21T03:00:12Z",
 "items": [Sample, ...]}
```
- `source_url` — страница-источник (данные NASA; проект неофициальный). `source_updated_at` — дата правки страницы по её метаданным или `null`; `updated_at` — когда список последний раз изменился у нас; оба `null` и `items: []`, пока данных нет.
- Страница обновляется с отставанием от миссии (месяцами): последних образцов в ней может не быть. Данные проверяются не чаще раза в сутки.
- Другой существующий марсоход → `404 not_found`; неизвестный → `404 rover_not_found`. `Cache-Control: public, max-age=3600`.

### `GET /api/v2/rovers/{rover}/samples/{number}`
`number` — целое ≥ 1; нечисловое → `422`; нет такого образца (в т.ч. `0`, отрицательные и больше 32 767) → `404 sample_not_found`. `200 Sample` · `404 rover_not_found` / `not_found` (как у списка). `Cache-Control: public, max-age=3600`.

**→ «Кэширование»:** к `location`/`traverse` (`max-age=3600`) добавить `/rovers/{rover}/samples` и `/samples/{number}`.

## 4. ADR

**ADR-1. Механизм загрузки.**
- А. Сид 30 строк в миграции 0010 (как полёты). Плюс: данные сразу, без запроса. Минусы: список не застывший (страница правится, `TBD` у № 29–30 заполнят, появятся новые) — каждая правка = миграция; текст NASA в репозитории.
- Б. Проверка раз в сутки + CLI (как положение). Плюсы: правки страницы приходят сами; при смене разметки старые данные остаются, срабатывает алерт. Минус: внешний запрос к новому хосту (~495 КБ/сутки) — нужно «ок» владельца.
- В. Сид + проверка. Две точки правды ради выигрыша в одни сутки до первой загрузки; при отказе парсера сид всё равно устареет.
- **Рекомендация: Б.** Первая загрузка — `samples` из CLI после выкатки. Тестам сид не нужен: они работают на фикстуре через respx.

**ADR-2. Парсер.** А — `html.parser` (stdlib): таблица простая, проверочный разборщик (~40 строк, события `starttag`/`data`/`endtag`) извлёк все 30 строк и заголовки без потерь; ~500 КБ разбираются за десятки мс, в `asyncio.to_thread`. Б — `selectolax`/`lxml`: быстрее и CSS-селекторы, но новая бинарная зависимость ради 30 строк раз в сутки. **Рекомендация: А.** Таблица ищется не по классу `wp-block-table`, а по заголовкам: ровно одна `<table>`, в первой строке которой есть `Sample No.`.

**ADR-3. Приёмка страницы (всё или ничего).** Нормализация ячейки: `&nbsp;` → пробел, `<br>` → пробел, пробелы схлопнуть, `strip`, Unicode NFC. Отказ (`rejected`, данные не трогаются, причина в `last_error`):
1. нет таблицы или их больше одной; нормализованные заголовки (без учёта регистра) не содержат все 11 ожидаемых: `sample no.`, `date sealed`, `sol sealed`, `sample name`, `feature name`, `sampling location`, `current location`, `science notes`, `sample type`, `sample height cm inch`, `rock type` (лишние колонки — предупреждение в лог); колонки — по имени, не по позиции;
2. `Sample No.` не целое 1…32 767 или повторяется; пустое `Sample Name`; строк 0;
3. `Sol Sealed` не `TBD`/пусто и не целое ≥ 0; `Date Sealed` не `TBD`/пусто и не разбирается (месяцы: `Jan.`, `Feb.`, `March`, `April`, `May`, `June`, `July`, `Aug.`, `Sept.`/`Sep.`, `Oct.`, `Nov.`, `Dec.` и полные имена);
4. без `--force`: пропал хотя бы один сохранённый номер (образцы не исчезают — это признак поломки, а не правки).
Мягко (без отказа): незнакомые `Current Location`/`Sample Type` → `status`/`sample_type` `null` + предупреждение; высота: `^число число$` или `^число cm` → `height_cm` (первое число), `n/a`/пусто → `null`, остальное → `null` + `height_note`; `dossier_url` — первая ссылка `https://` в ячейке `Science Notes`, иначе `null` (другие схемы не принимаются); `n/a` в тексте → `null`.

**ADR-4. Модель.** Состояние: А — `location_datasets` с `kind = 'samples'` (нужна правка `CHECK`, а `location_health`/алерты и `LocationSyncService` перебирают эту таблицу как «положение» — смешение); Б — своя таблица `sample_datasets` (одна строка на марсоход, ~10 столбцов). **Рекомендация: Б.** Замена списка — `DELETE` + `INSERT` 30 строк одной транзакцией под `pg_try_advisory_xact_lock(SAMPLES_LOCK_NAMESPACE, legacy_id)`. Связь со стоянкой **не хранится** (ADR-5).

**ADR-5. Связь со стоянкой и снимками.**
- А. По солу (из отчёта): стоянка этого сола, иначе последняя с `sol ≤`. Минус: в сол бывает до 4 стоянок; если марсоход в сол запечатывания отъехал, «стоянка сола» — уже после бурения.
- Б. По снимкам сола: в каждом из 28 солов снимки с `site`/`drive` дают ровно одну пару — это место бурения; дальше то же правило, что `location` карточки снимка (`find_waypoint_for_photo`). Нет снимков с парой или пар несколько — откат на А (`by_sol`).
- Хранить или считать: стоянки меняются раз в сутки, снимки сола могут доехать позже — хранение требует пересвязки после каждой синхронизации. Считать при чтении: 4 запроса на весь список (образцы; `DISTINCT sol, site, drive` по `photos` для ~28 солов — ~7,9 тыс. строк по индексу `(rover_id, sol, taken_at)`; стоянки — одним `LATERAL`; `total_photos` — `sol_summaries`), оценка — 10–20 мс, ответ кэшируется на час.
- **Рекомендация: Б, при чтении.** Доля `exact`/`nearest_before`/`by_sol` на проде — `data-engineer`.

**ADR-6. Ручки.** Путь `/rovers/{rover}/samples` — как `/flights`; у чужих марсоходов `404 not_found`. Фильтров нет: 30 строк, фильтрует клиент. `location` и `total_photos` — в каждом элементе списка: карта ставит маркеры одним запросом. Карточка `/samples/{n}` — тот же объект (для глубоких ссылок).

**ADR-7. Образец в карточке снимка.** А — не добавлять (фронтенд ищет по загруженному списку); Б — `sample: int | null` (как `flight`; два образца в один сол не исключены); В — `samples: [int]`. **Рекомендация: В** — запрос по `(rover_id, sol)` к 30 строкам, только для Perseverance; ссылка «Sample #N sealed this sol» без второго запроса.

**Авторство.** Страница — материалы NASA; берём только факты таблицы (без фото и описаний), в ответе `source_url`, в интерфейсе — «Source: NASA Mars Rock Samples» со ссылкой и датой `source_updated_at`. Никаких логотипов и формулировок об одобрении. Условия использования текста [не проверены] — вопрос В3.

## 5. Модель данных, миграция, поток

**0010 `20261122_0010_rover_samples.py`** (`down_revision "0009"`):
- `rover_samples`: `rover_id text` FK `rovers.id`, `number smallint` (`CHECK number > 0`), PK `(rover_id, number)`; `name text NOT NULL`, `feature_name text`, `sampling_location text`, `current_location text`, `status text` (`CHECK IN ('on_rover','depot')`), `sample_type text` (`CHECK IN ('rock_core','regolith','atmospheric')`), `sample_type_label text`, `rock_type text`, `sealed_on date`, `sol integer` (`CHECK sol >= 0`), `height_cm numeric(5,2)`, `height_note text`, `dossier_url text`. Индексов сверх PK нет (30 строк).
- `sample_datasets`: `rover_id text` PK FK, `created_at`, `checked_at`, `succeeded_at`, `changed_at`, `source_modified_at` (все `timestamptz`), `etag text`, `last_modified text`, `content_sha256 text`, `items integer`, `last_error text`.
- `downgrade`: `DROP TABLE rover_samples, sample_datasets` — данные восстанавливаются одним запуском `samples`.

**Поток:** страница → `app/sources/samples.py` (`SamplesAdapter.fetch` через `UpstreamClient.get_conditional`, `max_bytes=SAMPLES_MAX_BYTES`, `allowed_origin` = origin `SAMPLES_URL`; `parse_samples(body)`) → `app/services/sample_sync.py` (срок, приёмка ADR-3, sha256, замена под блокировкой, запись состояния) → `rover_samples` → `app/services/samples.py` (связи ADR-5) → `app/api/v2/rovers.py`. Частота: `SyncScheduler.run_once` после положения вызывает `SampleSyncService.refresh_due()` — не чаще `SAMPLES_REFRESH_HOURS` от `succeeded_at` (срок в БД); 1 GET в сутки, ретраи и лимит частоты — общего клиента. Сбой не влияет на снимки и положение, повтор — в следующем цикле. Идемпотентно: тот же разбор → `unchanged`, ничего не пишется, кроме `checked_at`/`succeeded_at`.

**Настройки:** `SAMPLES_SYNC_ENABLED` (true), `SAMPLES_URL` (адрес страницы), `SAMPLES_REFRESH_HOURS` (24), `SAMPLES_MAX_BYTES` (4 МиБ), `SAMPLES_STALE_DAYS` (3).

## 6. Задания ролям и общие имена

**Общие имена** (`backend-dev` реализует, `qa-engineer` тестирует параллельно):
- `app/sources/samples.py`: `SampleRecord` (dataclass: поля `rover_samples` без `rover_id`), `ParsedSamples(items, source_modified_at)`, `SamplesFormatError(UpstreamError)`, `parse_samples(body: bytes) -> ParsedSamples`, `SamplesAdapter.fetch(*, etag=None, last_modified=None) -> ConditionalBody`, `SAMPLES_ROVER = "perseverance"`.
- `app/services/sample_sync.py`: `SampleSyncService(engine, client, settings)`, `refresh_due(now=None) -> SampleSyncResult`, `refresh(*, force=False)`; статусы `skipped|unchanged|changed|rejected|failed`; `SAMPLES_LOCK_NAMESPACE = 7_250_003`.
- `app/db/models.py`: `RoverSample`, `SampleDataset`. `app/repositories/samples.py`: `list_samples`, `get_sample`, `replace_samples`, `get_dataset`, `save_dataset`, `sample_numbers_by_sol(session, rover_id, sol) -> list[int]`, `sol_positions(session, rover_id, sols)`, `waypoints_for_samples(...)`.
- `app/services/samples.py`: `list_samples(session, slug)`, `get_sample(session, slug, number)`, `SampleLocation(waypoint, match)`. `app/services/v2.py`: `PhotoDetail.samples: list[int]`.
- `app/core/errors.py`: `SampleNotFound` (`404 sample_not_found`). `app/api/v2/schemas.py`: `SampleOut`, `SampleLocationOut`, `SamplesResponse`, `PhotoDetailOut.samples` (последним). `app/api/caching.py`: `samples` — в сегменты с `LOCATION_CACHE_CONTROL`.
- `app/services/alerts.py`: `AlertMonitor._check_samples`, `samples_health(...)`. `app/services/scheduler.py`: `_refresh_samples()`. `app/cli.py`: `samples [--force]`, код 0 — `changed`/`unchanged`, 1 — `rejected`/`failed`.

**`backend-dev`:** всё выше + миграция 0010, настройки и `.env.example`; JSON-числа `height_cm` — числом; `source_modified_at` → UTC.

**`qa-engineer`** (фикстуры из `data/samples/rock-samples.html` без сети, урезанные: `<head>` с `article:modified_time` + `thead` + строки № 1, 8, 17, 25, 26, 29, 30 с сохранением `&nbsp;`/`<br>`; варианты: заголовок переименован, таблицы нет, две таблицы, дубль номера, `Sol Sealed` = `abc`, дата `Foo 1, 2021`, пропал № 8, незнакомый `Current Location`, ссылка `javascript:`):
- unit `parse_samples` — контрольные значения: № 1 → `sealed_on 2021-08-06`, `sol 164`, `status depot`, `sample_type atmospheric`, `rock_type null`, `height_cm null`, `height_note null`, `dossier_url …volume1.pdf#page=16` (PDS, не зеркало); № 8 → `name` `Ha'ahóni (aka "Hahonih")`, `sampling_location` с `Ch’ał`, `sealed_on 2022-03-07`; № 17 → `regolith`, `Likely mixed sedimentary and igneous grains`; № 25 → `height_cm 6.2`; № 26 → `dossier_url null`, `2.91`; № 29 → `sol null`, `sealed_on null`, `height_cm null`, `height_note "Est. 5-7 cm (2-2.8 in)"`; № 30 → `height_cm 1.91`, `height_note null`; `source_modified_at` = `2026-06-20T16:45:46Z`; все варианты отказа ADR-3; на полной странице (если qa решит держать её целиком в фикстурах — не обязательно) — 30 строк, depot 9, `rock_core` 27.
- integration: первая загрузка (respx) → 7 строк урезанной фикстуры, `changed`; повтор → `unchanged`; изменённая строка → `changed`; отказ → данные прежние, `last_error`; `--force` пропускает п. 4; срок `SAMPLES_REFRESH_HOURS`; `SAMPLES_SYNC_ENABLED=false`; планировщик: сбой образцов не ломает снимки; алерт через `SAMPLES_STALE_DAYS` и снятие; миграция 0010 вверх-вниз.
- api: список (порядок, конверт, пустая БД → `items: []`, `null`), карточка `1`/`30`/`0`/`31`/`-1`/`abc`/`99999999`; `curiosity` → `404 not_found`; `xyz` → `rover_not_found`; `PERSEVERANCE`; `location.match` — все три ветки и `null` (на подготовленных стоянках и снимках); `total_photos` — сол со снимками/без/`null`; PhotoDetail `samples` — сол с образцом, без, другой марсоход `[]`; `Cache-Control` 3600, ETag/304; v1 без изменений.

**`data-engineer`:** на проде после загрузки — сверка 30 строк со страницей; для 28 солов: есть ли стоянки, доля `exact`/`nearest_before`/`by_sol`, расстояние между вариантами А и Б ADR-5; время ответа списка.

**`designer` (сначала):** макеты — страница «Samples» (таблица 30 строк: №, имя, сол → снимки сола, дата, объект/место, статус «On rover»/«Depot», тип, порода, высота, PDF; плашка «Source: NASA Mars Rock Samples, updated …» и пометка об отставании; `TBD`); маркеры образцов на карте маршрута (у стоянки, попап с номером, именем, ссылками; статус «on rover/depot» — в попапе); строка в карточке снимка «Sample #N sealed this sol».
**`frontend-dev`** (после макета): страница по `/samples`, вкладка «Samples» у Perseverance; маркеры на карте по `location` (без `location` — не рисуются, в таблице — «—»); ссылка из карточки снимка по `samples`; «Photos of the tube» → `/photos?sol=S&camera=CACHECAM`.
**`devops`:** после выкатки `alembic upgrade head`, затем `uv run python -m app.cli samples` на ноутбуке; новые переменные — по умолчанию.

## 7. Вопросы владельцу

**В1. Утвердить раздел 3** (`/samples`, `/samples/{number}`, `sample_not_found`, `samples` в PhotoDetail)? Только добавления в v2, v1 не меняется. *Рекомендую: да.*

**В2. Регулярный запрос к science.nasa.gov:** 1 GET (~495 КБ) раз в сутки + ручной запуск CLI; валидаторов у страницы нет, поэтому каждый раз полная загрузка. *Рекомендую: да, раз в сутки (`SAMPLES_REFRESH_HOURS`, можно поднять до недели).*

**В3. Текст страницы NASA** (имена, места, тип породы) отдаём с `source_url` и подписью «Source: NASA» в интерфейсе, без фото; условия использования не проверялись. *Рекомендую: да — это фактические данные NASA, авторство указываем.*

**В4. Образцы после № 30 и witness-трубки** — в источнике их нет; вручную не дополняем, ждём страницу. *Рекомендую: да, не дополнять.*

**В5. Сид в миграции не делаем** (только загрузка со страницы; при смене разметки старые данные остаются, приходит алерт). *Рекомендую: да.*
