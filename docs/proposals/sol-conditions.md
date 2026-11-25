# Этап 22: диапазон солов, таблица солов, отчёт по солу — черновик архитектора

Редакция 1 (2026-11-25, HEAD `a739d8b`, миграции до `0019`). Основа: решения владельца 2026-11-25 (в задании тимлида); `design/NOTES.md`, раздел «Диапазон солов, таблица солов, отчёт по солу», и макеты `design/mockups/{weather-range,radiation-range,sol-report-curiosity,sol-report-perseverance}.html`; `docs/API.md` (WeatherSol, `/weather`, `/weather/{sol}`, RadiationSol, `/radiation`, Waypoint); `docs/proposals/stage-17-weather.md` § 6, `docs/proposals/stage-21-rad.md` § 6, `docs/reports/stage-21-rad-phase-a.md`; код `app/services/weather_sync.py`, `app/services/rad_sync.py` (`_plan`), `app/sources/rad.py`, `app/services/{weather,rad}_transfer.py`, `deploy/vps/rad-sync.sh`, `frontend/src/lib/weather.ts`, `frontend/src/router.tsx`.

## 0. Цель, границы, критерий

Три фазы, каждая выкатывается отдельно, строго в порядке A → B → C (миграции 0020 и 0021 идут цепочкой).

**Входит:**
- **A (только фронтенд, на действующем API):** ползунок диапазона с двумя ручками, полями «From sol / To sol» и пресетами на графиках «Weather by sol» (Curiosity, Perseverance) и «Radiation» (Curiosity); таблица солов под каждым графиком (50 строк, сортировка, «Jump to sol»); исправление двух ошибок — пустой «Last 100 sols» (диапазон считается от `max_sol`, а не от `source_max_sol`) и серая зона «not published yet», вылезающая за карточку.
- **B:** страница «Sol report» `/rovers/{rover}/sols/{sol}/conditions` на действующих часовых средних; почасовые `min`/`max` погоды (контракт, миграция 0020, переразбор сырья на ПК, выгрузка и импорт на VPS).
- **C:** почасовая радиация Curiosity (контракт `GET /rovers/curiosity/radiation/{sol}`, миграция 0021, разбор LMST в загрузчике, повторная полная выкачка набора RAD на ПК, выгрузка и импорт, работа месячного таймера).

**Не входит:** серверная сортировка и пагинация таблицы солов (диапазон ≤ ~4,7 тыс. солов сортируется на клиенте); широта или полушарие в API (раздел 10, вопрос 4); почасовые значения в списках `/weather` и `/radiation`; эквивалентная доза; хранение наблюдений RAD или файлов RAD на диске; восход и закат в API (считаются на клиенте, пометка «optional»).

**Готово, когда:** на проде графики погоды и радиации управляются диапазоном с таблицей, отчёт по солу открывается для обоих марсоходов, `/weather/{sol}` отдаёт почасовые `min`/`max`, `/radiation/{sol}` — 24 часа радиации, и все AC-1…AC-55 закрыты тестами или проверками из раздела 8.

## 1. Факты

- Ошибка «Last 100 sols»: `frontend/src/lib/weather.ts:204-212` — `domainOf` берёт конец `max(now, last)`, где `now` — `max_sol` марсохода (`DataWeather.tsx:110`); у Curiosity `max_sol` ≫ `source_max_sol` (архив отстаёт на 6–8 месяцев), поэтому последние 100 солов пусты.
- Серая зона строится в `WeatherChart.tsx` и `DataRadiation.tsx:80-82` без обрезки по области графика.
- `hours[]` в `/weather/{sol}` — только средние (`API.md`, `/weather/{sol}`); хранение — `weather_hourly` (средние и `coverage`, 24 строки на сол). Сутки — `_Series` в `weather_sync.py:215-249`: суточные `min`/`max` — по сырым годным значениям, по часам копятся только суммы и счётчики. Ветер хранит все скорости (`percentile(…, 0.99)` — порыв).
- Переразбор управляется `WEATHER_PARSER_REVISION = 4` (`weather_sync.py:65`): повышение ревизии ставит в план все солы. Полный разбор Curiosity ≈ 4 мин (замер владельца); Perseverance — `[не проверено]`, ожидаем того же порядка (≈ 48 тыс. показаний воздуха на сол против ≈ 24 тыс., но солов втрое меньше).
- Импорт погоды отклоняет выгрузку с другой `schema_revision` (`weather_transfer.py:323`).
- Реальные значения Curiosity, сол 344: Ls 356,7°, воздух −77…−7 °C, давление 852 Па (данные владельца). Числа в макетах выдуманы, Ls в макете линейная и неверна (178°) — клиент берёт `ls` только из API.
- RAD: в БД только суточные сводки; файлы при загрузке не сохраняются (`stage-21-rad.md` § 6). `_plan` (`rad_sync.py:849-876`) отбирает солы только по md5/статусам: без нового признака повторная выкачка не начнётся, а с ним — нельзя дать таймеру на VPS перекачать 300 ГБ (ADR-4).
- Разрешение RAD по времени (`stage-21-rad-phase-a.md`): с ≈ 2013 года наблюдения почти непрерывные, ≈ 16 мин (`OBS_DURATION` 961 с, живое время 930 с), 88 дозовых наблюдений на сол (сол 4843); в 2012 году — раз в час по 960 с (26 на сол 10). Доза B и E — одно число мкГр/ч на наблюдение.
- Время наблюдения на Марсе: `START_OBS_MARS` вида `"4843 0:04:39"`, сол в нём у всех наблюдений равен `START_SOL` файла, время суток «заворачивается» после полуночи (последнее наблюдение файла 4843 — через 24 ч 28 мин после начала, помечено `0:04:39`). Отсюда: файл начинается около местной полуночи `[не проверено: по двум файлам, оценка по шагу]`. Парсер `app/sources/rad.py` сейчас `START_OBS_MARS` не читает (`RadObservation` без этого поля).
- `[не проверено]`: шкала времени в `START_OBS_MARS` — LMST или LTST (истинное солнечное). Задача `data-engineer` до разработки C (раздел 7), без запросов, по `data/rad_sample/`.
- `[не проверено]`: `START_OBS_MARS` бывает мусорным так же, как `START_OBS_UTC` (сол 10, наблюдение 12). Правило ADR-6 отбрасывает такие наблюдения из часов, не из суток.
- Широта: `Waypoint` в `/location` уже содержит `lat` (`API.md`, Waypoint) — клиент может взять её из `/location?latest=true`.

## 2. Критерии приёмки

Тесты называются по номерам; `qa-engineer` и `frontend-qa` сдают таблицу «AC → тест». Значения в фикстурах — из записанных фикстур `tests/fixtures/upstream/` и фикстурных ответов API во `frontend/tests/`.

### Фаза A — диапазон и таблица (фронтенд)

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | `defaultRange({first: 9, sourceMax: 4710})` → `[4042, 4710]` (669 солов до `source_max_sol`); `sourceMax − 668 < first` → начало = `first` | frontend-qa | компонентный (`lib/solRange`) |
| AC-2 | Пресеты при `max_sol` 5066 и `source_max_sol` 4710: «Last 120 sols» → `[4591, 4710]`, «Whole published data» → `[first, 4710]`; таблица и график не пусты (регресс ошибки «Last 100 sols») | frontend-qa | компонентный |
| AC-3 | Клавиатура на ручке `role=slider` → стрелки ±1, PageUp/PageDown ±10, Home/End; ручки не перекрещиваются; ширина не меньше 6 солов; `aria-valuemin/max/now` и `aria-valuetext` («sol 4591») обновляются | frontend-qa | компонентный |
| AC-4 | Поле «To sol» = «From sol» + 3, текст «abc», значение вне `[first, max(max_sol, source_max_sol)]` → `aria-invalid="true"`, текст ошибки, диапазон не меняется; верное значение применяется по Enter и при потере фокуса | frontend-qa | компонентный |
| AC-5 | Диапазон `[4591, 4710]` → ось X графика ровно `4591…4710`, точки вне диапазона не рисуются | frontend-qa | компонентный |
| AC-6 | Диапазон `[4600, 5066]` при `source_max_sol` 4710 → серая зона `4711…5066` внутри `clipPath` области графика, левый край зоны не левее левого поля графика; диапазон целиком в опубликованном → зоны нет | frontend-qa | компонентный |
| AC-7 | Таблица следует диапазону: 120 солов → 3 страницы по 50/50/20; клик по заголовку меняет `aria-sort` (`ascending`/`descending`), по умолчанию — сол по убыванию; смена диапазона возвращает на 1-ю страницу | frontend-qa | компонентный |
| AC-8 | «Jump to sol»: сол в диапазоне с данными → его страница, строка подсвечена и в фокусе; сол без данных → ближайший сол с пояснением; сол вне диапазона → пояснение и кнопка «Widen the range» (после нажатия сол в диапазоне); сол > `source_max_sol` → фраза «not published yet» | frontend-qa | компонентный |
| AC-9 | Клик по точке графика → таблица переходит к строке этого сола | frontend-qa | компонентный |
| AC-10 | Ширина 360 px → строки — карточки, сортировка через «Sort by» + направление, у страницы нет горизонтальной прокрутки | frontend-qa | e2e (Playwright) |
| AC-11 | Радиация (`/data/radiation`): тот же ползунок и таблица; шкала Y «Auto»: диапазон ≥ 400 солов → от нуля, иначе по данным; переключатели «From zero», «Fit to the range» работают | frontend-qa | компонентный |
| AC-12 | Диапазон в адресе `?from=4591&to=4710` восстанавливается при перезагрузке; неверные или неполные `from`/`to` → диапазон по умолчанию без ошибки | frontend-qa | компонентный |
| AC-13 | Строка таблицы содержит ссылку «Photos» → `/rovers/{rover}?sol={sol}` | frontend-qa | компонентный |
| AC-14 | Загрузка → скелет без ползунка; `503` — ошибка с «Retry»; `items: []` — «no data yet», ползунка нет | frontend-qa | компонентный |

### Фаза B — отчёт по солу и почасовые min/max погоды

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-15 | `GET /api/v2/rovers/perseverance/weather/{sol}` (база из фикстур MEDA) → у каждого часа есть `air_temp_c_min`, `air_temp_c_max`, `pressure_pa_min/max`, `humidity_pct_min/max`, `ground_temp_c_min/max`, `wind_gust_ms`; округление как у средних; для часа с данными `min ≤ среднее ≤ max` | qa-engineer | api |
| AC-16 | Час без измерений → все `_min`, `_max`, `wind_gust_ms` — `null`, `coverage` 0 | qa-engineer | api |
| AC-17 | `combine_sol` на фикстурах обоих марсоходов → суточный `min` параметра = наименьший из почасовых `_min`, суточный `max` = наибольший из почасовых `_max` (до округления — в `combine_sol`) | qa-engineer | unit |
| AC-18 | `/weather/{sol}` Curiosity → в часах есть `uv_wm2_min/max`, ключей ветра нет; Perseverance, сол вне 36–313 → `wind_gust_ms` `null` | qa-engineer | api |
| AC-19 | `WEATHER_M20_GROUND_ENABLED=false` → в часах Perseverance нет ни `ground_temp_c`, ни `ground_temp_c_min/max` | qa-engineer | api |
| AC-20 | `GET /weather` (список) на фикстурной базе → тело для фикстурной базы совпадает с эталоном до этапа | qa-engineer | api |
| AC-21 | Час с одним показанием → `_min = _max =` среднее; порыв часа — `percentile(скорости часа, 0.99)` | qa-engineer | unit |
| AC-22 | `weather parse` на базе со строками ревизии 4 → в плане все солы (ревизия 5); суточные столбцы не меняются; повтор → 0 солов | qa-engineer | integration |
| AC-23 | Миграция 0020: `upgrade` → `downgrade` → `upgrade` на временной базе; существующие строки получают `NULL`; `downgrade` удаляет только новые столбцы | qa-engineer | integration |
| AC-24 | `weather export` → версия формата 2, `schema_revision` `0020`, часы с `_min/_max`; `weather import` в пустую базу → `/weather/{sol}` побайтно как в исходной; файл с `schema_revision` `0018` → отказ, код 1, база не тронута | qa-engineer | integration |
| AC-25 | Маршрут `/rovers/curiosity/sols/344/conditions` → страница отчёта; `/rovers/opportunity/sols/1/conditions` → состояние «no weather data for this rover» со ссылкой на снимки; `/rovers/curiosity/sols/abc/conditions` → страница «not found» | frontend-qa | компонентный |
| AC-26 | Шапка: Ls из `ls` ответа (ответ с `ls` 356.7 → «Ls 356.7°»), сезон по полушарию (Curiosity, юг: «southern summer»), покрытие, флаги, восход/закат с пометкой «optional»; `ls: null` → «—», сезона нет | frontend-qa | компонентный |
| AC-27 | Отдельный график на параметр, ось X — LMST 0–24; час с `null` → разрыв линии и штриховая колонка, без интерполяции | frontend-qa | компонентный |
| AC-28 | Часы с `_min/_max` → полоса min–max по часам; ответ без этих ключей (до переразбора) → полосы нет, ошибки нет | frontend-qa | компонентный |
| AC-29 | Наведение, касание или ползунок «Inspect hour» → курсор на этом часе во всех графиках; значения часа объявляются `aria-live` | frontend-qa | компонентный |
| AC-30 | «← Sol N−1» / «Sol N+1 →» ведут на соседний номер; сол 0 — кнопки назад нет; `404 sol_not_found` → состояние «No data for sol N» с теми же кнопками | frontend-qa | компонентный |
| AC-31 | «Photos of this sol →» → `/rovers/{rover}?sol={sol}`; строки таблиц фазы A получают ссылку «Sol report» на отчёт | frontend-qa | компонентный |
| AC-32 | Отчёт Perseverance → есть график ветра (средняя скорость, порыв, направление часа), блока радиации нет; отчёт Curiosity → есть УФ и блок радиации — суточная сводка из `/radiation?sol_min=N&sol_max=N`, почасовой части нет до фазы C | frontend-qa | компонентный |
| AC-33 | Ширина 360 px → одна колонка графиков, 760 px → две; тёмная тема, загрузка и `503` → читаемо, с «Retry» | frontend-qa | e2e + `/ui-check` |

### Фаза C — почасовая радиация Curiosity

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-34 | `RadParser` на фикстурах 4843 и сол 10 → у дозовых наблюдений `start_mars_sol` и `start_lmst_s` из `START_OBS_MARS` (`"4843 0:04:39"` → 4843, 279.0); значение без времени или неразборчивое → оба `None` | qa-engineer | unit |
| AC-35 | Час наблюдения — по середине: LMST начала + (длительность / 2) / 1,0274912517; начало 13:50:00, длительность 961 с → середина 13:57:47 → час 13; начало 13:55:00 → час 14 | qa-engineer | unit |
| AC-36 | Наблюдение, у которого LMST меньше, чем у предыдущего в файле (завёртка: последнее наблюдение 4843, `0:04:39`), и наблюдения с `start_lmst_s = None` → в часы не входят; в суточную сводку входят как раньше | qa-engineer | unit |
| AC-37 | `combine_hours` на фикстурах → в часах только учтённые суточной свёрткой наблюдения (QC ≠ 0 и низкая скорость отброшены); доза часа = Σ(доза × живое время) / Σ живое время; Σ `live_s` по часам + `live_s` отброшенных по AC-36 = `live_s` сола | qa-engineer | unit |
| AC-38 | `GET /api/v2/rovers/curiosity/radiation/4843` (фикстурная база) → `200`, поля RadiationSol совпадают с элементом списка, `hours` — 24 элемента; час без наблюдений → `coverage` 0, значения `null`, `observations` 0; доза до 0,01, скорость до 0,001, `coverage` до 0,01 | qa-engineer | api |
| AC-39 | `/radiation/5000` (нет сводки) → `404 sol_not_found`; `/rovers/perseverance/radiation/1` → `404 not_found`; `/rovers/foo/radiation/1` → `404 rover_not_found`; сол `-1`, `2147483648`, `abc` → `422 validation_error` | qa-engineer | api |
| AC-40 | Сол со сводкой, но без часов (`hourly_revision` 0) → `200`, `hours: null` | qa-engineer | api |
| AC-41 | `rad sync --reprocess --from-sol 4842 --to-sol 4843` на базе с `hourly_revision` 0 → запросы (respx): 2 перечня + 2 файла, без проб `Range` для `duplicate`; в `rad_hourly` строки, `hourly_revision` 1; столбцы `rad_daily` и `updated_at` не меняются; повтор → только 2 перечня | qa-engineer | integration |
| AC-42 | `rad sync` без `--reprocess` на базе, где md5 совпадают, а `hourly_revision` 0 → ни одного запроса файла (таймер на VPS не перекачивает историю) | qa-engineer | integration |
| AC-43 | Ctrl+C посреди `--reprocess` → обработанные солы с ревизией 1, сол в работе откатан; повтор продолжает с необработанных | qa-engineer | integration |
| AC-44 | Обычный `rad sync` нового сола → `rad_daily`, `rad_hourly` и `hourly_revision` 1 одной транзакцией; удаление сола из `rad_daily` удаляет его часы (`ON DELETE CASCADE`) | qa-engineer | integration |
| AC-45 | Миграция 0021: `upgrade` → `downgrade` → `upgrade`; `downgrade` удаляет `rad_hourly` и `rad_daily.hourly_revision`, суточные данные целы | qa-engineer | integration |
| AC-46 | `rad export` → версия 2, `schema_revision` `0021`, строки `{"t": "hourly"}`; `rad import` в пустую базу → `/radiation/{sol}` как в исходной; файл `0017` → отказ, код 1 | qa-engineer | integration |
| AC-47 | `/radiation/{sol}` → `Cache-Control: public, max-age=3600`; `docs/openapi.json` содержит путь и схемы `RadiationSolDetail`, `RadiationHour` | qa-engineer | api |
| AC-48 | Отчёт Curiosity: график мощности дозы E (сплошная) и B (пунктир), мкГр/ч, и график скорости счёта B по часам из `/radiation/{sol}`; `hours: null` или `404` → пояснение «hourly radiation not available for this sol» и суточная сводка | frontend-qa | компонентный |
| AC-49 | `timing_caveat: true` → предупреждение «observation times may be shifted» у блока радиации | frontend-qa | компонентный |

### Живые данные и производительность (все фазы)

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-50 | После переразбора на ПК: `/weather/344` Curiosity → `ls` 356.7, воздух −77…−7 °C, давление 852 Па (как в данных владельца); нарушений AC-17 по всей базе — 0 (SQL-проверка) | data-engineer | живая проверка |
| AC-51 | Переразбор с ревизией 5 → Curiosity ≤ 6 мин, Perseverance ≤ 10 мин на ПК; рост `weather_hourly` ≤ 20 МБ | data-engineer | живая проверка |
| AC-52 | Пробная выкачка C1–C4 (раздел 9) → сол 4843 — средняя по часам доза B ≈ 6,95 мкГр/ч (отчёт фазы A), 24 часа с данными; сол 10 — около 26 часов-наблюдений, `coverage` часа ≈ 0,26 | data-engineer | живая проверка |
| AC-53 | Полная выкачка C5 → часы есть у ≥ 99 % солов `rad_daily`; тождество AC-37 по всей базе; список солов без часов и причин — в отчёте | data-engineer | живая проверка |
| AC-54 | `/perf-check` на полном объёме → `/weather/{sol}` и `/radiation/{sol}` p95 ≤ 30 мс на сервере (запрос по PK, 24 строки); `/weather` и `/radiation` (списки) — не хуже прежнего замера | data-engineer | живая проверка |
| AC-55 | Перетаскивание ручки на диапазоне ≈ 4700 солов → перерисовка графика и таблицы без заметной задержки (≤ 50 мс на кадр в профайлере Chrome); сортировка 700 строк ≤ 50 мс | frontend-qa | `/ui-check` |

## 3. Решения

1. Фазы A, B, C выкатываются отдельно, в этом порядке; A не требует бэкенда и данных.
2. Конец диапазона по умолчанию — `source_max_sol`, длина — марсианский год (669 солов, константа клиента `MARS_YEAR_SOLS`); минимум 6 солов. Пресеты: «Last Mars year (default)», «Last 120 sols», «Whole published data».
3. Диапазон хранится в адресе (`?from=&to=`, `history.replaceState`) — ссылку на выбранный отрезок можно передать.
4. Таблица и сортировка — на клиенте по уже загруженному списку (один запрос `/weather` или `/radiation` без параметров, как сейчас); новых параметров API нет.
5. Почасовые `min`/`max` погоды — по сырым годным значениям часа, тем же правилом, что суточные (влажность до 200 % входит, `out_of_range` — нет); порыв часа — 99-й перцентиль, как у сола.
6. Новые ключи часов — плоские рядом со средним (`air_temp_c_min`), а не объекты: изменение только добавляет ключи и не ломает клиентов (ADR-1).
7. Почасовая радиация — отдельная ручка `/radiation/{sol}` по образцу `/weather/{sol}`; в часе — мкГр/ч (единица источника), в сутках — по-прежнему мкГр/сут (ADR-2).
8. Час RAD — по середине наблюдения в LMST из `START_OBS_MARS`, наблюдение целиком в один час (без деления) (ADR-3); «завёрнутые» за полночь наблюдения в часы не входят (ADR-6).
9. Повторная выкачка RAD — только явным `rad sync --reprocess`, с возобновлением по `rad_daily.hourly_revision`; таймер на VPS флаг не передаёт (ADR-4).
10. Широта в API не добавляется: сезон — по полушарию (константа клиента), восход/закат — по `lat` из `/location?latest=true` с запасной константой (ADR-5, вопрос 4).

## 4. Текст для `API.md` (если меняется контракт)

Черновик на «ок» владельца; тимлид переносит через `/contract-change`. Обе правки только добавляют ключи и ручку — клиентов не ломают.

### 4.1. Фаза B — замена примера и первых двух пунктов в `GET /api/v2/rovers/{rover}/weather/{sol}`

Пример `hours` (вместо текущего):
```json
 "hours": [{"hour": 0, "coverage": 0.5,
            "air_temp_c": -71.2, "air_temp_c_min": -73.9, "air_temp_c_max": -68.4,
            "pressure_pa": 735.4, "pressure_pa_min": 734.1, "pressure_pa_max": 736.8,
            "humidity_pct": 5.8, "humidity_pct_min": 4.9, "humidity_pct_max": 6.7,
            "wind_speed_ms": 2.1, "wind_gust_ms": 6.4, "wind_direction_deg": 157.5,
            "ground_temp_c": -78.0, "ground_temp_c_min": -79.3, "ground_temp_c_max": -76.6},
           {"hour": 1, "coverage": 0.0,
            "air_temp_c": null, "air_temp_c_min": null, "air_temp_c_max": null,
            "pressure_pa": null, "pressure_pa_min": null, "pressure_pa_max": null,
            "humidity_pct": null, "humidity_pct_min": null, "humidity_pct_max": null,
            "wind_speed_ms": null, "wind_gust_ms": null, "wind_direction_deg": null,
            "ground_temp_c": null, "ground_temp_c_min": null, "ground_temp_c_max": null}],
```
Пункты (вместо первых двух):
- `hours` — всегда 24 элемента, `hour` 0…23 — час местного среднего солнечного времени (LMST). Ключ параметра без суффикса — среднее за час; `_min` и `_max` — наименьшее и наибольшее годное значение за час (то же правило, что у `min`/`max` сола: влажность выше 100 % входит, значения с флагом `out_of_range` — нет). Единицы и округление — как у сола. Час без измерений параметра — `null` во всех трёх ключах; `coverage` часа — доля его 10-минутных интервалов с измерениями температуры воздуха. Суточные `min`/`max` равны наименьшему из почасовых `_min` и наибольшему из почасовых `_max`.
- Ключи часов следуют ключам марсохода: Curiosity — `air_temp_c`, `pressure_pa`, `humidity_pct`, `ground_temp_c`, `uv_wm2`, у каждого `_min` и `_max`; Perseverance — `air_temp_c`, `pressure_pa`, `humidity_pct`, `ground_temp_c` с `_min` и `_max`, а также `wind_speed_ms` (средняя скорость часа), `wind_gust_ms` (99-й перцентиль скорости за час) и `wind_direction_deg` (самое частое направление часа); вне солов 36–313 все три ключа ветра `null`. Новые ключи (этап 22) добавлены к прежним: клиенты, читающие только средние, не затронуты.

### 4.2. Фаза C — новая ручка после `GET /api/v2/rovers/{rover}/radiation`

### `GET /api/v2/rovers/{rover}/radiation/{sol}` (этап 22)
Только `curiosity`. `sol` — целое от 0 до 2 147 483 647, иначе `422`. Сол без сводки → `404 sol_not_found`. Ответ — RadiationSol и дополнительно:
```json
{"rover": "curiosity", "sol": 4843, "...": "поля RadiationSol",
 "credit": "MSL RAD (SwRI) / NASA PDS Planetary Plasma Interactions Node, MSL-M-RAD-3-RDR-V1.0",
 "citation": "J. Peterson, S. Rafkin, C. Zeitlin, B. Ehresmann, E. Weigle, S. Jeffers, and D.M. Hassler, MSL MARS RADIATION ASSESSMENT DETECTOR RDR V1.0, MSL-M-RAD-3-RDR-V1.0, NASA Planetary Data System, 2013.",
 "hours": [{"hour": 0, "coverage": 0.92, "observations": 4,
            "dose_e_ugy_h": 6.61, "dose_b_ugy_h": 6.98, "count_rate_b_per_s": 7.012},
           {"hour": 1, "coverage": 0.0, "observations": 0,
            "dose_e_ugy_h": null, "dose_b_ugy_h": null, "count_rate_b_per_s": null}]}
```
(значения в примере условные)
- `hours` — 24 элемента, `hour` 0…23 — час местного среднего солнечного времени (LMST) по времени наблюдения из данных прибора. Наблюдение (≈ 16 минут; в 2012 году — 16 минут раз в час) целиком относится к часу, в который попадает его середина. `null` вместо списка — часы для этого сола ещё не посчитаны.
- `dose_e_ugy_h`, `dose_b_ugy_h` — мощность поглощённой дозы в детекторах E и B, **микрогрей в час** (как в источнике; суточные поля — в мкГр/сут), среднее за час, взвешенное по живому времени наблюдений, до 0,01. `count_rate_b_per_s` — скорость счёта детектора B за час, до 0,001. В часы входят те же наблюдения, что в суточную сводку, кроме наблюдений после местной полуночи в конце суточного файла (они относятся к следующему солу и в часах не учитываются).
- `observations` — число наблюдений в часе; `coverage` — доля часа (1/24 сола, 3699 с), покрытая живым временем наблюдений, от 0 до 1, до 0,01. Час без наблюдений — `coverage` 0, `observations` 0, значения `null`.
- `timing_caveat: true` — время наблюдений сола может быть смещено: распределение по часам ненадёжно.
- `credit` и `citation` — как у списка.
- `200` · `404 rover_not_found` · `404 not_found` — марсоход без RAD · `404 sol_not_found` · `422 validation_error`.

## 5. ADR

**ADR-1. Форма почасовых min/max погоды.**
- (а) Плоские ключи `air_temp_c_min`/`_max` рядом со средним. Плюсы: только добавление, старые клиенты не ломаются; столбцы таблицы ложатся один к одному. Минусы: ответ шире (≈ +60 % на час).
- (б) Объекты `{"mean", "min", "max"}` вместо чисел. Плюсы: как у WeatherSol. Минусы: ломает клиентов `hours[]`.
- (в) Отдельный массив `hours_range`. Минусы: два массива для одного графика.
- **Рекомендация: (а)** — без ломки контракта; размер ответа одного сола несущественен (≈ 6 КБ).

**ADR-2. Почасовая радиация в API.**
- (а) Новая ручка `/radiation/{sol}` (RadiationSol + `hours`), как `/weather/{sol}`. Плюсы: единообразие, список не тяжелеет, свой кэш. Минусы: ещё одна ручка.
- (б) Поле `radiation_hours` в списке по параметру `?hours=true`. Минусы: тело списка ×24, ветвление ответа по параметру.
- **Рекомендация: (а)**; имя поля `hours`, а не `radiation_hours` (предложение `designer`), — по образцу погоды.

**ADR-3. Привязка наблюдения RAD к часу LMST.**
- (а) По `START_OBS_MARS` + середина наблюдения, целиком в один час. Плюсы: время из данных прибора, просто; ошибка привязки ≤ 8 мин. Минусы: шкала `[не проверено]` (LMST или LTST).
- (б) LMST по `START_OBS_UTC` через Mars24 и долготу Curiosity. Минусы: нужна функция LMST и долгота из `/location`; `START_OBS_UTC` бывает мусором (фаза A).
- (в) Деление наблюдения между двумя часами пропорционально времени. Плюсы: точнее у краёв. Минусы: сложнее, дробные `observations`.
- **Рекомендация: (а)**; если `data-engineer` найдёт, что шкала — LTST, — переход на (б) с отдельным решением владельца по тексту контракта (там «LMST»).

**ADR-4. Как запустить повторную выкачку RAD и не перекачать историю на VPS.**
- (а) Явный флаг `rad sync --reprocess`: в план попадают солы со сводкой и `rad_daily.hourly_revision < RAD_HOURLY_REVISION`; качаются файлы статуса `loaded` (основной и `extra` не-дубли), `duplicate` не трогаются. Без флага — прежний план по md5. Плюсы: таймер не меняется и не может начать 300 ГБ; прерывание и повтор продолжают с места. Минусы: новый флаг CLI.
- (б) Ревизия в общем плане (как `WEATHER_PARSER_REVISION`). Минусы: на VPS после миграции до импорта таймер начал бы полную выкачку.
- (в) Сохранить файлы RAD на диск при выкачке. Минусы: 230–340 ГБ на ПК; противоречит решению этапа 21.
- **Рекомендация: (а).**

**ADR-5. Широта для сезона и восхода/заката.**
- (а) Ничего в API: полушарие — константа клиента (Curiosity — юг, Perseverance — север; марсоходы его не меняют), широта — `lat` из `/location?latest=true`, запасная константа (−4,6° и 18,4°). Плюсы: контракт не меняется, данные уже есть. Минусы: лишний запрос на странице отчёта (кэшируется).
- (б) Поле `latitude` (или `hemisphere`) в Rover. Плюсы: один источник. Минусы: правка контракта ради справочной константы; значение — из `/location` всё равно.
- **Рекомендация: (а).**

**ADR-6. Наблюдения RAD после местной полуночи в конце файла.**
- (а) В часы не включать (в сутках остаются, как сейчас). Плюсы: «сол — одна транзакция» сохраняется; файл N+1 начинается около полуночи, теряется лишь начало часа 0 следующего сола. Минусы: час 0 у части солов с покрытием ниже.
- (б) Переносить в час 0 сола N+1. Минусы: часы сола зависят от двух файлов; при обработке N+1 нужно перечитывать N или хранить хвосты.
- **Рекомендация: (а)**; `data-engineer` в фазе C подтверждает, что начало файла близко к полуночи (иначе вопрос пересматривается).

## 6. Модель данных, миграция, поток

### Фаза A
Модели и миграций нет.

### Фаза B — миграция `20261201_0020_weather_hourly_extremes.py` (ревизия 0020, `down_revision` 0019)

`weather_hourly` — новые столбцы, все `double precision` null: `air_min_k`, `air_max_k`, `pressure_min_pa`, `pressure_max_pa`, `humidity_min_pct`, `humidity_max_pct`, `ground_min_k`, `ground_max_k`, `uv_min_wm2`, `uv_max_wm2`, `wind_gust_ms`. CHECK на пары: `(x_min IS NULL) = (x_max IS NULL)`. Индексы не нужны (запрос по PK `(rover_id, sol, hour)`). Объём: 11 × 8 Б × ≈ 156 тыс. строк ≈ +14 МБ (таблица ≈ 30–35 МБ). `downgrade` — `DROP COLUMN` одиннадцати столбцов и CHECK; данные средних не трогаются.

Поток:
```
data/weather_raw/… → app/sources/weather.py (без изменений)
  → app/services/weather_sync.py: _Series копит lows[24]/highs[24]; ветер — скорости по часам
    → HourAggregate(+ air_min, air_max, …, uv_min, uv_max, wind_gust) → hourly_rows()
    WEATHER_PARSER_REVISION = 5 → все солы в плане
  → app/repositories/weather.py: replace_hourly (новые столбцы)
  → app/services/weather.py (K → °C, округление, ключи по марсоходу, выключатель 17G)
  → app/api/v2/weather.py → WeatherHour
```
Идемпотентность и сбои — как в этапе 17 (сол — одна транзакция, блокировка `7_250_005`). Выгрузка: `EXPORT_VERSION = 2`, `SCHEMA_REVISION = "0020"`, ≈ 25–30 МБ без сжатия, ≈ 5–6 МБ gzip, в пределах `WEATHER_IMPORT_MAX_BYTES`.

Выкат B: код и миграция на VPS (`/release`) → у прод-часов новые ключи `null` (контракт это допускает) → на ПК `weather parse --rover curiosity` и `--rover perseverance` → `weather export` → копия → `deploy/vps/weather-import.sh` (с «ок» владельца).

### Фаза C — миграция `20261202_0021_rad_hourly.py` (ревизия 0021, `down_revision` 0020)

`rad_hourly` — только часы с наблюдениями (разреженно):
| Столбец | Тип | Примечание |
|---|---|---|
| `sol` | `integer` | PK (`sol`, `hour`); FK `sol` → `rad_daily.sol` `ON DELETE CASCADE` |
| `hour` | `smallint` | CHECK 0–23 |
| `n_obs` | `smallint` not null | CHECK > 0 |
| `live_s` | `double precision` not null | CHECK > 0 |
| `dose_b_mean`, `dose_e_mean` | `double precision` not null | мкГр/ч, взвешенные по `live_s` |
| `bu_count` | `bigint` not null | скорость = `bu_count / live_s` при чтении |

`rad_daily.hourly_revision smallint not null default 0` — ревизия расчёта часов сола (`RAD_HOURLY_REVISION = 1` в коде).

Объём: ≤ 4700 × 24 ≈ 113 тыс. строк (2012 — до 26 часов на сол, позже — 24) × ≈ 70 Б ≈ 8 МБ + PK ≈ 3 МБ ≈ **11–12 МБ**. Выгрузка RAD растёт на ≈ 113 тыс. строк JSON (≈ 14 МБ без сжатия, ≈ 2 МБ gzip), в пределах `RAD_IMPORT_MAX_BYTES` (64 МиБ). `downgrade` — `DROP TABLE rad_hourly`, `DROP COLUMN rad_daily.hourly_revision`.

Поток:
```
CHECKSUMS.TXT, TIMING_ERRORS.TXT → app/sources/rad.py: RadParser читает START_OBS_MARS
    → RadObservation(+ start_mars_sol, start_lmst_s, obs_duration_s)
  → app/services/rad_sync.py: RadLoader.run(from_sol, to_sol, reprocess)
      план без reprocess — прежний (_plan по md5);
      план с reprocess — солы rad_daily с hourly_revision < RAD_HOURLY_REVISION ∩ диапазон
      combine_sol(...) → SolSummary (без изменений) + used-наблюдения
      combine_hours(used, file_sol) → list[HourSummary] (середина в LMST, отброс завёртки)
      одна транзакция: rad_files группы, rad_daily сола (без изменения значений → updated_at прежний),
                       replace_hourly(sol), hourly_revision = 1
  → app/repositories/rad.py → app/services/radiation.py: RadiationService.get_sol(rover, sol)
  → app/api/v2/radiation.py: GET /rovers/{rover}/radiation/{sol}
```
Середина наблюдения: `start_lmst_s + (d / 2) / 1.0274912517`, `d` = `OBS_DURATION`, в раннем формате — длительность `COUNTER_L1` (как у суточной свёртки); час = `floor(середина / 3600) mod 24`. Завёртка: в порядке наблюдений файла LMST стал меньше, чем у предыдущего учтённого, — это и все последующие в часы не входят. Покрытие часа = `min(1, live_s / 3698.97)`.

Идемпотентность и сбои — как в этапе 21: сол — одна транзакция, блокировка `7_250_004`, коды выхода прежние; `--reprocess` прерывается и продолжается по `hourly_revision`. Выгрузка: `EXPORT_VERSION = 2`, `SCHEMA_REVISION = "0021"`, строки `{"t": "hourly", …}`; импорт заменяет и `rad_hourly`.

Месячный таймер (`deploy/vps/rad-sync.sh`, `rad sync` без флага): новые и изменившиеся по md5 солы обрабатываются полностью — с часами и ревизией; старые солы с ревизией 0 не трогаются (AC-42). Скрипт не меняется.

Выкат C: код и миграция 0021 на VPS (часы `null`, контракт допускает) → пробная выкачка C1–C4 (`data-engineer`) → полная `rad sync --reprocess` на ПК (запускает владелец в отдельном окне, ≈ сутки, можно частями) → отчёт и сверка → `rad export` → `deploy/vps/rad-import.sh` (с «ок» владельца). Таймер на VPS работает всё время; импорт и таймер разведены блокировкой.

## 7. Задания ролям и общие имена

**Общие имена:**
- Фронтенд A: `frontend/src/lib/solRange.ts` — `MARS_YEAR_SOLS = 669`, `MIN_RANGE_SOLS = 6`, `type SolRange = [number, number]`, `defaultRange({first, sourceMax})`, `presetRange(preset, {first, sourceMax})`, `clampRange(range, bounds)`, `parseRangeQuery(search)`; компоненты `frontend/src/components/SolRangeSlider.tsx`, `SolTable.tsx` (пропсы `rows`, `columns`, `range`, `onJump`, `rowLink`); `domainOf` в `lib/weather.ts` заменяется диапазоном.
- Фронтенд B/C: маршрут `{ name: "solReport"; rover: string; sol: number }` в `router.tsx`, путь `/rovers/{rover}/sols/{sol}/conditions`; страница `frontend/src/pages/SolReport.tsx`; `frontend/src/components/HourChart.tsx`; `frontend/src/lib/marsSeason.ts` — `HEMISPHERE`, `FALLBACK_LAT`, `seasonOf(ls, hemisphere)`, `sunTimes(ls, latDeg)`.
- Бэкенд B: `HourAggregate` + `air_min`, `air_max`, `pressure_min`, `pressure_max`, `humidity_min`, `humidity_max`, `ground_min`, `ground_max`, `uv_min`, `uv_max`, `wind_gust`; `WEATHER_PARSER_REVISION = 5`; схема `WeatherHour` (ключи раздела 4.1); `weather_transfer.EXPORT_VERSION = 2`, `SCHEMA_REVISION = "0020"`.
- Бэкенд C: `RadObservation.start_mars_sol`, `.start_lmst_s`, `.obs_duration_s`; `HourSummary(hour, n_obs, live_s, dose_b_mean, dose_e_mean, bu_count)`, `combine_hours(observations, file_sol) -> list[HourSummary]`, `RAD_HOURLY_REVISION = 1`, `LMST_RATIO = 1.0274912517`, `HOUR_LIVE_S = 3698.97` в `rad_sync.py`; ORM `RadHourly`; `rad_repo.replace_hourly(session, sol, rows)`; `RadiationService.get_sol(rover, sol)`; схемы `RadiationSolDetail`, `RadiationHour`; CLI `rad sync --reprocess`; `rad_transfer.EXPORT_VERSION = 2`, `SCHEMA_REVISION = "0021"`.

- `backend-dev`: B — миграция 0020, `weather_sync.py` (`_Series` с `lows`/`highs` по часам, порыв по часам), репозиторий, сервис (выключатель 17G и для `_min/_max`), схема, `weather_transfer.py`; C — миграция 0021, парсер `START_OBS_MARS`, `combine_hours`, план `--reprocess` (только `loaded`-файлы, без проб дублей), транзакция сола, ручка и кэш в `app/api/caching.py`, `rad_transfer.py`, справка CLI; обе — `docs/openapi.json`, `examples/types.ts`, вставка текстов `tech-writer` в `/developers` и `/llms.txt`.
- `qa-engineer`: B — AC-15…AC-24; C — AC-34…AC-47. Фикстуры: для B — существующие MEDA/REMS, при нехватке часа с одним показанием — синтетический CSV с пометкой в README фикстур; для C — дописать в фикстуры 4843 и сол 10 значения `START_OBS_MARS` из `data/rad_sample/` (`/record-fixtures rad`, без запросов), одно наблюдение без времени. Граничные: час с одним наблюдением, все наблюдения часа отброшены QC, сол из одного наблюдения, наблюдение ровно на границе часа (середина 14:00:00 → 14), `--reprocess` с `--from-sol` без солов в плане, импорт выгрузки 0020/0017 поверх.
- `data-engineer`: (1) до разработки C, без запросов — по `data/rad_sample/`: формат `START_OBS_MARS` в обоих форматах, шкала LMST или LTST (сверка с Mars24 по `START_OBS_UTC` и долготе Curiosity), начало файла относительно полуночи, доля «мусорных» значений; дописать в `DATA-SOURCES.md` § 7; (2) B — переразбор на ПК, AC-50, AC-51, `weather export`; (3) C — пробная выкачка C1–C4, AC-52; после полной — отчёт `docs/reports/stage-22-rad-hourly.md` (солы с часами, причины пропусков, распределение покрытия часа по годам, трафик и время), AC-53, AC-54, `rad export`.
- `designer`: правки не нужны; по запросу `frontend-dev` — уточнения по макетам. Вопрос «Auto» для шкалы радиации — раздел 10.
- `frontend-dev`: A — ползунок, таблица, пресеты, адрес, исправления зоны и `domainOf` на страницах `/data/weather` и `/data/radiation` по `weather-range.html`, `radiation-range.html`; B — страница отчёта по `sol-report-*.html`, ссылки из таблиц; C — блок почасовой радиации вместо «Concept». Без новых зависимостей (SVG, как `LineChart.tsx`), типы — из `openapi-ts`.
- `frontend-qa`: AC-1…AC-14, AC-25…AC-33, AC-48, AC-49, AC-55 (Vitest; Playwright — AC-10, AC-33 и сквозной «таблица → Sol report → Photos»).
- `tech-writer`: B — абзац о почасовых `min`/`max`; C — о `/radiation/{sol}` (мкГр/ч против мкГр/сут, привязка к часу, `hours: null`) для `/developers` и `/llms.txt`, текстом в отчёте.
- `devops`: B, C — выкат миграций обычным `update.sh`; хранение новых выгрузок рядом с бэкапами; `rad-sync.sh` и юниты без изменений (проверить, что флаг `--reprocess` нигде не передаётся).
- `reviewer` — каждая фаза; `security-reviewer` — фаза C (новая публичная ручка, разбор нового поля источника, импорт формата v2).

## 8. Проверка и бюджет

- Команды: `uv run ruff check .`, `uv run ruff format .`, `uv run pytest -q -n 12`, `/migration-check` (0020, 0021), `uv run python -m app.cli openapi --output docs/openapi.json`; в `frontend/` — `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.
- Живая проверка: A — `/ui-check` страниц `/data/weather` (оба марсохода) и `/data/radiation`: диапазон по умолчанию, 120 солов, за пределами опубликованного, «Jump to sol», 360 px, тёмная тема. B — переразбор, `/weather/344` Curiosity (AC-50), `/ui-check` отчётов Curiosity 344 и Perseverance 100 (данные, нет данных, неполное покрытие, `503`). C — C1–C4, `GET /radiation/4843`, `/ui-check` отчёта Curiosity с радиацией; после полной выкачки — `/perf-check`.
- Ревью: `reviewer` на каждую фазу; `security-reviewer` — фаза C.
- Ожидаемый расход токенов: architect — этот черновик ≈ 2 %, редакция 2 по ответам ≈ 0,5 %; `data-engineer` — проверка `START_OBS_MARS` ≈ 0,5–1 %, переразбор B ≈ 0,5 %, пробная выкачка C ≈ 1 %, отчёт полной ≈ 1–2 %. Остальные роли (порядок): A — `frontend-dev` крупно, `frontend-qa` средне; B — `backend-dev` и `qa-engineer` мало–средне, `frontend-dev` крупно (новая страница), `frontend-qa` средне; C — `backend-dev` и `qa-engineer` средне, `frontend-dev` мало; `reviewer` — по кругу на фазу, `security-reviewer` — один круг на C.

## 9. Внешние запросы (для «ок» владельца)

**Фазы A и B:** нет (сырьё погоды уже на ПК; фикстуры из имеющихся файлов).

**Фаза C:** хост `pds-ppi.igpp.ucla.edu`, том `MSL-M-RAD-3-RDR-V1.0`, User-Agent `mars-rover-api/0.1 (personal project)`, строго по одному, пауза ≥ 2 с, ретраи — как в этапе 21.

| № | Запуск | Кто | Запросов | Объём |
|---|---|---|---|---|
| C1 | `rad sync --reprocess --from-sol 10 --to-sol 10` | data-engineer | 2 перечня (условные) + 1 файл | ≈ 13 МБ |
| C2 | `rad sync --reprocess --from-sol 1649 --to-sol 1649` | data-engineer | 2 условных + 1 файл (дубли не качаются) | ≈ 67 МБ |
| C3 | `rad sync --reprocess --from-sol 4842 --to-sol 4843` | data-engineer | 2 условных + 2 файла | ≈ 143 МБ |
| C4 | повтор C3 | data-engineer | 2 условных | ≈ 0 |
| C5 | `rad sync --reprocess` — вся история | владелец, ПК, отдельное окно | 2 + ≈ 4700 файлов `loaded` (до ≈ 5350, если среди `extra` есть не-дубли) | **≈ 230–340 ГБ**, обрывы — до +20 %; ≈ сутки (9–40 ч плюс паузы ≈ 2,6 ч) |

C1–C4: ≈ 13 запросов, ≈ 225 МБ — в пределах самостоятельности `data-engineer` (5 % недельного лимита), перечислены для полноты. **C5 требует отдельного «ок» владельца.** Ежемесячно на VPS — без изменений (2 условных запроса; раз в 4–5 месяцев ≈ 130 файлов, ≈ 9 ГБ).

## 10. Вопросы владельцу

1. **Контракт B (раздел 4.1):** почасовые `_min`/`_max` плоскими ключами рядом со средним, у ветра — `wind_gust_ms` часа; только добавление ключей. *Рекомендация: «ок».*
2. **Контракт C (раздел 4.2):** ручка `GET /rovers/curiosity/radiation/{sol}` с `hours` в мкГр/ч (суточные остаются в мкГр/сут), `hours: null`, пока часы не посчитаны; наблюдения после местной полуночи в конце файла в часы не входят. *Рекомендация: «ок».*
3. **Полная повторная выкачка RAD (C5):** ≈ 4,7–5,35 тыс. запросов, 230–340 ГБ, ≈ сутки на ПК, запуск вами после успешных C1–C4. *Рекомендация: «ок», запуск после отчёта `data-engineer` по C1–C4.*
4. **Широта в API (пункт 6 решений):** *рекомендация — не добавлять*: полушарие — константа клиента, широта для восхода/заката — из уже существующего `/location?latest=true` (`lat`), запасная константа. Поле `latitude` в Rover — только если хотите видеть её в API для сторонних клиентов.
5. **Шкала радиации по умолчанию (вопрос `designer`):** «Auto» — от нуля при диапазоне ≥ 400 солов, иначе по данным. *Рекомендация: «Auto», как в макете.*

**Решения владельца 2026-11-25:** все пять — по рекомендациям («Да, всё ок»): контракты 4.1 и 4.2 приняты (вносятся в `API.md` в начале фаз B и C соответственно); полная выкачка C5 разрешена после отчёта по C1–C4; широту в API не добавляем; шкала радиации — «Auto». Старт — фаза A.

**Решение тимлида 2026-11-25 (ревью фазы A):** на `/data/weather` марсоход пишется в адрес вместе с диапазоном — `?rover=curiosity&from=&to=` (без `rover` — Perseverance, как раньше); диапазон из адреса, не пересекающийся с `[первый сол данных, max(max_sol, source_max_sol)]`, считается неверным → диапазон по умолчанию (уточнение AC-12).

**Решение владельца 2026-11-25 (после проверки `data-engineer`, `DATA-SOURCES.md` § 7 «RAD RDR: время наблюдения START_OBS_MARS»):** часы радиации — в LMST, как у погоды. Метка `START_OBS_MARS` не считается LMST (в 2026 это LTST, в 2012 залипает, в 2017 сбит UTC): час вычисляется из `START_OBS_UTC` по Mars24 с проверкой правдоподобия, метка — только для сола и сверки; плюс фильтр мусорных доз (< 1 мкГр/ч при `QC=0`). До фазы C — редакция 2 спецификации (`architect`): AC-34, ADR-3, ADR-6, раздел 7.
