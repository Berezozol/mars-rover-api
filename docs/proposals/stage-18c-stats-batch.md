# Этап 18C: сводки и выборка по списку id — черновик архитектора

Редакция 1 (2026-11-21, HEAD `663175a`, миграции до `0015`). Основа: `docs/PLAN.md` (этап 18, строка 18C и «Порядок»), `docs/API.md` («Общие правила v2», Photo, `GET /api/v2/photos`, манифест, «Ограничение частоты» с «Перегрузкой», «Кэширование»), `docs/proposals/stage-18b-filters.md`, `docs/proposals/overload-protection.md`; код `app/db/models.py` (`SolSummary`, `CameraSolCount`), `app/repositories/photos.py` (`camera_total_photos`, `sol_range_total_photos`, `list_sol_summaries`, `get_photo`), `app/services/v2.py` (`classify_total`, `_heavy_gate`, `parse_rovers`, `_camera_filters`), `app/core/overload.py`, `app/api/caching.py` (`cache_control_for`), `app/main.py` (CORS). Замеры — dev-база `mars` (только чтение). Сеть не использовалась.

## 0. Цель, границы, критерий

**Входит:**
- `GET /api/v2/photos/stats` — число снимков, солов и границы периода по марсоходам, камерам, годам или месяцам с фильтрами по марсоходам, камерам, диапазону солов или дат; считается только по сводкам (`sol_summaries`, `camera_sol_counts`), в `photos` не ходит.
- `GET /api/v2/photos/batch?ids=…` — до 100 снимков (объект Photo, как в списках) по списку id одним запросом, в порядке запроса, с перечнем ненайденных id.
- Исключение обоих путей из правила «`/photos/{id}` кэшируется на час», тексты для `/developers` и `/llms.txt`, `openapi.json`.

**Не входит:** панорамы (позже, по `PLAN.md`); `POST`-форма batch (см. ADR-1); статистика по `sample_type`, отличному от `full`, по местному времени, размеру кадра, стоянкам и `near` (в сводках этого нет — пришлось бы считать по `photos`); группировка по солам (это уже даёт `/rovers/{rover}/manifest`); новые таблицы и миграции; v1; фронтенд Sol Index.

**Готово, когда:** обе ручки отвечают по тексту раздела 4, все AC раздела 2 зелёные, ни одна из ручек не берёт место тяжёлого поиска и не читает таблицу `photos` сверх поиска по первичному ключу.

## 1. Факты

- Сводки ведёт синхронизация вместе со снимками: `sol_summaries (rover_id, sol, earth_date, total_photos, cameras[], full_photos, full_cameras[])` и `camera_sol_counts (rover_id, sol, camera_id, total_photos, full_photos)`, строк с нулём нет (`app/db/models.py:201-222`, `recompute_sol_summaries` в `app/repositories/photos.py:554`). На них уже стоят `total` у лёгких поисков (`_answered_by_summaries`, `_summary_total` в `app/services/v2.py:778,830`) и манифест.
- `sol_summaries.earth_date` — самая ранняя дата съёмки сола (`API.md`, манифест, `nearest_sols`). Дата отдельного снимка в сводках не хранится — фильтр по датам в сводках возможен только «по дате сола».
- Объём dev-базы: `photos` 1 728 301, `camera_sol_counts` 69 361, `sol_summaries` 13 781, `cameras` 58; `max(id)` 1 735 464.
- Худший случай stats — все марсоходы, все камеры, группировка по (марсоход, камера): `HashAggregate` по `camera_sol_counts ⋈ cameras`, 22,6 мс, 506 буферов (~4 МБ). Группировка по месяцу добавит соединение с `sol_summaries` по первичному ключу (13,8 тыс. строк) — порядок десятков мс; холодный кэш — несколько МБ чтения. Тяжёлый вход не нужен. `[не проверено]` холодное время на продакшене — проверка после реализации (раздел 7, `data-engineer`).
- batch: `photos.id = ANY(array)` идёт по `pk_photos` (Index Scan, 7 id — 1,9 мс, из них 6 страниц с диска). 100 id в худшем случае — ~100–200 случайных чтений страниц, десятки мс.
- `cache_control_for` (`app/api/caching.py:56`) считает любой путь `/photos/<x>` карточкой (`max-age=3600`) — `/photos/stats` и `/photos/batch` попадут туда, если не добавить исключение.
- CORS открыт только для `GET` (`app/main.py:158`, `API.md` «Кэширование»); `ETag`/`304` и `HEAD` реализованы для `GET`.
- Лимиты частоты и одновременных запросов действуют на все пути `/api/v2/*` — на новые ручки тоже, без правок.

## 2. Критерии приёмки

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | `GET /api/v2/photos/stats` без параметров → 200, `group_by: "rover"`, в `groups` по одному элементу на марсоход со снимками; `total_photos` верхнего уровня = сумма по группам = сумма `total_photos` из `/api/v2/rovers` | qa-engineer | api |
| AC-2 | `?rovers=perseverance&camera=NAVCAM_LEFT&sol_min=100&sol_max=200` → `total_photos` = `total` у `/api/v2/photos` с теми же параметрами; `sols_with_photos` = число солов с этой камерой в диапазоне | qa-engineer | api |
| AC-3 | то же с `sample_type=full` → `total_photos` = `total` у `/api/v2/photos?...&sample_type=full` | qa-engineer | api |
| AC-4 | `group_by=camera` при `rovers=curiosity,perseverance` → группы `(rover, camera)`, `period: null`, отсортированы по `rover`, затем по коду камеры (как в `/rovers/{rover}/cameras`, решение 2026-11-21 по контракту); у камер без снимков в выборке групп нет | qa-engineer | api |
| AC-5 | `group_by=month` → `period` вида `YYYY-MM` по `earth_date` сола, `camera: null`; `group_by=year` → `period` вида `YYYY`; порядок — `rover`, затем `period` по возрастанию | qa-engineer | api |
| AC-6 | `date_min=2024-01-01&date_max=2024-01-31` → учитываются солы, у которых `earth_date` манифеста в этом диапазоне (включительно); сол, начавшийся 2023-12-31 и захвативший 2024-01-01, не учитывается | qa-engineer | integration |
| AC-7 | у группы `first_sol`/`last_sol`/`first_date`/`last_date` — минимум и максимум по солам выборки; пустая выборка (`sol_min` больше `max_sol`) → 200, `total_photos: 0`, `sols_with_photos: 0`, даты `null`, `groups: []` | qa-engineer | api |
| AC-8 | ошибки → `422 validation_error`: неизвестный slug в `rovers`; код камеры, которого нет ни у одного выбранного марсохода (в `details` — допустимые коды); `group_by=sol` и любое иное значение; `sample_type=subframe`; `sol_min` вместе с `date_min`; `min > max`; число вне границ | qa-engineer | api |
| AC-9 | «всё и холодно»: место тяжёлых поисков занято (ворота заполнены) → `GET /photos/stats?group_by=month` всё равно 200; в журнале SQL запроса нет таблицы `photos` | qa-engineer | integration |
| AC-10 | `GET /api/v2/photos/batch?ids=3,1,2` → 200, `items` — три Photo в порядке `3, 1, 2`, формат элемента совпадает с элементом списка `/api/v2/photos` (в том числе `images.full` MER — абсолютная ссылка на текущий хост), `missing: []` | qa-engineer | api |
| AC-11 | id разных марсоходов в одном запросе (Curiosity, Perseverance, Spirit, Ingenuity) → все в `items`, у каждого свой `rover` и `credit` | qa-engineer | api |
| AC-12 | повторы `ids=5,7,5,5` → `items` — снимки 5 и 7 по одному разу в порядке первого появления | qa-engineer | api |
| AC-13 | неизвестные и вне диапазона id: `ids=1,999999999,0,9223372036854775808` → 200, `items` — снимок 1, `missing: [999999999, 0, 9223372036854775808]` в порядке запроса (как числа); все id неизвестны → 200, `items: []` | qa-engineer | api |
| AC-14 | ограничения → `422 validation_error`: нет `ids` или пусто; пустой элемент (`1,,2`); не цифры (`1,a`, `-1`, `1.5`, пробел); элемент длиннее 19 цифр; больше 100 элементов (считая повторы); строка `ids` длиннее 2000 символов; повторённый параметр `ids=1&ids=2` | qa-engineer | api |
| AC-15 | batch на 100 разных id делает один запрос к `photos` (по `id = ANY`) плюс подгрузку камер — без запросов на каждый id; ворота тяжёлых поисков заполнены → всё равно 200 | qa-engineer | integration |
| AC-16 | `GET /photos/stats` и `GET /photos/batch` → `Cache-Control: public, max-age=300` (не `3600`), есть `ETag`, повтор с `If-None-Match` → `304`; `HEAD` → те же заголовки без тела; ошибки — `no-store` | qa-engineer | api |
| AC-17 | `POST /api/v2/photos/batch` → `405` (формы `POST` нет); `GET /api/v2/photos/stats` и `/batch` не перехватываются маршрутом `/photos/{id}` (не `404 photo_not_found`) | qa-engineer | api |
| AC-18 | запрос к каждой из ручек → счётчик лимита частоты уменьшается на 1, в ответе есть `X-RateLimit-*` | qa-engineer | api |
| AC-19 | `app.cli openapi` после этапа → `docs/openapi.json` содержит обе ручки с параметрами и границами; `GET /developers` и `GET /llms.txt` → упоминают обе ручки | reviewer | — |
| AC-20 | полный объём dev-базы, холодный кэш Postgres: stats худшего вида (все марсоходы, `group_by=month`, список из 10 камер) и batch из 100 случайных id → каждый меньше 200 мс, `EXPLAIN` без `Seq Scan` по `photos` | data-engineer | замер |

## 3. Решения

1. Stats считается только по сводкам — значит, не тяжёлый, без ворот; всё, чего в сводках нет, — `422`, а не тихий проход в `photos` (так дыра перегрузки не открывается в принципе).
2. Даты в stats — даты солов (как в манифесте), не даты отдельных снимков; разница описана в контракте.
3. Batch — `GET` с `ids=` (ADR-1), не больше 100 id (как `per_page`), один запрос по первичному ключу.
4. Ненайденные id не дают `404` — они в `missing`; формат элемента — Photo из списков, без `PhotoDetail` (у карточки `in_sol` и прочее стоит отдельных запросов на каждый снимок).
5. Новые таблицы и индексы не нужны.

## 4. Текст для `API.md` (если меняется контракт)

Вставить после раздела `GET /api/v2/photos` (этап 18B). Правка аддитивная, клиентов не ломает. Дополнительно — строки в «Кэширование» (ниже).

> ### `GET /api/v2/photos/stats` (этап 18C)
> Сводка: сколько снимков и солов, первый и последний сол и дата — по марсоходам, камерам, годам или месяцам. Считается по сводкам синхронизации (как манифест), поэтому быстрый и не относится к тяжёлым поискам. Параметры (все необязательные):
> - `rovers` — как у `GET /api/v2/photos`; без параметра — все марсоходы;
> - `camera` — один или несколько кодов через запятую (до 10), правила — как у `GET /api/v2/photos`;
> - `sol_min`/`sol_max` — диапазон солов, включительно, любой край можно опустить; солы у каждого марсохода свои (у `ingenuity` — солы Perseverance);
> - `date_min`/`date_max` (`YYYY-MM-DD`, включительно) — по **дате сола** (`earth_date` в манифесте — самая ранняя дата съёмки сола), а не по дате каждого снимка, как у `/photos`: снимки сола, перешедшего через полночь UTC, считаются в дату его начала. Солы и даты в одном запросе не сочетаются; `min > max` → `422`;
> - `sample_type` — только `full` (полные кадры); без параметра — все типы; другое значение → `422`;
> - `group_by` — `rover` (по умолчанию), `camera` (марсоход + камера), `year`, `month` (марсоход + год или месяц по дате сола, UTC); иное → `422`. По солам — `GET /api/v2/rovers/{rover}/manifest`.
>
> Другие фильтры `/photos` (местное время, размер кадра, стоянки, `near`, `sample_type` кроме `full`) здесь не поддерживаются и игнорируются как неизвестные параметры.
>
> ```json
> {"group_by": "camera",
>  "total_photos": 48209, "sols_with_photos": 101,
>  "first_date": "2021-06-01", "last_date": "2021-09-14",
>  "groups": [
>    {"rover": "perseverance", "camera": "NAVCAM_LEFT", "period": null,
>     "total_photos": 12345, "sols_with_photos": 98,
>     "first_sol": 100, "last_sol": 200, "first_date": "2021-06-01", "last_date": "2021-09-14"}
>  ]}
> ```
> - `total_photos`, `sols_with_photos` верхнего уровня — по всей выборке (солы разных марсоходов считаются отдельно); `first_date`/`last_date` — по всей выборке. `first_sol`/`last_sol` есть только у групп: солы разных марсоходов несравнимы.
> - `camera` — код камеры при `group_by=camera`, иначе `null`; `period` — `"YYYY"` или `"YYYY-MM"` при `group_by=year|month`, иначе `null`.
> - Порядок групп: по марсоходу (как в `/api/v2/rovers`), затем по камере (как в `/rovers/{rover}/cameras`) или по `period` по возрастанию. Группы без снимков не выводятся; пустая выборка — `total_photos: 0`, `sols_with_photos: 0`, даты `null`, `groups: []`.
> - Числа совпадают с `total` у `GET /api/v2/photos` с теми же `rovers`, `camera`, `sol_min`/`sol_max`, `sample_type` (по датам — нет, см. выше). Сводки обновляются вместе с синхронизацией.
>
> `200` · `422 validation_error`
>
> ### `GET /api/v2/photos/batch?ids=…` (этап 18C)
> Снимки по списку id одним запросом — например, для избранного или ссылок.
> - `ids` — обязательный: id через запятую без пробелов, от 1 до 100 элементов (повторы считаются), строка не длиннее 2000 символов, каждый элемент — от 1 до 19 цифр. Пусто, пустой элемент, не цифры, больше 100 элементов, параметр повторён → `422`. Повторы схлопываются.
> - Ответ — `{"items": [Photo, ...], "missing": [id, ...]}`: `items` — найденные снимки (объект Photo, как в списках) в порядке первого появления id в запросе; `missing` — ненайденные id в том же порядке, включая id вне диапазона (`0`, больше 2⁶³−1). Ненайденные id не дают `404`.
> - Снимки разных марсоходов в одном ответе допустимы.
>
> ```json
> {"items": [{"id": 81234, "rover": "perseverance", "sol": 2031, "...": "..."}],
>  "missing": [999999999]}
> ```
> `200` · `422 validation_error`
>
> **Кэширование** (дополнение): `/photos/stats` и `/photos/batch` — `public, max-age=300`, как списки.

## 5. ADR

**ADR-1. Batch: `GET ?ids=` или `POST` с телом.**
- `POST /photos/batch` (как в `PLAN.md`). Плюсы: длина списка не упирается в URL. Минусы: CORS сейчас только `GET` — браузерным клиентам понадобится `POST` и предварительный запрос `OPTIONS`; нет `ETag`/`304`, `HEAD`, кэша CDN и браузера — правило «Кэширование» на `POST` не распространяется; новая ветка в прослойках кэша и лимитов; тело надо ограничивать по размеру отдельно.
- `GET /photos/batch?ids=` . Плюсы: работают все существующие механизмы (`ETag`, `304`, `HEAD`, `Cache-Control`, CORS, лимит частоты) без правок; ссылку можно скопировать. Минусы: 100 id по 19 цифр — до ~2000 символов в URL; это в пределах любых прокси (обычно 8 КБ) и Tailscale Funnel.
- **Рекомендация:** `GET`, путь `/photos/batch` оставить. 100 id хватает (это и `per_page`); `POST` без нужды усложнил бы публичную поверхность. Это отступление от формулировки `PLAN.md` — вопрос владельцу 1.

**ADR-2. Stats: только сводки или полный набор фильтров через ворота.**
- Только сводки: худший запрос ~25–50 мс, ворота не нужны, перегрузка не открывается. Ограничение: `sample_type` — только `full`; даты — по дате сола.
- Полный набор (`sample_type` любой, местное время, кадр, стоянки, даты снимков): счёт по `photos` с `GROUP BY`, тяжёлый, через ворота (`HEAVY_QUERY_CONCURRENCY=2`); при `group_by=month` по всем марсоходам — полный просмотр 1,7 млн строк, близко к лимиту 5 с; результат почти всегда `503` у второго клиента.
- **Рекомендация:** только сводки; если понадобится — добавить колонки в сводки отдельной задачей, а не считать по `photos`. Вопрос владельцу 2.

**ADR-3. Неизвестные id в batch: `missing` или `404`.**
- `404`, если хотя бы один не найден — клиент теряет всё из-за одного удалённого снимка. Молча пропускать — клиент не отличит «нет» от ошибки.
- **Рекомендация:** `200` + `missing` (порядок запроса). Id вне диапазона — тоже в `missing`, как `/photos/{id}` отвечает на них `404`, а не `422`.

**ADR-4. Группировка по солам в stats.**
- Есть: до 13,8 тыс. групп на все марсоходы — нужна пагинация групп.
- Нет: это уже `manifest` (с `from_sol`/`to_sol`).
- **Рекомендация:** нет; `year`/`month` дают периоды крупнее (≈ 400 групп на всё).

## 6. Модель данных, миграция, поток

Миграции нет. Чтение:
- stats: `camera_sol_counts` (если задана камера или `group_by=camera`) или `sol_summaries`; для `year`/`month` и дат — соединение с `sol_summaries` по `(rover_id, sol)` (первичный ключ). Колонка счёта — `full_photos` при `sample_type=full`, иначе `total_photos`; при `full` солы с `full_photos = 0` не считаются в `sols_with_photos`. Агрегат — один SQL-запрос `GROUP BY` (без цикла по марсоходам), диапазон солов — по первичному ключу сводок.
- batch: `select(Photo).options(joinedload(Photo.camera)).where(Photo.id.in_(ids))` — `pk_photos`; порядок и `missing` собирает сервис в Python.
- Синхронизация не меняется: сводки уже пересчитываются в той же транзакции, что и снимки.
- Сбой: таймаут или нет соединения — общий `503 service_busy`/`db_unavailable`, как у всех ручек.

## 7. Задания ролям и общие имена

**Общие имена:**
- роутер `app/api/v2/photos.py`: `stats_photos` (`GET /photos/stats`), `batch_photos` (`GET /photos/batch`) — объявить **до** `/photos/{photo_id}`;
- схемы `app/api/v2/schemas.py`: `PhotoStatsGroupOut`, `PhotoStatsResponse`, `PhotoBatchResponse` (`items: list[PhotoOut]`, `missing: list[int]`);
- сервис `app/services/v2.py`: `photo_stats(session, query: StatsQuery) -> PhotoStats`, `photos_by_ids(session, raw_ids: str) -> tuple[list[Photo], list[int]]`, `parse_ids(value: str) -> list[int]` (с повторами схлопнутыми, порядок сохранён), константы `BATCH_MAX_IDS = 100`, `BATCH_IDS_MAX_LEN = 2000`, `STATS_GROUP_BY = ("rover", "camera", "year", "month")`;
- репозиторий `app/repositories/photos.py`: `summary_stats(session, rover_ids, camera_ids | None, sol_min, sol_max, date_min, date_max, full_only, group_by) -> list[StatsRow]`, `get_photos_by_ids(session, ids) -> list[Photo]`;
- `app/api/caching.py`: исключение `stats`, `batch` в `cache_control_for` (→ `LIST_CACHE_CONTROL`).

- `backend-dev`: всё выше; разбор `rovers`/`camera` — переиспользовать `parse_rovers` и `_camera_filters` (те же сообщения `422`); `_check_order` для диапазонов; без обращений к `photos` в stats; повтор параметра `ids` → `422` (проверка по `request.query_params.getlist`); тексты `/developers` и `/llms.txt` от `tech-writer`; `uv run python -m app.cli openapi --output docs/openapi.json`.
- `qa-engineer`: AC-1…AC-18 по номерам, таблица «AC → тест»; фикстуры — существующие снимки в `migrated_db`, сводки через `recompute_sol_summaries`; AC-9 и AC-15 — заполнить ворота (`get_heavy_query_gate` с `in_use = limit`) и считать SQL (событие `before_cursor_execute`); AC-6 — сол с `earth_date` на границе месяца; AC-13 — id `9223372036854775808` и `0`.
- `data-engineer`: AC-20 после реализации (`/perf-check`: `EXPLAIN (ANALYZE, BUFFERS)` двух худших запросов, холодный кэш — после перезапуска контейнера базы), до 0,5% недельного лимита. Внешних запросов нет.
- `tech-writer`: абзацы для `/developers` и `/llms.txt` (ручки, лимит 100 id, «даты — по солу»); пример в `examples/` — по желанию владельца (вопрос 3).
- `frontend-dev` / `designer`: не участвуют (рекомендация: страница статистики Sol Index — отдельной задачей после этапа, если понадобится).
- `devops`: ничего; выкладка без миграций.
- `security-reviewer`: да — новые публичные ручки (длина `ids`, стоимость, кэш).

## 8. Проверка и бюджет

- Команды: `uv run ruff check .`, `uv run pytest -q -n 12`, `uv run python -m app.cli openapi --output docs/openapi.json`, `uv run --no-project python .claude/tools/ac_trace.py`.
- Живая проверка: `/sync-check` не нужен (синхронизация не меняется); `/perf-check` — AC-20.
- Ревью: `reviewer`; `security-reviewer` (публичная поверхность).
- Ожидаемый расход токенов: этот черновик — ~1% недельного лимита; `data-engineer` (AC-20) — до 0,5%; `backend-dev` + `qa-engineer` — порядок этапа 18B-2 (небольшой: две ручки без миграции).

## 9. Внешние запросы (для «ок» владельца)

Нет.

## 10. Вопросы владельцу

1. **Batch — `GET /api/v2/photos/batch?ids=1,2,3` вместо `POST` из `PLAN.md`?** Рекомендация: да, `GET` (ADR-1): кэш, `ETag`, `HEAD` и CORS работают без правок; потолок 100 id.
2. **Stats — только по сводкам (`sample_type` только `full`, даты — по дате сола)?** Рекомендация: да (ADR-2) — иначе stats становится тяжёлым поиском и снова открывает перегрузку.
3. **Пример в `examples/` и упоминание в README?** Рекомендация: только `/developers` и `/llms.txt`; README — строка в списке ручек, без отдельного примера.
4. **Текст раздела 4 в `API.md` — «ок»?** Рекомендация: принять как есть.
