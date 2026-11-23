# Этап 17: погода по солам — черновик архитектора

Редакция 1 (2026-11-23, HEAD `cd24b16`, миграции до `0017`). Основа: решения владельца по погоде (переданы тимлидом 2026-11-23, пункты 1–9), `docs/PLAN.md` «Этап 17», `docs/reports/stage-17-weather-analysis.md` (алгоритм свёртки, объёмы), `docs/reports/stage-17-weather-inventory.md`, `docs/DATA-SOURCES.md` (REMS `mslrem_1001`, MEDA, «Погода: разбор локальных файлов»), `docs/API.md` (общие правила v2, RadiationSol, `/radiation`, кэширование), `docs/proposals/stage-21-rad.md` (образец: таблицы, CLI, выгрузка ПК → VPS, блокировка), `docs/ARCHITECTURE.md`, `app/api/v2/radiation.py` (кэш тела), `design/NOTES.md` «Раздел «Data»», `design/mockups/data.html`, `.claude/tools/nightly_download.ps1`, `data/weather_raw/` (метки и перечни коллекций).

## 0. Цель, границы, критерий

**Входит:**
- разбор локальных сырых файлов `data/weather_raw/` на ПК владельца: MEDA (`CAL_ATS`, `CAL_WS`, `DER_PS`, `DER_RHS`) и REMS `RMD`; источник `app/sources/weather.py` (форматы NASA знает только он), свёртка в сервисе;
- таблицы `weather_files`, `weather_daily`, `weather_hourly`, `weather_dataset` (миграция `0018`): суточные сводки, почасовые средние, состояние файлов;
- флаги аномалий сола (словарь и правила — раздел 3); среднее всегда отдаётся, в том числе у неполных солов;
- CLI `weather parse` (инкрементально по размеру, времени изменения и версии файла), `weather status`, `weather export`, `weather import`;
- ручки v2 `GET /api/v2/rovers/{rover}/weather` (все солы, только сутки) и `GET /api/v2/rovers/{rover}/weather/{sol}` (сутки + 24 часа) для `curiosity` и `perseverance`;
- фронтенд: секция «Weather by sol» в разделе «Data» (суточный график с полосой min–max и средним, метки аномалий, инспектор сола с почасовыми графиками); сначала `designer` правит макет;
- **часть 17G (закрыта до проверки):** оценка температуры грунта Perseverance по MEDA TIRS с пометкой `estimate` — только после того, как `data-engineer` проверит метод на опубликованных значениях (раздел 9).

**Не входит:** ветер Curiosity (нет ни в API, ни на сайте); УФ MEDA (коллекция не качается); сырьё в БД; загрузка файлов из `app/` (качает `scripts/weather_download.py`, решение 12); таймер погоды на VPS; погода в манифесте, карточке снимка и на странице сола (вопрос 5); v1; влажность в абсолютных единицах (VMR); вертикальный ветер; удаление сырых файлов (хранятся все, решение владельца 6).

**Готово, когда:** все AC зелёные; `weather parse` на ПК дал сводки по всем опубликованным и скачанным солам обоих марсоходов, `data-engineer` сверил их с прототипом (`docs/reports/stage-17-meda-daily.csv`) и сдал отчёт полного прогона; страница `/data#weather` показывает обе миссии на этих данных, с задержкой публикации (серая зона после `source_max_sol`). Часть 17G и перенос на VPS — после этапа, по разделам 6 и 9.

## 1. Факты

- MEDA скачан полностью: `CAL_ATS` 1754 сола, `CAL_WS` 265 (солы 36–315), `DER_PS` 1754, `DER_RHS` 1754, `DER_TIRS` 1751 (`stage-17-weather-analysis.md` § 1). REMS `RMD`: на 2026-11-23 есть солы 1, 9–71, остальное (≈ 4703 файла, ≈ 38 ГБ) качается ночами задачей Windows 00:00–05:00 (`.claude/tools/nightly_download.ps1`, `scripts/weather_download.py --mission rems --interval 3`). Солов 2–8 нет в источнике.
- Раскладка: MEDA `meda/data_{derived,calibrated}_env/sol_AAAA_BBBB/sol_NNNN/WE__NNNN___________{DER,CAL}_ДАТЧИК___…P0V.CSV` + `.xml`; REMS `rems/DATA/SOL_AAAAA_BBBBB/SOLNNNNN/RME_<SCLK>RMDNNNN0000000_______P1.TAB` + `.LBL`/`.xml` (analysis § 5 п. 4, inventory § 1). Версия — суффикс `P0V` (MEDA) и `P1` (REMS); у ранних солов MEDA есть несколько версий одного продукта.
- Время: сол — первые 5 цифр `LMST` (`00136M00:00:00.471`); строк с чужим солом в `CAL_ATS` нет. Частота 1–2 Гц (MEDA), 1 Гц (REMS), сеансами.
- Дата: в метках MEDA и REMS есть `start_date_time` (проверено: MEDA сол 1 `2021-02-19T21:35:54.970Z`, REMS сол 9 `2012-08-15T08:37:32.099Z`). Ls: у MEDA `start_solar_longitude` в метке (inventory § 2); у REMS в метке `solar_longitude` не найден [не проверено]. **Уточнено 2026-11-23:** `ls` у обоих марсоходов вычисляется по алгоритму Mars24 (Allison & McEwen 2000) от начала сола (`start_date_time` метки), чистая функция без зависимостей; `label_ls` в `weather_files` — только для сверки (MEDA).
- Воздух MEDA — медиана `ATS_LOCAL_TEMP1..3` (T4/T5 греются от ровера на +4…+6 К, analysis § 2); давление `DER_PS.PRESSURE`; влажность `DER_RHS.LOCAL_RELATIVE_HUMIDITY`; пропуски `999999999` и пусто. Ветер `CAL_WS`: бумы с `ALG_STATE_STABLE = 1`; дневной max до 52 м/с — выбросы, порыв = p99. Конвенция направления MEDA в метке не описана [не проверено] (у REMS — «откуда дует, по часовой от севера»).
- REMS `RMD`: 40 колонок, порядок подтверждён метками (analysis § 3). Фильтры: воздух `AMBIENT_TEMP` при байтах 7 и 8 маски = `1`; давление `PRESSURE` ≠ −999 и байт 1 маски = `1`; грунт `BRIGHTNESS_TEMP` при `GTS` байтах 0, 1 = `1`; УФ `UV_ABC` ≥ 0; влажность — как есть, «предварительная». Глитч: грунт 92,6 К на соле 30 без маски.
- Влажность > 100 %: MEDA солы 1228–1229, 1241, 1852–1853 (до 1601 строки за сол), выброс 2 680 332 % на соле 950 (inventory § 2). Распределение значений > 100 % по величине [не проверено].
- Покрытие: MEDA `hours_covered ≥ 18` у 1599 из 1754 солов (91 %); REMS — 10-минутное покрытие медиана 0,33 при 24 часах. Прототип делил число 10-минутных корзин на 148 (88 775 / 600); корзины по LMST дают 144 на сол — в спецификации 144 (решение 4), `data-engineer` сверяет.
- Стоимость разбора: MEDA 14,5 ГБ — 90 с на 14 процессах (≈ 21 мин одним); REMS 38 ГБ — ≈ 20 мин одним процессом; узкое место — диск. Сводки ≈ 6,5 тыс. строк (2–3 МБ), почасовые ≈ 156 тыс. строк (15–20 МБ) (analysis § 4).
- Грунт Perseverance: в `DER_TIRS` есть `UPWARD_LW_IRRADIANCE` (6,5–30 мкм, Вт/м²); наивный Стефан — Больцман даёт ≈ 201 К в 15:28 LTST сола 1 — слишком холодно (полоса ловит только часть излучения). `CAL_TIRS` не скачан; в перечне `collection_meda_data_calibrated_env_inventory.csv` 1751 продукт `cal_tirs`, имя файла по LID-версии (`p::1.0`) [не проверено: соответствие `P01`], содержимое (есть ли яркостные температуры каналов) [не проверено].
- Образец кода: `app/api/v2/radiation.py` — `RadiationBodyCache` (готовые тела ответов по отпечатку данных), `_given_once` (повтор параметра → 422). Блокировки `7_250_001…004` заняты, погода — `7_250_005`.

## 2. Критерии приёмки

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | Фикстура `CAL_ATS` (прорежена, строки во всех 24 часах, один ряд с T2 вне 150–300 К) → `parse_meda_ats` даёт строки `(lmst_seconds, air_k)`, где `air_k` = медиана T1–T3; ряд с глитчем → не в данных, счётчик `out_of_range[air_temp]` = 1; T4/T5 не читаются | qa-engineer | unit |
| AC-2 | Фикстуры `DER_PS`, `DER_RHS` с `999999999`, пустыми полями, давлением 50 Па и влажностью 2 680 332 % и 104,5 % → пропуски отброшены без счёта; 50 Па и 2 680 332 % — `out_of_range`; 104,5 % — в данных как есть, счётчик `humidity_over_100` = 1 | qa-engineer | unit |
| AC-3 | Фикстура `CAL_WS` (бум 1 стабилен, бум 2 нет; строка с пустой скоростью) → скорость = значение бума 1; строка без стабильных бумов и без скорости не учитывается; направление попадает в сектор 16 румбов с центром на север (0° и 359° → сектор 0) | qa-engineer | unit |
| AC-4 | Фикстура REMS `RMD` (кавычки, маски `X`, `-999.00`, ветер в 3 строках) → воздух только при байтах 7 = 8 = `1`, давление при байте 1 = `1`, грунт при `GTS` 0 = 1 = `1`, УФ ≥ 0; колонки ветра не читаются; строка с 39 полями → `WeatherFormatError` с номером строки | qa-engineer | unit |
| AC-5 | Фикстура REMS сол 30 с `BRIGHTNESS_TEMP` 92,6 К (маска «плохо») → в данных грунта значения нет, у сола флаг `{"code": "out_of_range", "parameter": "ground_temp_c"}` (граница проверяется до маски) | qa-engineer | unit |
| AC-6 | Строка с солом `LMST`, не равным солу из пути файла → отброшена, счётчик `foreign_sol_rows` в `weather_files`; файл, где таких строк > 50 % → `WeatherFormatError` | qa-engineer | unit |
| AC-7 | `combine_sol` на синтетике: часы 0–23 по 60 значений, температура в часе h = h К → `mean` = среднее часовых средних (11,5 + сдвиг), `min`/`max` — сырые экстремумы, `hours_covered` 24, `coverage` = корзины/144 до 0,01 | qa-engineer | unit |
| AC-8 | Синтетика: 10 000 строк в часе 3 и 10 строк в часе 15 → `mean` = (среднее часа 3 + среднее часа 15) / 2, а не среднее по строкам | qa-engineer | unit |
| AC-9 | Сол с данными только в 6 часах → `mean` не `null`, `hours_covered` 6, флаг `{"code": "partial_coverage", "parameter": null}`; ровно 18 часов → флага нет; 17 → есть | qa-engineer | unit |
| AC-10 | Параметр без годных значений за сол (давление MEDA сол 10) → его объект `null`, флага нет; сол, где нет ни одного годного значения ни одного параметра → строки `weather_daily` нет | qa-engineer | unit |
| AC-11 | Ветер MEDA, 1000 значений: 990 по 3 м/с, 10 по 50 м/с → `gust_ms` = p99 с линейной интерполяцией по отсортированным значениям (позиция 0,99 × 999 = 989,01 → 3 + 0,01 × 47 = 3,47, в ответе `3.5`), а не 50; `direction_mode_deg` = центр сектора с наибольшим числом значений | qa-engineer | unit |
| AC-12 | Несколько версий продукта в каталоге сола (`…CAL_ATS…P01.CSV`, `…P02.CSV`) → разбирается старшая, `source_version` = `{"CAL_ATS": "P02", …}`; `.part` и `.xml` без `.CSV` игнорируются | qa-engineer | unit |
| AC-13 | `weather parse --rover perseverance` на каталоге-фикстуре (3 сола) → в `weather_daily` 3 строки, в `weather_hourly` 72, в `weather_files` строка на каждый разобранный файл с `size_bytes`, `mtime_ns`, `version`, `status = parsed` | qa-engineer | integration |
| AC-14 | Повторный `weather parse` без изменений файлов → 0 разобранных файлов, `updated_at` строк не меняется, код 0 | qa-engineer | integration |
| AC-15 | Изменить `mtime` одного файла сола 2 (содержимое то же) → пересчитан только сол 2, `updated_at` его строки не меняется (значения те же); изменить содержимое → `updated_at` обновлён | qa-engineer | integration |
| AC-16 | Положить в сол 2 файл старшей версии → пересчитан сол 2, `source_version` обновлён; сменить `WEATHER_PARSER_REVISION` → пересчитаны все солы | qa-engineer | integration |
| AC-17 | `weather parse --from-sol 2 --to-sol 2` → затронут только сол 2; `--from-sol 5 --to-sol 2` → код 2 без изменений | qa-engineer | integration |
| AC-18 | Битый файл сола 2 (`WeatherFormatError`) → строки сола 2 не меняются, `weather_files.status = failed` с текстом ошибки, остальные солы разобраны, код 1; после исправления файла повтор → сол 2 разобран, код 0 | qa-engineer | integration |
| AC-19 | Все файлы сола 3 удалены с диска → без `--prune` сводки сола 3 остаются, `weather status` показывает «файлы пропали: 1 сол»; с `--prune` → строки сола 3 удалены; `--prune`, если пропало больше `1 − WEATHER_MIN_KEEP_RATIO` солов → отказ, код 1 | qa-engineer | integration |
| AC-20 | Два запуска `weather parse`/`weather import` одновременно → второй сразу выходит с кодом 2 (`pg_try_advisory_lock(7_250_005)`) | qa-engineer | integration |
| AC-21 | Прерывание посреди сола (исключение после разбора файлов, до записи) → данные сола в БД прежние; повтор дописывает | qa-engineer | integration |
| AC-22 | `weather export --output F` → gzip JSON Lines: `meta` (`format` `mars-weather-export`, `version` 1, `schema_revision` `0018`, счётчики по марсоходам), строки `daily`, `hourly`, `dataset`; `weather import --input F` на пустую базу → три таблицы равны исходным по всем столбцам (`weather_files` не переносится); повтор → без изменений, код 0 | qa-engineer | integration |
| AC-23 | `weather import`: чужой `format`, `version` ≠ 1, битый gzip, сол меньше 0, час 24, `NaN`, счётчики `meta` не равны числу строк, распакованный объём больше `WEATHER_IMPORT_MAX_BYTES` → код 2, база не изменена; солов марсохода меньше `WEATHER_MIN_KEEP_RATIO` от имеющихся → код 1, с `--force` → импорт | qa-engineer | integration |
| AC-24 | Миграция `0018`: `upgrade` → 4 таблицы, CHECK-ограничения (`hour` 0–23, `hours_covered` 0–24, `coverage` 0–1); `downgrade` → таблиц нет, остальные таблицы не тронуты | qa-engineer | integration |
| AC-25 | `GET /api/v2/rovers/perseverance/weather` → 200, шапка (`rover`, `credit`, `source_url`, `updated_at`, `source_max_sol`, `parameters`), `items` по `sol ASC`, у каждого ключи как в примере `API.md`; ключей `ground_temp_c` и `uv_wm2` нет (пока 17G закрыта) | qa-engineer | api |
| AC-26 | `GET /api/v2/rovers/curiosity/weather` → у каждого сола и в `parameters` нет ключа `wind` ни в каком виде; есть `ground_temp_c`, `uv_wm2` | qa-engineer | api |
| AC-27 | Perseverance, сол 400 (вне 36–313) → `wind: null`; сол 100 → `wind` с `speed_mean_ms`, `gust_ms`, `direction_mode_deg` | qa-engineer | api |
| AC-28 | Единицы и округление: 220,15 К в БД → `-53.0` °C; давление до 0,1 Па, влажность до 0,1 %, УФ до 0,01 Вт/м², ветер до 0,1 м/с, `coverage` до 0,01; `-0.0` не выводится (как `0.0`) | qa-engineer | api |
| AC-29 | `sol_min=10&sol_max=20` → только солы 10–20; `sol_min=20&sol_max=10` → 422; `sol_min=-1`, `sol_min=abc`, `sol_min` дважды → 422; неизвестный параметр игнорируется | qa-engineer | api |
| AC-30 | `GET /api/v2/rovers/opportunity/weather` (и `spirit`, `ingenuity`) → 404 `not_found`; `/rovers/pluto/weather` → 404 `rover_not_found`; пустая база → 200, `items: []`, `updated_at`, `source_max_sol` — `null` | qa-engineer | api |
| AC-31 | `GET /api/v2/rovers/perseverance/weather/100` → 200 WeatherSolDetail: поля сола как в списке + `hours` — ровно 24 элемента `hour` 0…23; часы без данных — значения `null`, `coverage` 0 | qa-engineer | api |
| AC-32 | `/weather/{sol}` у Curiosity → в `hours` нет `wind_*`; есть `ground_temp_c`, `uv_wm2`; `source.files` — список `{product, version, file, link}` всех разобранных файлов сола | qa-engineer | api |
| AC-33 | `/weather/5` (сола нет в данных) → 404 `sol_not_found`; `/weather/abc`, `/weather/-1`, `/weather/2147483648` → 422 | qa-engineer | api |
| AC-34 | Запрос любой из двух ручек → `Cache-Control: public, max-age=3600`, `ETag`, `304` при `If-None-Match`; ошибки `no-store`; после `weather import` с новыми данными тело списка меняется (кэш тела сброшен по отпечатку `weather_dataset.updated_at`) | qa-engineer | api |
| AC-35 | Флаги в ответе: сол с `humidity_over_100` → `flags` содержит `{"code": "humidity_over_100", "parameter": "humidity_pct", "count": N}`, `humidity_pct.max` > 100 выводится как есть; порядок флагов — по словарю раздела 3, затем по `parameter` | qa-engineer | api |
| AC-36 | Пересборка `docs/openapi.json` → у `WeatherSol` ключи `wind`, `ground_temp_c`, `uv_wm2` необязательные (не `required`), описание говорит, у каких марсоходов они есть | qa-engineer | api |
| AC-37 | Полный прогон на ПК: MEDA 1754 сола → строк `weather_daily` 1754 (или 1753 при соле без единого параметра — с объяснением), значения min/max/mean воздуха и давления совпадают с `stage-17-meda-daily.csv` до 0,05 К / 0,05 Па (кроме `coverage`, где 148 → 144), список расхождений — в отчёте | data-engineer | ручная проверка |
| AC-38 | Полный прогон → отчёт: число солов по каждому флагу и параметру, 20 худших солов по каждому флагу; если флаг `out_of_range` у параметра стоит у > 10 % солов — границы пересматриваются до выкатки | data-engineer | ручная проверка |
| AC-39 | `weather parse` по всей истории обоих марсоходов → ≤ 30 мин на ПК при `WEATHER_PARSE_WORKERS` по умолчанию; повтор без изменений ≤ 60 с; память процесса-исполнителя ≤ 300 МБ | data-engineer | ручная проверка |
| AC-40 | База с полной историей, тёплый кэш, `GET /rovers/curiosity/weather` → p50 ≤ 200 мс, тело gzip ≤ 700 КБ; `/weather/{sol}` p50 ≤ 50 мс (`/perf-check`) | data-engineer | ручная проверка |
| AC-41 | Секция «Weather by sol» раскрыта на `/data#weather` → один запрос `/api/v2/rovers/perseverance/weather` (по умолчанию марсоход Perseverance), график: полоса min–max и линия среднего для выбранного параметра, ось X — сол, серая зона от `source_max_sol` до `max_sol` марсохода с подписью о задержке публикации | frontend-qa | компонентный |
| AC-42 | Переключатель Curiosity → запрос `/rovers/curiosity/weather`; в списке параметров нет «Wind», есть «Ground temp» и «UV»; у Perseverance нет «Ground temp» и «UV», пока API не отдаёт ключ | frontend-qa | компонентный |
| AC-43 | Сол с флагами → метка на графике (точка другого цвета) и подсказка со списком флагов человеческими словами; сол с `partial_coverage` — среднее показано пунктиром или полупрозрачно, не скрыто | frontend-qa | компонентный |
| AC-44 | Инспектор: выбор сола (ползунок или щелчок по графику) → запрос `/weather/{sol}`, почасовые графики параметров (24 точки, разрывы на `null`), подпись «LMST hours»; у влажности пометка «preliminary» | frontend-qa | компонентный |
| AC-45 | Загрузка, ответ `items: []`, ошибка, 404 сола в инспекторе → состояния «loading», «not loaded yet», ошибка с повтором, сообщение «no data for this sol» без падения; 360 px и тёмная тема без горизонтальной прокрутки | frontend-qa | компонентный |
| AC-46 | Сквозной сценарий: шапка → Data → `#weather` → переключение марсохода → выбор сола → почасовые графики | frontend-qa | e2e |
| AC-47 | `deploy/vps/weather-import.sh PATH` на выгрузке из AC-22 → данные на месте, счётчики выведены, повтор без изменений | devops | ручная проверка |
| AC-48 | **17G.** `WEATHER_M20_GROUND_ENABLED=false` → у Perseverance ключа `ground_temp_c` нет ни в сутках, ни в часах, ни в `parameters`; `true` и данные есть → ключ есть, в `parameters.ground_temp_c.quality` = `estimate` | qa-engineer | api |
| AC-49 | **17G.** Метод оценки грунта на солах из опубликованной статьи → отклонение от опубликованных значений ≤ 3 К по дневному максимуму и ночному минимуму на ≥ 3 солах; отчёт `docs/reports/stage-17-meda-ground.md` с формулой и остатками; без этого флаг не включается | data-engineer | ручная проверка |

## 3. Решения

1. **Сол** — из пути (`sol_NNNN`, `SOLNNNNN`), строки с другим солом в `LMST` отбрасываются (AC-6). **Час** — `floor(LMST_секунд / 3600)`, 0–23, местное среднее солнечное время (LMST); корзина 10 мин — `floor(LMST_секунд / 600)`, 144 в соле.
2. **Величины.** Perseverance: воздух — медиана `ATS_LOCAL_TEMP1..3` (если все три есть); давление `DER_PS.PRESSURE`; влажность `DER_RHS.LOCAL_RELATIVE_HUMIDITY`; ветер `CAL_WS` (скорость — среднее стабильных бумов, направление — направление суммы единичных векторов стабильных бумов). Curiosity: воздух `AMBIENT_TEMP`, давление `PRESSURE`, грунт `BRIGHTNESS_TEMP`, влажность `LOCAL_RELATIVE_HUMIDITY`, УФ `UV_ABC`; фильтры по маскам — как в разделе 1. Ветра у Curiosity нет нигде.
3. **Суточная сводка** (по каждому параметру, где есть годные значения): `min`, `max` — сырые экстремумы годных значений; **`mean` — среднее почасовых средних по часам с данными, всегда, в том числе у неполных солов** (решение владельца 2). У УФ — только `max`, `mean` (ночной `min` всегда 0). Ветер — `speed_mean_ms` (среднее почасовых), `gust_ms` (p99 значений сола, линейная интерполяция), `direction_mode_deg` (центр сектора 16 румбов по 22,5°, сектор 0 — север, с наибольшим числом значений).
4. **Покрытие сола:** `hours_covered` — число часов LMST, где есть хоть одно годное значение температуры воздуха (у обоих марсоходов — основной параметр); `coverage` — доля 10-минутных корзин из 144 с годной температурой воздуха, до 0,01. Почасовая `coverage` — корзины часа / 6.
5. **Словарь флагов** (элемент `flags[]`: `{"code", "parameter", "count"}`; `parameter` — ключ ответа или `null` для сола; `count` — число значений или `null`). Порядок кодов — как в таблице, внутри кода — по `parameter`.

   | `code` | `parameter` | Правило | Что с данными |
   |---|---|---|---|
   | `partial_coverage` | `null` | `hours_covered < WEATHER_FULL_HOURS` (18) | всё отдаётся; `mean` — по наблюдавшимся часам, может быть смещён к дню или ночи |
   | `humidity_over_100` | `humidity_pct` | есть значения 100 < h ≤ `WEATHER_HUMIDITY_MAX` (200) %; `count` — их число | значения остаются как есть и входят в min/max/mean и почасовые |
   | `out_of_range` | любой | в исходных строках сола есть значения параметра вне физических границ (проверка — до масок источника, по всем числовым значениям, кроме пропусков `999999999`, `-999.00`, пусто); `count` — их число | значения не учитываются |

   Физические границы (константы в `app/services/weather_sync.py`, менять — с пересчётом `WEATHER_PARSER_REVISION`): воздух 150–300 К; грунт 120–330 К; давление 300–1200 Па; влажность меньше 0 или больше `WEATHER_HUMIDITY_MAX`; УФ `UV_ABC` 0–60 Вт/м²; скорость ветра 0–60 м/с, направление 0–360°. Границы проверяются на полном прогоне (AC-38). Флагов «нет данных» нет: это `null` у параметра.
6. **Пометки качества параметров** — в шапке `parameters`: `measured` (воздух, давление, грунт Curiosity, УФ, ветер), `preliminary` (влажность у обоих — решение владельца 5), `estimate` (грунт Perseverance, только после 17G). На сол не повторяются.
7. **Ключи по марсоходу** (решение владельца 4, вопрос 1): ключ параметра есть в ответе, только если у марсохода есть такой датчик в наших данных. Curiosity — без `wind`; Perseverance — без `uv_wm2` и (до 17G) без `ground_temp_c`. `null` — датчик есть, данных за сол нет (ветер Perseverance вне солов 36–313).
8. **Версии:** у каждого продукта сола берётся старшая версия (`P02` > `P01`; REMS `P1`); `source_version` сола — объект `продукт → версия`, хранится и отдаётся (решение владельца 6).
9. **Инкрементальный разбор** (ADR-3): `weather_files` хранит `(size_bytes, mtime_ns, version)` разобранного файла; сол пересчитывается, если у любого его файла изменились размер или время, появился/пропал файл или версия, либо `weather_daily.parser_revision ≠ WEATHER_PARSER_REVISION`. Строка сводки переписывается только при изменении значений (`updated_at` — AC-15).
10. **Единица работы — сол:** разбор файлов сола (в процессе-исполнителе) → одна транзакция: `weather_files` сола, `weather_daily`, 24 строки `weather_hourly` (`DELETE` + `INSERT`). Прерывание не оставляет полусолов.
11. **Хранение в БД:** кельвины, Па, %, Вт/м², м/с; перевод в °C (`K − 273,15`) и округление — в сервисе чтения (как ×24 у RAD).
12. **Загрузка сырья остаётся вне `app/`:** `scripts/weather_download.py` (`data-engineer`) и ночная задача Windows; приложение только читает локальный каталог `WEATHER_RAW_DIR`. Внешних запросов из `app/` в этапе нет (ADR-4).
13. **Перенос на VPS** — `weather export` → копия → `weather import` (как RAD); таймера на VPS нет: сырья там нет. Обновление: новые файлы ночью → `weather parse` (инкрементально, минуты) → выгрузка и импорт после пополнения источника (раз в квартал–полгода) — с «ок» владельца на каждую выкатку данных (ADR-5).
14. **Ручки:** список всех солов без почасовых (лёгкий), почасовые — по одному солу (решение владельца 3). Только `curiosity` и `perseverance`; остальные → `404 not_found`.
15. **Кэш тела** — как `RadiationBodyCache`: готовые байты ответа по ключу `(rover, sol_min, sol_max)` и отпечатку `(weather_dataset.updated_at, settings 17G)`; `/weather/{sol}` — без кэша тела (запрос по PK, ≈ 25 строк).
16. **Интерфейс:** секция «Weather by sol» в «Data» (решение владельца 8): переключатель марсохода, выбор параметра, суточный график (полоса min–max, линия среднего), метки флагов, инспектор сола с почасовыми графиками. Без новых зависимостей — SVG по образцу графика радиации.

## 4. Текст для `API.md` (если меняется контракт)

Новое, обратно совместимо: две ручки, один код ошибки (`sol_not_found`), строка в «Кэширование»; черновик «Погода отложена» удаляется. Ломающих изменений нет. Текст `credit` — по меткам коллекций (`collection_meda_data_*.xml`, `collection_mslrems_data_calibrated.xml`, лежат в `data/weather_raw/`); точную формулировку `data-engineer` выписывает до переноса в контракт.

```diff
@@ Общие правила v2 — таблица ошибок
-  | 404 | `rover_not_found`, `photo_not_found`, `flight_not_found`, `sample_not_found`, `not_found` | нет сущности или пути |
+  | 404 | `rover_not_found`, `photo_not_found`, `flight_not_found`, `sample_not_found`, `sol_not_found`, `not_found` | нет сущности или пути |

@@ Эндпоинты — после `GET /api/v2/rovers/{rover}/radiation`
+**WeatherSol** (этап 17; суточная сводка погоды по солу; данные NASA PDS: Curiosity — REMS, коллекция `msl_rems:data_calibrated`, продукт `RMD`; Perseverance — MEDA, `mars2020_meda:data_calibrated_env` и `data_derived_env`)
+```json
+{"sol": 100, "earth_date": "2021-06-01", "ls": 52.4,
+ "coverage": 0.61, "hours_covered": 24,
+ "air_temp_c": {"min": -82.4, "max": -18.9, "mean": -53.7},
+ "pressure_pa": {"min": 708.3, "max": 745.1, "mean": 726.0},
+ "humidity_pct": {"min": 0.0, "max": 14.6, "mean": 2.3},
+ "wind": {"speed_mean_ms": 3.4, "gust_ms": 11.8, "direction_mode_deg": 112.5},
+ "flags": [{"code": "humidity_over_100", "parameter": "humidity_pct", "count": 12}],
+ "source_version": {"CAL_ATS": "P02", "CAL_WS": "P03", "DER_PS": "P01", "DER_RHS": "P01"}}
+```
+(Perseverance; значения в примере условные.) У Curiosity вместо `wind` — `"ground_temp_c": {"min": -88.0, "max": 7.5, "mean": -40.2}` и `"uv_wm2": {"max": 19.2, "mean": 5.1}`, `source_version` — `{"RMD": "P1"}`.
+- Ключи параметров зависят от марсохода и одинаковы у всех его солов: Curiosity — `air_temp_c`, `pressure_pa`, `humidity_pct`, `ground_temp_c`, `uv_wm2`; Perseverance — `air_temp_c`, `pressure_pa`, `humidity_pct`, `wind`. **Ветра у Curiosity нет**: ключа `wind` в его ответах нет совсем. Значение `null` — датчик у марсохода есть, но за этот сол годных значений нет (ветер Perseverance есть только на солах 36–313: дальше его в архиве нет).
+- Температуры — °C до 0,1; давление — Па до 0,1; влажность — относительная, % до 0,1; УФ — суммарный поток UV-A+B+C, Вт/м² до 0,01; ветер — м/с до 0,1, направление — градусы, откуда дует, по часовой от севера (центр сектора 22,5°).
+- `min` и `max` — наименьшее и наибольшее годное значение за сол; `mean` — среднее почасовых средних по часам, где были измерения (так неравномерная по времени съёмка не перекашивает среднее). `mean` отдаётся всегда, даже у неполного сола — тогда у сола флаг `partial_coverage`.
+- `wind.speed_mean_ms` — среднее почасовых средних скорости; `wind.gust_ms` — 99-й перцентиль скорости за сол (наибольшее значение не берётся: в данных бывают одиночные выбросы датчика); `wind.direction_mode_deg` — самое частое направление (16 секторов).
+- `coverage` — доля сола, покрытая измерениями температуры воздуха (10-минутные интервалы местного времени), от 0 до 1; `hours_covered` — сколько из 24 часов местного среднего времени (LMST) содержат измерения. Приборы работают сеансами: `coverage` 0,3–0,6 при 24 часах — обычное дело.
+- `flags` — замечания к солу (пустой список — замечаний нет), элемент `{"code", "parameter", "count"}`:
+  | `code` | Когда | Что с данными |
+  |---|---|---|
+  | `partial_coverage` | измерения меньше чем в 18 часах из 24; `parameter` и `count` — `null` | всё отдаётся, `mean` может быть смещён к дню или ночи |
+  | `humidity_over_100` | есть значения влажности выше 100 % (до 200 %); `count` — их число | значения отданы как есть и входят в `min`/`max`/`mean` |
+  | `out_of_range` | в исходных данных есть значения параметра вне физически возможных границ (сбой датчика); `count` — их число | эти значения не учтены |
+  Список кодов может пополняться: незнакомый код клиент должен пропускать.
+- `earth_date` — дата UTC начала сола по метке файла источника; `ls` — солнечная долгота (сезон, 0–360°) на начало сола или `null`, если источник её не даёт.
+- `source_version` — версии продуктов источника, из которых посчитан сол (архив перевыпускает продукты; при новой версии сол пересчитывается).
+
+### `GET /api/v2/rovers/{rover}/weather` (этап 17)
+Только `curiosity` и `perseverance`. Параметры (необязательные): `sol_min`, `sol_max` — диапазон включительно, границы общие; `sol_min > sol_max` или параметр повторён → `422`. Без параметров — вся история. Без пагинации (Curiosity ≈ 4700 солов, ≈ 600 КБ со сжатием gzip). Почасовых значений в списке нет — они в `/weather/{sol}`. Неизвестные параметры игнорируются.
+```json
+{"rover": "perseverance",
+ "credit": "MEDA team (CAB, INTA-CSIC; JPL) / NASA PDS Atmospheres Node, mars2020_meda (текст уточняется по метке коллекции)",
+ "source_url": "https://atmos.nmsu.edu/PDS/data/PDS4/Mars2020/mars2020_meda/",
+ "updated_at": "2026-11-24T02:10:00Z", "source_max_sol": 1859,
+ "parameters": {"air_temp_c": {"unit": "°C", "quality": "measured"},
+                "pressure_pa": {"unit": "Pa", "quality": "measured"},
+                "humidity_pct": {"unit": "%", "quality": "preliminary"},
+                "wind": {"unit": "m/s", "quality": "measured"}},
+ "items": [WeatherSol, ...]}
+```
+- Сортировка `sol ASC`. Пропуски в солах — дни, за которые в архиве нет данных (у источника есть дыры: например, у Curiosity нет солов 2–8).
+- `credit` есть всегда — при использовании данных указывайте его (команды REMS и MEDA; проект неофициальный).
+- `parameters` — какие ключи параметров есть у этого марсохода, их единица и `quality`: `measured` — измерение; `preliminary` — влажность у обоих марсоходов: команды приборов считают её калибровку предварительной; `estimate` — наш расчёт по косвенным данным (зарезервировано). Шапка не зависит от `sol_min`/`sol_max`.
+- `updated_at` — когда сводки марсохода последний раз изменились у нас; `source_max_sol` — наибольший сол в наших сводках. Оба `null` и `items: []`, пока данных нет.
+- Архив пополняется с задержкой 6–8 месяцев, пакетами: последних солов в ответе нет (сравните `source_max_sol` с `max_sol` из `/rovers/{rover}`).
+- `200` · `404 rover_not_found` · `404 not_found` — существующий марсоход без погоды (`opportunity`, `spirit`, `ingenuity`) · `422 validation_error`.
+
+### `GET /api/v2/rovers/{rover}/weather/{sol}` (этап 17)
+`sol` — целое от 0 до 2 147 483 647, иначе `422`. Сол без данных → `404 sol_not_found`. Ответ — WeatherSol и дополнительно:
+```json
+{"rover": "perseverance", "sol": 100, "...": "поля WeatherSol",
+ "hours": [{"hour": 0, "coverage": 0.5, "air_temp_c": -71.2, "pressure_pa": 735.4,
+            "humidity_pct": 5.8, "wind_speed_ms": 2.1, "wind_direction_deg": 157.5},
+           {"hour": 1, "coverage": 0.0, "air_temp_c": null, "pressure_pa": null,
+            "humidity_pct": null, "wind_speed_ms": null, "wind_direction_deg": null}],
+ "source": {"files": [{"product": "CAL_ATS", "version": "P02",
+                       "file": "data_calibrated_env/sol_0090_0179/sol_0100/WE__0100___________CAL_ATS_________________P02.CSV",
+                       "link": "https://atmos.nmsu.edu/PDS/data/PDS4/Mars2020/mars2020_meda/data_calibrated_env/sol_0090_0179/sol_0100/WE__0100___________CAL_ATS_________________P02.CSV"}]}}
+```
+- `hours` — всегда 24 элемента, `hour` 0…23 — час местного среднего солнечного времени (LMST). Значения — средние за час, в тех же единицах и с тем же округлением, что у сола; час без измерений — `null`; `coverage` часа — доля его 10-минутных интервалов с измерениями температуры воздуха.
+- Ключи часов следуют ключам марсохода: Curiosity — `air_temp_c`, `pressure_pa`, `humidity_pct`, `ground_temp_c`, `uv_wm2`; Perseverance — `air_temp_c`, `pressure_pa`, `humidity_pct`, `wind_speed_ms`, `wind_direction_deg` (самое частое направление часа); вне солов 36–313 ветер `null`.
+- `source.files` — файлы архива, из которых посчитан сол (путь от корня набора и ссылка в архиве NASA PDS).
+- `200` · `404 rover_not_found` · `404 not_found` (как у списка) · `404 sol_not_found` · `422 validation_error`.

@@ Кэширование (v1 и v2)
-… `/samples/{number}` и `/rovers/{rover}/radiation` (этап 21) — `public, max-age=3600` …
+… `/samples/{number}`, `/rovers/{rover}/radiation` (этап 21), `/rovers/{rover}/weather` и `/rovers/{rover}/weather/{sol}` (этап 17) — `public, max-age=3600` …

@@ Черновики (уточнить перед своим этапом)
-- **Погода** отложена: живых источников нет (`DATA-SOURCES.md`).
```

**17G, после проверки метода (отдельная правка контракта, тоже на «ок» владельца):** у Perseverance в `parameters` появляется `"ground_temp_c": {"unit": "°C", "quality": "estimate"}`, в солах — `ground_temp_c {min, max, mean}`, в часах — `ground_temp_c`; в описании `estimate` — одна фраза о методе и ссылка на отчёт. Добавление ключа клиентов не ломает.

## 5. ADR

**ADR-1. Как скрыть ветер у Curiosity (и грунт/УФ у Perseverance).**
- А) Ключа нет совсем. Плюсы: буквально «не показывать», клиент не путает «нет датчика» и «нет данных за сол»; тело меньше. Минусы: схема OpenAPI с необязательными ключами, типизированным клиентам нужна проверка наличия.
- Б) Ключ всегда, `null`. Плюсы: одна форма для всех. Минусы: `null` значит то «нет данных сегодня», то «нет датчика вообще» — двусмысленно; на сайте легко нарисовать пустой «Wind».
- В) Разные схемы по марсоходу (`oneOf`). Минусы: сложнее генерация типов, выигрыша против А нет.
- **Рекомендация: А** + шапка `parameters` как перечень ключей марсохода (вопрос 1).

**ADR-2. Где почасовые.**
- А) Отдельная ручка `/weather/{sol}`. Плюсы: список лёгкий (≈ 600 КБ gzip против ≈ 5 МБ с часами), инспектор грузит один сол (≈ 3 КБ). Минусы: второй запрос при выборе сола.
- Б) Параметр `include=hours` у списка. Минусы: тяжёлые ответы, сложнее кэш; `include` в v2 решили не делать (этап 19).
- **Рекомендация: А** (решение владельца 3).

**ADR-3. Признак «файл изменился».**
- А) `(size, mtime_ns)` + версия из имени + ревизия парсера. Плюсы: `stat` 13 тыс. файлов — секунды; перевыпуск продукта даёт новое имя (`P0V`), докачка — новый размер. Минусы: копирование с сохранением времени и тем же размером не заметится — закрывается `--force`.
- Б) sha256 содержимого. Плюсы: точно. Минусы: чтение 52 ГБ на каждый прогон (десятки минут) — дороже самого разбора.
- В) md5 из меток `.xml` (PDS4 `md5_checksum`). Плюсы: точно и без чтения CSV. Минусы: есть не у всех продуктов [не проверено], у REMS `.LBL` другой формат.
- **Рекомендация: А**, `--force` для полного пересчёта.

**ADR-4. Откуда приложение берёт сырьё.**
- А) Только локальный каталог; загрузка — скрипт `data-engineer` (`scripts/weather_download.py`) и ночная задача. Плюсы: загрузчик уже работает и докачивает, сырьё хранится (решение 6), в `app/` нет нового внешнего хоста и HTTP-кода. Минусы: отступление от «HTTP к источникам — только через `app/sources/http.py`» для загрузчика (он вне приложения, как прочие скрипты `scripts/`); на VPS обновиться самостоятельно нельзя.
- Б) Загрузчик в `app/` (`weather fetch`) через общий клиент, как RAD. Плюсы: правило соблюдено, VPS мог бы сам. Минусы: переписывать работающий загрузчик, на VPS нужно ≈ 52 ГБ или потоковый разбор (у MEDA продукты сола — 4 файла, потоковая свёртка сложнее, чем у RAD); отдельное «ок» на запросы с VPS.
- **Рекомендация: А** на этот этап; Б — если владелец захочет обновления без ПК (вопрос 3).

**ADR-5. Обновление данных на VPS.**
- А) Вручную: `weather parse` на ПК → `weather export` → `deploy/vps/weather-import.sh` после пополнения архива (раз в 3–6 месяцев). Плюсы: просто, данные проверяются глазами. Минусы: ручной шаг.
- Б) Автоматическая выгрузка ПК → VPS (scp по таймеру). Минусы: ключ доступа к VPS на ПК в задаче, сбои без присмотра; ради двух обновлений в год не окупается.
- **Рекомендация: А**; в ночной скрипт тимлид добавляет только `weather parse` после загрузки (без выгрузки).

**ADR-6. Параллельный разбор.** Разбор CPU- и дисково-ёмкий (≈ 40 мин одним процессом). `concurrent.futures.ProcessPoolExecutor` из асинхронной команды (`loop.run_in_executor`), исполнитель получает пути файлов сола и возвращает `SolAggregate` (маленький объект), запись в БД — только в основном процессе. Число процессов `WEATHER_PARSE_WORKERS` (по умолчанию `max(1, cpu_count − 2)`). Новых зависимостей нет (stdlib `csv`, без numpy/pandas). **Рекомендация:** так; альтернатива «один процесс» укладывается в AC-39 только на пределе.

**ADR-7. Оценка грунта Perseverance (17G) — метод не выбран.**
- А) Яркостная температура канала, смотрящего на грунт, из `CAL_TIRS`, если она там есть [не проверено]. Плюсы: величина команды прибора, не наша формула. Минусы: нужно скачать `CAL_TIRS` всей истории (объём неизвестен, ≈ 1751 файл).
- Б) Обращение интеграла Планка по полосе 6,5–30 мкм: найти T, при которой `ε·π∫B(λ,T)dλ = F_up − (1−ε)·F_down` по `DER_TIRS` (уже скачан), ε ≈ 0,95–1. Плюсы: данные на диске; объясняет холодные 201 К (наивный Стефан — Больцман берёт весь спектр). Минусы: наш расчёт, выбор ε, ошибка зависит от времени суток.
- В) Не показывать (прежнее решение 2026-11-22).
- **Рекомендация:** проверить А и Б на ≥ 3 солах с опубликованными значениями (AC-49, раздел 9); включать тот, что укладывается в 3 К, предпочтительно А; ни один не укладывается — В. Хранение уже заложено (столбцы `ground_*` в `weather_daily`/`weather_hourly`), миграция для 17G не нужна.

## 6. Модель данных, миграция, поток

### Миграция `20261129_0018_weather.py` (ревизия 0018, `down_revision` 0017)

`weather_files` — разобранные сырые файлы (≈ 7,0 тыс. MEDA + ≈ 4,7 тыс. REMS):
| Столбец | Тип | Примечание |
|---|---|---|
| `rover_id` | `text` FK `rovers.id` | PK (`rover_id`, `path`) |
| `path` | `text` | от корня миссии в `WEATHER_RAW_DIR` (`data_calibrated_env/sol_…/…CSV`, `DATA/SOL_…/…TAB`), разделитель `/` |
| `sol` | `integer` not null | из пути |
| `product` | `text` not null | `CAL_ATS`, `CAL_WS`, `DER_PS`, `DER_RHS`, `RMD` (17G: `DER_TIRS`/`CAL_TIRS`) |
| `version` | `text` not null | `P02`, `P1` |
| `size_bytes` | `bigint` not null | |
| `mtime_ns` | `bigint` not null | |
| `status` | `text` not null, CHECK in (`parsed`, `superseded`, `failed`) | `superseded` — есть старшая версия |
| `rows_total`, `rows_used`, `foreign_sol_rows` | `integer` not null default 0 | диагностика |
| `label_start_time` | `timestamptz` null | `start_date_time` метки |
| `label_ls` | `double precision` null | `start_solar_longitude` метки (MEDA) |
| `error` | `text` null | |
| `parsed_at` | `timestamptz` not null | |

Индекс `ix_weather_files_rover_sol (rover_id, sol)` — группа файлов сола.

`weather_daily` — ответ списка (≈ 6,5 тыс.):
| Столбец | Тип |
|---|---|
| `rover_id`, `sol` | PK (`text` FK, `integer`) |
| `earth_date` | `date` not null |
| `ls` | `double precision` null |
| `coverage` | `real` not null, CHECK 0–1 |
| `hours_covered` | `smallint` not null, CHECK 0–24 |
| `air_min_k`, `air_max_k`, `air_mean_k` | `double precision` null |
| `pressure_min_pa`, `pressure_max_pa`, `pressure_mean_pa` | `double precision` null |
| `humidity_min_pct`, `humidity_max_pct`, `humidity_mean_pct` | `double precision` null |
| `ground_min_k`, `ground_max_k`, `ground_mean_k` | `double precision` null (Curiosity; Perseverance — 17G) |
| `uv_max_wm2`, `uv_mean_wm2` | `double precision` null |
| `wind_mean_ms`, `wind_gust_ms`, `wind_dir_mode_deg`, `wind_dir_r` | `double precision` null (`wind_dir_r` — длина среднего вектора, только диагностика) |
| `n_air`, `n_pressure`, `n_humidity`, `n_ground`, `n_uv`, `n_wind` | `integer` not null default 0 |
| `flags` | `jsonb` not null default `[]` — список `{"code","parameter","count"}` в порядке ответа |
| `source_version` | `jsonb` not null — `{"CAL_ATS": "P02", …}` |
| `parser_revision` | `smallint` not null |
| `updated_at` | `timestamptz` not null |

PK обслуживает оба запроса (`WHERE rover_id = $1 AND sol BETWEEN … ORDER BY sol`, `… AND sol = $2`). Тройки `min/max/mean` — все `null` или все заданы (CHECK по каждому параметру).

`weather_hourly` — 24 строки на сол (≈ 156 тыс., ≈ 15–20 МБ): PK (`rover_id`, `sol`, `hour smallint` CHECK 0–23), FK (`rover_id`, `sol`) → `weather_daily` `ON DELETE CASCADE`; `coverage real`, `air_k`, `pressure_pa`, `humidity_pct`, `ground_k`, `uv_wm2`, `wind_ms`, `wind_dir_deg` (`double precision` null). Пишутся все 24 часа (пустые — `null`, `coverage` 0).

`weather_dataset` — по строке на марсоход: PK `rover_id`; `parsed_at`, `changed_at`, `updated_at` (изменение сводок — отпечаток кэша и `updated_at` ответа), `source_max_sol`, `files_parsed`, `files_failed`, `last_error`, `imported_at`, `import_meta jsonb`.

`downgrade` — `DROP` четырёх таблиц. Данные восстанавливаются повторным `weather parse` (ПК) или импортом выгрузки (VPS).

### Поток

```
data/weather_raw/{meda,rems}/…  (WEATHER_RAW_DIR; качает scripts/weather_download.py ночью)
  → app/services/weather_sync.py: WeatherParser.run(rover, from_sol, to_sol, force, prune)
      скан: os.scandir по каталогам солов (stat, без чтения), выбор старших версий
      план: солы, где (size, mtime_ns, version) ≠ weather_files или parser_revision устарел
      ProcessPoolExecutor: parse_sol_files(rover, sol, paths) →
          app/sources/weather.py: iter_meda_ats / iter_meda_ws / iter_meda_ps / iter_meda_rhs /
                                  iter_rems_rmd (строки → WeatherSample), read_label(xml) → LabelInfo
          → combine_sol(samples, labels) → SolAggregate (сутки, 24 часа, флаги, счётчики)
      основной процесс: одна транзакция на сол (weather_files сола, weather_daily, weather_hourly)
      итог: weather_dataset (updated_at — только если изменилась хоть одна строка)
  → app/repositories/weather.py (SQL)
  → app/services/weather.py: WeatherService (K → °C, округление, ключи по марсоходу, 17G)
  → app/api/v2/weather.py (кэш тела списка) 
```

**Идемпотентность и сбои:** повтор без изменений — 0 файлов; сол — одна транзакция; сбой файла — `status = failed`, сол не меняется, прогон идёт дальше. Блокировка `pg_try_advisory_lock(7_250_005)` на весь `weather parse` и `weather import`; занято → код 2. Коды: 0 — всё разобрано; 1 — есть `failed` или отказ `--prune`/импорта по порогу; 2 — неверный ввод, блокировка, нет `WEATHER_RAW_DIR`; 130 — Ctrl+C (сол в работе откатывается). Файлы `.part` и файлы моложе 60 с (загрузчик пишет) пропускаются.

**Выгрузка и импорт:** `weather export --output PATH [--rover X]` — gzip JSON Lines: `{"t": "meta", "format": "mars-weather-export", "version": 1, "schema_revision": "0018", "exported_at", "counts": {"perseverance": {"daily", "hourly"}, "curiosity": {…}}}`, затем `{"t": "dataset"}`, `{"t": "daily"}`, `{"t": "hourly"}` (все столбцы, время ISO 8601 UTC). ≈ 20 МБ без сжатия, ≈ 3–4 МБ gzip. `weather import --input PATH [--force]` — потоковое чтение с пределом `WEATHER_IMPORT_MAX_BYTES` (256 МиБ распакованного), проверка pydantic-моделями, затем под блокировкой в одной транзакции замена данных марсоходов из файла (`DELETE` + `INSERT`, `updated_at` из файла). `weather_files` не переносится (сырья на VPS нет).

**Настройки (`Settings`, `.env.example`):** `WEATHER_RAW_DIR` (`data/weather_raw`), `WEATHER_PARSE_WORKERS` (0 = `max(1, cpu_count − 2)`), `WEATHER_PARSER_REVISION` — не настройка, константа в коде; `WEATHER_FULL_HOURS` (18), `WEATHER_HUMIDITY_MAX` (200), `WEATHER_MIN_KEEP_RATIO` (0.9), `WEATHER_IMPORT_MAX_BYTES` (256 МиБ), `WEATHER_M20_GROUND_ENABLED` (false), `WEATHER_MEDA_BASE_URL` (`https://atmos.nmsu.edu/PDS/data/PDS4/Mars2020/mars2020_meda/`), `WEATHER_REMS_BASE_URL` (`https://atmos.nmsu.edu/PDS/data/mslrem_1001/`) — только для ссылок `source`, приложение по ним не ходит.

### Порядок выката

1. Разработка и тесты на фикстурах; `designer` — макет до `frontend-dev`.
2. ПК: `uv run python -m app.cli weather parse --rover perseverance`, затем `--rover curiosity` (REMS — сколько скачано); `data-engineer` — AC-37…AC-40 и отчёт.
3. Тимлид добавляет в `.claude/tools/nightly_download.ps1` шаг `weather parse --rover curiosity` после загрузки (до 05:00).
4. Выкатка кода на VPS (`/release`, миграция 0018), `weather export` → копия → `deploy/vps/weather-import.sh` — с «ок» владельца.
5. Когда докачается REMS — повтор шага 4 (импорт заменяет данные марсохода).
6. 17G — после отчёта `data-engineer` и «ок» владельца на метод и правку контракта.

## 7. Задания ролям и общие имена

**Общие имена:**
- `app/sources/weather.py`: `WeatherSample(lmst_s: float, param: str, value: float)` (или колоночный кортеж — на усмотрение `backend-dev`, с тем же смыслом), `LabelInfo(start_time, ls)`, `read_meda_label(path)`, `read_rems_label(path)`, `iter_meda_ats(path, sol)`, `iter_meda_ws(path, sol)`, `iter_meda_ps(path, sol)`, `iter_meda_rhs(path, sol)`, `iter_rems_rmd(path, sol)`, `parse_lmst(text) -> (sol, seconds)`, `RawFile(rover, path, sol, product, version, size, mtime_ns)`, `scan_raw_dir(root, rover) -> list[RawFile]`, `WeatherFormatError`, `PhysicalBounds`, счётчики `RawCounters` (`out_of_range` по параметрам, `humidity_over_100`, `foreign_sol_rows`).
- `app/services/weather_sync.py`: `WeatherParser`, `WeatherRunResult`, `SolAggregate`, `combine_sol(...) -> SolAggregate | None`, `parse_sol_files(...)` (вызывается в исполнителе, без БД), `WEATHER_LOCK_NAMESPACE = 7_250_005`, `WEATHER_PARSER_REVISION = 1`, `FLAG_ORDER = ("partial_coverage", "humidity_over_100", "out_of_range")`.
- `app/services/weather_transfer.py`: `export_weather(session, path, rovers)`, `import_weather(session, path, force)`.
- `app/repositories/weather.py`; `app/services/weather.py` (`WeatherService.list(rover, sol_min, sol_max)`, `WeatherService.get_sol(rover, sol)`, `fingerprint(rover)`); `app/api/v2/weather.py` (`WeatherBodyCache`); схемы `WeatherResponse`, `WeatherSol`, `WeatherSolDetail`, `WeatherHour`, `Stat3`, `UvStats`, `WindStats`, `WeatherFlag`, `ParameterInfo`, `WeatherSourceFile` в `app/api/v2/schemas.py`; ошибка `SolNotFound` (`sol_not_found`) в `app/core/errors.py`.
- ORM: `WeatherFile`, `WeatherDaily`, `WeatherHourly`, `WeatherDataset` (`app/db/models.py`).
- CLI: `weather parse --rover {curiosity,perseverance} [--from-sol N] [--to-sol M] [--force] [--prune]`, `weather status [--rover X]`, `weather export --output PATH [--rover X]`, `weather import --input PATH [--force]`.
- Фикстуры `tests/fixtures/upstream/weather/` — та же раскладка, что в `data/weather_raw/`: MEDA солы 100 (все 4 продукта + `CAL_ATS` в `P01` и `P02`), 400 (без `CAL_WS`), 1230 (короткий, только ночь), 1852 (влажность > 100 %); REMS солы 9 и 30; прореживание — строка в 30 с (MEDA) и 60 с (REMS) с сохранением всех часов, плюс вставленные строки с глитчами (помечаются в `README` фикстур как синтетика); метки `.xml`/`.LBL` целиком.

- `backend-dev`: всё из «Общих имён» в `app/`; миграция `0018` (автогенерацию проверить глазами, CHECK и FK `ON DELETE CASCADE`); настройки и `.env.example`; подкоманда `weather` в `app/cli.py` (docstring с кодами выхода); разбор — чистый stdlib (`csv`, `xml.etree`), потоково, файл не грузится в память целиком; исполнитель без доступа к БД; ручки с `Cache-Control: public, max-age=3600` в `app/api/caching.py`, кэш тела как `RadiationBodyCache`, `_given_once`; у других марсоходов `NotFound`; 17G за флагом `WEATHER_M20_GROUND_ENABLED` (до проверки — без расчёта); вставка текстов `tech-writer` в `/developers` и `/llms.txt`; `docs/openapi.json` и `examples/types.ts`. Прототипы `scripts/weather_*.py` — только как образец алгоритма, код не переносить без проверки.
- `qa-engineer`: тесты AC-1…AC-36 и AC-48; фикстуры по списку выше через `/record-fixtures weather` из `data/weather_raw/` (внешних запросов нет); `mtime` — через `os.utime`; исполнитель в тестах — `WEATHER_PARSE_WORKERS=1` и проверка с 2 процессами (один тест); `migrated_db`. Граничные: пустой каталог, сол только с `DER_PS`, `CAL_WS` без стабильных бумов, `LMST` 23:59:59.9 (час 23), 2 Гц и 1 Гц в одном файле, CRLF и LF, BOM в заголовке, файл 0 байт, `.part` рядом с `.CSV`, импорт поверх непустой базы.
- `data-engineer`: (1) до разработки, без запросов — выписать `credit` из меток коллекций; проверить, есть ли Ls в метке REMS; распределение влажности > 100 % (подтвердить `WEATHER_HUMIDITY_MAX` = 200) и долю солов вне физических границ по каждому параметру на прототипе; помочь `qa-engineer` с прореживанием фикстур; дописать в `DATA-SOURCES.md` правила масок REMS и выбор датчиков MEDA (раздел 1 этой спецификации); (2) после разработки — AC-37…AC-40, отчёт `docs/reports/stage-17-weather-full-run.md`, `weather export`; (3) 17G — по разделу 9, отчёт `docs/reports/stage-17-meda-ground.md` (AC-49).
- `designer`: секция «Weather by sol» в `design/mockups/data.html` и `design/NOTES.md`: переключатель Perseverance / Curiosity, выбор параметра (Air temp, Pressure, Humidity *preliminary*, Wind — только Perseverance, Ground temp и UV — только Curiosity), суточный график (полоса min–max, линия среднего, пунктир/прозрачность у `partial_coverage`), метки флагов с подсказкой, серая зона «not published yet», инспектор сола с 24-часовыми малыми графиками (LMST), таблица последних солов; состояния: загрузка, «not loaded yet», ошибка, сол без данных; светлая и тёмная тема, 360 px. Данные макета — из `docs/reports/stage-17-meda-daily.csv` и `stage-17-rems-daily-sols1-71.csv`.
- `frontend-dev`: секция по макету (хеш `#weather`), запросы — список при раскрытии и смене марсохода, `/weather/{sol}` при выборе сола; параметры — по `parameters` ответа (не по зашитому списку); подписи флагов человеческими словами, незнакомый код — общая подпись; SVG без новых зависимостей; типы из `openapi-ts`.
- `frontend-qa`: AC-41…AC-46 (Vitest — компонентные, Playwright — AC-46).
- `tech-writer`: раздел о погоде для `/developers` и `/llms.txt` (текст в отчёте): источники REMS и MEDA, единицы, `mean` по часам, флаги, `preliminary`, нет ветра у Curiosity и ветер Perseverance только 36–313, задержка архива, `credit`; `README.md` — источник и авторство.
- `devops`: `deploy/vps/weather-import.sh PATH` (по образцу `rad-import.sh`), последняя выгрузка — рядом с бэкапами, строки в `deploy/README.md`; таймера нет.
- `reviewer`: весь этап; `security-reviewer` — новые публичные ручки, разбор файла импорта (пределы размера, проверка строк), чтение локального каталога (пути только внутри `WEATHER_RAW_DIR`, без следования за ссылками наружу).

## 8. Проверка и бюджет

- Команды: `uv run ruff check .`, `uv run pytest -q -n 12`, в `frontend/` — `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`; `uv run python -m app.cli openapi --output docs/openapi.json`; `/migration-check` (0018 вверх-вниз, `weather export`/`import` на временной базе).
- Живая проверка (без запросов): `weather parse --rover perseverance --from-sol 100 --to-sol 110`, повтор (0 файлов), `weather parse --rover curiosity --from-sol 1 --to-sol 71`; `GET /api/v2/rovers/perseverance/weather?sol_min=100&sol_max=110` — воздух −85…0 °C, давление 700–760 Па; `/weather/105` — 24 часа; `/rovers/curiosity/weather/30` — флаг `out_of_range` у `ground_temp_c`; `/ui-check` страницы `/data#weather` (обе темы, 360 px, переключатель, инспектор, «not loaded yet», ошибка).
- Ревью: `reviewer`; `security-reviewer` (новые публичные ручки, импорт файла).
- Ожидаемый расход токенов (порядок величины, по образцу этапа 21): `architect` — этот черновик ≈ 1–1,5 % недельного лимита, правки по ответам владельца ≈ 0,5 %; `data-engineer` — подготовка (1) ≈ 1,5 %, полный прогон и отчёт (2) ≈ 2 %, 17G ≈ 2–3 % (отдельный запуск); `backend-dev` ≈ 1,5–2,5 млн токенов; `qa-engineer` ≈ 1–1,5 млн; `designer` ≈ 0,3 млн; `frontend-dev` ≈ 0,8–1,2 млн; `frontend-qa` ≈ 0,4–0,6 млн; `tech-writer` ≈ 0,1–0,2 млн; `devops` ≈ 0,1 млн; `reviewer` + `security-reviewer` ≈ 0,5–0,8 млн.

## 9. Внешние запросы (для «ок» владельца)

Основная часть этапа — **без запросов**: сырьё уже на диске, докачка REMS идёт ночной задачей по прежнему «ок». Новые запросы — только для проверки метода 17G (`data-engineer`, один запуск, ≈ 14 запросов, ≈ 40 МБ):

| Хост | Запросов | Что и зачем |
|---|---|---|
| `atmos.nmsu.edu` | 1–2 | листинг каталога документов `mars2020_meda` и SIS MEDA (`meda_env_rdr_sis`, PDF): что лежит в `CAL_TIRS` (яркостные температуры каналов, канал грунта), конвенция направления ветра MEDA (нужна и для основной части — `direction_mode_deg`) |
| `atmos.nmsu.edu` | 4 + до 4 | `CAL_TIRS` CSV и метка `.xml` для 4 солов из статьи; если версия в имени не выводится из перечня (`p::1.0` → `P01` [не проверено]) — по одному листингу каталога сола |
| `doi.org` → сайт издателя | 1–2 | одна статья в открытом доступе с опубликованными температурами грунта по TIRS для конкретных солов (кандидат — работа команды MEDA о тепловой инерции и энергобалансе поверхности в Джезеро, JGR Planets, 2023 [из памяти, не проверено]; `data-engineer` сначала ищет DOI в метках и SIS, без лишних запросов) |

Если проверка выберет метод А (ADR-7), полная история `CAL_TIRS` (≈ 1751 файл, объём неизвестен) — отдельный список на «ок» после отчёта.

## 10. Вопросы владельцу

1. **Ветер у Curiosity: убрать ключ или `null`?** Рекомендую убрать ключ совсем (ADR-1): «нет датчика» и «нет данных за сол» не путаются; то же для УФ и грунта у Perseverance. Перечень ключей марсохода — в шапке `parameters`.
2. **Контракт (раздел 4): две ручки `/weather` и `/weather/{sol}`, новый код `sol_not_found`, флаги `partial_coverage`, `humidity_over_100`, `out_of_range`, порог неполного сола 18 часов, влажность до 200 % «как есть».** Рекомендую принять как написано; `credit` впишем по меткам до переноса.
3. **Обновление погоды на VPS:** вручную выгрузкой с ПК после пополнения архива (раз в 3–6 месяцев) или загрузчик в приложении и таймер на VPS? Рекомендую вручную (ADR-4, ADR-5): архив пополняется пакетами, сырьё хранится на ПК.
4. **17G — внешние запросы раздела 9** (≈ 14 запросов, ≈ 40 МБ, `atmos.nmsu.edu` и одна статья): рекомендую «ок»; включение оценки грунта — только после отчёта с отклонением ≤ 3 К и отдельного «ок» на правку контракта.
5. **Погода на странице сола и в карточке снимка** (есть в `PLAN.md`): рекомендую не в этом этапе — только раздел «Data»; ссылку «Weather this sol» на странице сола можно добавить позже одной строкой.

**Ответы владельца (2026-11-23):** «Да, как рекомендовано» — по всем пяти пунктам: 1) ключа `wind` у Curiosity нет (так же УФ и грунт у Perseverance); 2) контракт раздела 4 принят, `credit` вписывается по меткам коллекций до переноса в `API.md`; 3) погода на VPS обновляется вручную выгрузкой с ПК; 4) внешние запросы раздела 9 для 17G одобрены; 5) погода на странице сола и в карточке снимка — не в этом этапе.
