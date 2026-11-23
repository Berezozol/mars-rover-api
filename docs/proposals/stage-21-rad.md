# Этап 21: Радиация RAD (Curiosity) — черновик архитектора

Редакция 2 (2026-11-23, HEAD `f5b3ec7`, миграции до `0016`). Основа: решения владельца 2026-11-23 (передал тимлид), `docs/reports/stage-21-rad-phase-a.md` (фаза A и дополнение 2026-11-23), `docs/reports/stage-15-rad.md`, `docs/DATA-SOURCES.md` (RAD RDR, фаза A), `design/NOTES.md` (раздел «Data», ответы владельца), `design/mockups/data.html`, `docs/API.md` (общие правила v2, `/samples`, кэширование), `docs/ARCHITECTURE.md`, `app/sources/http.py`, `app/cli.py`, `app/services/{sample_sync,location_sync,alerts}.py`, `deploy/vps/systemd/`, `frontend/src/components/{Layout,BarChart}.tsx`.

Что изменилось против редакции 1: сол — из `START_SOL` файла (вклады «файл × сол» и `rad_file_sols` убраны); дубли одного сола не качаются целиком (проба хвоста по `Range`); длительность — живое время детектора B из `COUNTER_L1` (ранний формат без `OBS_DURATION`); фильтр «низкая скорость BU»; новый ряд — скорость счёта детектора B; загрузка истории — на домашнем ПК владельца, перенос на VPS файлом выгрузки; ежемесячное обновление; интерфейс — раздел «Data» вместо блока на странице Curiosity; CLI — команда `rad` вместо `--source rad`.

## 0. Цель, границы, критерий

**Входит:**
- источник `app/sources/rad.py`: разбор `CHECKSUMS.TXT`, `TIMING_ERRORS.TXT` и потоковый построчный разбор суточного `.TXT` (md5 и хвост на лету, на диск ничего не пишется);
- таблицы `rad_files`, `rad_daily`, `rad_dataset` (миграция `0017`); в БД только суточные сводки и состояние файлов, наблюдений нет;
- CLI `rad sync [--from-sol --to-sol]` (вся история и обновления, возобновление), `rad status`, `rad export --output`, `rad import --input [--force]`;
- ручка v2 `GET /api/v2/rovers/curiosity/radiation?sol_min&sol_max`: доза B и E в мкГр/сут, скорость счёта детектора B (событий/с), покрытие, счётчики наблюдений, флаг ошибок времени;
- фронтенд: пункт шапки «Data» вместо «Samples», страница `/data` (аккордеон: «Rock samples», «Radiation (RAD)»);
- VPS: процедура импорта выгрузки и ежемесячный systemd-таймер `rad sync` (`devops`).

**Не входит:** данные отдельных наблюдений в API и БД; эквивалентная доза (мЗв), коэффициент качества, слова «опасно/безопасно»; нейтральные события (`COUNTER_L2` №6/№7), гистограммы, LET, PHA; перелёт (`DATA/CRUISE/`); EDR-том, метки `.LBL`; радиация в манифесте и карточке снимка; v1; полный вид радиации; секция погоды (её ручки нет — секция скрыта, решение владельца 8); ручка-оглавление `/api/v2/data`.

**Готово, когда:** все AC зелёные; пробная загрузка на базе разработки (солы 10, 1649, 4842–4843) дала строки `rad_daily` с правдоподобными значениями, повтор — 2 условных запроса и 0 скачиваний; страница `/data` показывает радиацию на этих данных. Полная загрузка на ПК, выгрузка и импорт на VPS, включение таймера — после этапа, по разделу 9.

## 1. Факты

Источник (`docs/reports/stage-21-rad-phase-a.md`, `DATA-SOURCES.md`; всего 20 запросов к хосту за этапы 15 и 21):
- Хост `pds-ppi.igpp.ucla.edu`, том `MSL-M-RAD-3-RDR-V1.0`, Apache, `Accept-Ranges: bytes`, ответ `206` на `Range` проверен (этап 15, сол 4710). Поддержка `HEAD` — `[не проверено]`.
- `CHECKSUMS.TXT` (1,1 МБ, LF): строки `md5(32 hex, нижний регистр)␣␣./путь`. Суточных `.TXT` поверхности 5351 (`DATA/SOL_AAAAA_BBBBB/RAD_RDR_YYYY_DDD_HH_MM_SSSS_V00.TXT`), различных солов 4697 (1–4843, сола 0 нет, 146 пробелов), 218 файлов перелёта. Все `V00`. Размеров в перечне нет.
- Несколько файлов на сол: 125 солов, +654 файла (1649–1766 — 759 файлов на 118 солов, до 17 на сол). Проверено на соле 1649: файлы — побайтные копии тела (65 795 430 Б после шапки), отличаются только `CREATION_TIME` и `START_TIME` в `[FILE]`; размер файлов одинаков. Более поздний по имени — более поздний `CREATION_TIME` (2 случая). Остальные 124 сола `[не проверено]`.
- Сол: `START_OBS_MARS` у всех наблюдений файла равен `START_SOL`, местное время «заворачивается»; файл = интервал от своего начала до начала следующего; совпадающих наблюдений у файлов соседних солов нет (1649/1650). Сол = `START_SOL`.
- `START_OBS_UTC` бывает мусором (сол 10: `2013-123` среди `2012-229`; сол 1649: скачки ≈ +6 ч у 21 из 89). Порядок наблюдений в файле не монотонен. `START_TIME` шапки у дублей — время не первого наблюдения.
- Формат: шапка `[FILE]` (`START_SOL`, `START_TIME`, `CREATION_TIME`, `RDR_VERSION`), блоки `[OBSERVATION: NN]`, у дозовых — `[COUNTER_L1]`, `[COUNTER_L2]`, `[DOSIMETRY_TOTAL_DOSE_B]`/`_E` (пустая строка + число, мкГр/ч). Блоки-близнецы `ESD` (2017, 2026) — только `PHA`, дозы нет. PHA — 97 % объёма.
- Ранний формат (2012): нет `OBS_DURATION`; `QC=00000000` без кавычек; `START_SOL=0010`.
- `QC` (SIS 4.2.1.2): бит 0 — «возможны проблемы качества» (на практике у укороченных наблюдений, доза занижена), бит 2 — режим DAN, бит 3 — Solar Event; биты 2 и 3 в 4 разобранных файлах не встретились.
- Живое время: `COUNTER_L1`, строка `BU-x8`, колонка `duration` (960 с в 2012; 909–930 с в 2017–2026 при `OBS_DURATION` 961). Счёт детектора B: `COUNTER_L2`, строка с `entry` = 4 и логикой `BU`, колонка `count` (`count` = `read` везде). Маркеры `-1`/`-2` в этих полях не встречались. Точная раскладка колонок L1/L2 в `DATA-SOURCES.md` не записана — задача `data-engineer` (раздел 7, по локальным образцам, без запросов).
- Скорость счёта BU (QC = 0, живое время): сол 10 — 7,85/с, 1649 — 9,44/с, 4843 — 7,05/с. Наблюдение 13 сола 10: доза 0,22 мкГр/ч при соседях ≈ 9, `QC=00000000`, скорость BU 0,03/с.
- `TIMING_ERRORS.TXT` (75 КБ, CRLF): блоки `Timing Error` и `Date Range Error` со строкой `Found in File:` и имя файла; 98 файлов, из них 19 поверхности (солы 10–174).
- Размеры файлов: 12,2 МБ (сол 10), 42,4 (1650), 65,8 (1649), 71,6 (4843). Скорость 2,4–8 МБ/с; 1 из 5 запросов дополнения оборвался посреди тела (122 с, curl 18).
- Релизы — раз в 4–5 месяцев (последний 2026-08-03, данные до сола 4843). Цитирование и `credit` — раздел 4.

Код:
- `UpstreamClient` (`app/sources/http.py:167`): `get_json`, `download` (на диск), `get_bytes`, `get_conditional`; ретраи и лимит — на экземпляр. Потоковой обработки без диска нет — нужен `stream_into` (раздел 6).
- Блокировки: `7_250_001` (снимки), `7_250_002` (положение), `7_250_003` (образцы). Последняя миграция — `20261122_0016_api_keys.py` (ревизия 0016).
- CLI: у отдельных наборов свои команды (`locations`, `samples`, `mer-download`, `import-mer`). Алерты — `app/services/alerts.py`.
- Фронтенд: `Layout.tsx:113` — пункт «Samples» в шапке; свой SVG `BarChart.tsx`, библиотек графиков нет; Playwright есть (`ef1d444`).

## 2. Критерии приёмки

Каждый критерий проверяем (вход → ожидаемый результат) и имеет номер. `qa-engineer` и `frontend-qa` называют тесты по номерам (`AC-3`) и сдают таблицу «AC → тест»; `reviewer` проверяет, что покрыты все.

Фикстуры — раздел 7 (`tests/fixtures/upstream/rad/`). Числа «мкГр/сут» в API = хранимые мкГр/ч × 24.

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | Фикстура нового формата (сол 4843) целиком в `RadParser.feed()` → шапка (`start_sol` 4843, `start_time`, `creation_time`, `rdr_version`), список дозовых наблюдений (`start_utc_raw`, `qc`, `dose_b`, `dose_e`, `live_s` из `BU-x8`, `bu_count` из `entry` 4), блоки только с PHA пропущены; `md5` и последние 64 КиБ равны md5 и хвосту файла | qa-engineer | unit |
| AC-2 | Та же фикстура кусками по 1 Б, 7 Б и 64 КиБ → результат как в AC-1 (CRLF на стыке кусков) | qa-engineer | unit |
| AC-3 | Фикстура раннего формата (сол 10: нет `OBS_DURATION`, `QC` без кавычек, `START_SOL=0010`) → `start_sol` 10, `live_s` 960 из `COUNTER_L1`, `qc` разобран, наблюдения годные | qa-engineer | unit |
| AC-4 | `QC` `00000001` и `00000101` → `dropped_qc`; `00000100`, `00001000`, `00001100` → наблюдение учтено, `qc_bits` сола содержит эти биты; `0000001`, `ABCDEFGH`, пусто → `dropped_invalid` | qa-engineer | unit |
| AC-5 | Наблюдение без секции дозы E при наличии B, с нечисловой или отрицательной дозой, с `-1`/`-2` в `count` или `duration` строки BU, с `live_s` ≤ 0 или > 7200, без строки `entry` 4 или с логикой не `BU` → `dropped_invalid`; `count` ≠ `read` → наблюдение учтено, предупреждение в логе | qa-engineer | unit |
| AC-6 | `combine_sol`: скорости BU 8,0; 8,1; 7,9; 0,03 событий/с при годном QC → последнее `dropped_low_rate` (< 0,5 × медианы); один годный блок → ничего не отбрасывается | qa-engineer | unit |
| AC-7 | `combine_sol`: (B 6,0 мкГр/ч, live 900 с, count 7200) и (B 8,0, live 300, count 2700) → `dose_b_mean` 6,5, `min` 6,0, `max` 8,0, `live_s` 1200, `bu_count` 9900; в API `mean` 156,00, `min` 144,00, `max` 192,00, `count_rate_b_per_s` 8,250, `coverage` 0,01 (1200 / 88 775,244) | qa-engineer | unit |
| AC-8 | Строка длиннее `RAD_MAX_LINE_BYTES` или тело больше `RAD_MAX_FILE_BYTES` → `RadFormatError`/`UpstreamError`, файл `failed`, `rad_daily` сола не меняется | qa-engineer | unit |
| AC-9 | Фикстура `CHECKSUMS.TXT` → `parse_checksums()` отдаёт только `DATA/SOL_*/RAD_RDR_*.TXT` с солом из имени, путь без `./`; `.LBL`, `.FMT`, `CRUISE/`, `DOCUMENT/` отброшены; битая строка → пропуск и предупреждение | qa-engineer | unit |
| AC-10 | Фикстура `TIMING_ERRORS.TXT` (2 блока `Timing Error`, 1 `Date Range Error`) → множество имён файлов из `Found in File:` обоих видов | qa-engineer | unit |
| AC-11 | `earth_date` = дата UTC `START_TIME` шапки основного файла; наблюдение со скачком UTC (+1 год) не меняет ни `earth_date`, ни сол; `START_TIME` дальше 2 суток от медианы `START_OBS_UTC` учтённых наблюдений → дата самого раннего учтённого наблюдения в пределах ±2 суток от медианы | qa-engineer | unit |
| AC-12 | Пустая база, `rad sync --from-sol 4842 --to-sol 4843`, respx: `CHECKSUMS.TXT`, `TIMING_ERRORS.TXT`, 2 файла → 2 строки `rad_files` (`loaded`), 2 строки `rad_daily`; запросов ровно 4, по одному, между концом одного и началом следующего ≥ `RAD_PAUSE_SECONDS` (поддельные часы) | qa-engineer | integration |
| AC-13 | Повтор AC-12 → 2 условных запроса (`304`), 0 запросов файлов, код 0, `rad_daily.updated_at` не меняется | qa-engineer | integration |
| AC-14 | Сол 1649 с двумя файлами-дублями (фикстуры: шапки разные, тело одно) → полностью скачан только последний по имени, второй — запрос `Range: bytes=-65536` → `206`, размер из `Content-Range` и хвост совпали → второй `duplicate`, `duplicate_of` = первый, сводка — по основному; запросов файлов 2 (1 полный + 1 проба) | qa-engineer | integration |
| AC-15 | Проба получила `200` (сервер не поддержал `Range`): `Content-Length` равен основному → `duplicate` (`dup_check = size`), тело дальше первого куска не читается; длина другая или её нет → файл скачан целиком, наблюдения объединены по ключу (`START_SOL`, `START_OBS_UTC` как в файле): совпавший ключ — значение из файла с более поздним `CREATION_TIME`, новые ключи добавлены; оба файла `loaded` | qa-engineer | integration |
| AC-16 | В `CHECKSUMS.TXT` изменился md5 одного файла → заново обработан только его сол (вся группа файлов сола), `updated_at` этого сола сменился, если сводка изменилась; соседние солы не тронуты | qa-engineer | integration |
| AC-17 | md5 тела ≠ md5 из `CHECKSUMS.TXT` → ещё одна полная попытка; снова не совпал → файл `md5_mismatch`, `rad_daily` сола не меняется, остальные солы грузятся, код 1 | qa-engineer | integration |
| AC-18 | Тело короче `Content-Length` (обрыв) → попытка повторяется целиком, всего до `RAD_FILE_ATTEMPTS` (3); частичный разбор отброшен; успех на 2-й → `loaded` | qa-engineer | integration |
| AC-19 | `503` на все попытки файла → пауза `RAD_COOLDOWN_SECONDS` (поддельный sleep), ещё одна попытка; снова сбой → прогон останавливается, код 3, закоммиченные солы сохранены, следующий запуск начинает с этого сола | qa-engineer | integration |
| AC-20 | Исключение после коммита сола 1 и до сола 2 → повторный запуск пропускает сол 1 и грузит сол 2 | qa-engineer | integration |
| AC-21 | `rad sync` без диапазона: файла больше нет в перечне → его строка `rad_files` удалена, сол пересчитан (нет файлов — строка `rad_daily` удалена); в перечне меньше `RAD_MIN_KEEP_RATIO` известных файлов → ничего не удаляется, ошибка в `rad_dataset.last_error`, код 1; с диапазоном удалений нет | qa-engineer | integration |
| AC-22 | Сол, где ни одно наблюдение не годно → строки `rad_daily` нет (прежняя удалена), файл `empty` | qa-engineer | integration |
| AC-23 | Основной файл сола есть в `TIMING_ERRORS.TXT` → `timing_caveat` `true`; нет → `false`; `TIMING_ERRORS.TXT` не получен → флаги прежних солов не меняются, новые солы `null`, предупреждение в `last_error`, код 0 | qa-engineer | integration |
| AC-24 | Два запуска `rad sync` (или `sync` и `import`) одновременно → второй сразу выходит с кодом 2 (`pg_try_advisory_lock` `7_250_004`), запросов не делает | qa-engineer | integration |
| AC-25 | Редирект или URL файла вне хоста `RAD_BASE_URL` → `UpstreamError`, запроса на чужой хост нет | qa-engineer | integration |
| AC-26 | `rad sync` по фикстурам при запрете `open` в режимах записи → прогон успешен, `UpstreamClient.download` не вызван, файлов на запись не открыто | qa-engineer | integration |
| AC-27 | `rad export --output F` на заполненной базе → gzip JSON Lines: строка `meta` (`format` `mars-rad-export`, `version` 1, `schema_revision`, счётчики, `rad_dataset`), строки `file` и `daily`; `rad import --input F` на пустую базу → три таблицы равны исходным по всем столбцам (включая `updated_at`); повторный импорт → без изменений, код 0 | qa-engineer | integration |
| AC-28 | `rad import`: чужой `format`, `version` ≠ 1, битый gzip, недопустимая строка (сол < 1, доза `NaN`), счётчики `meta` не равны числу строк → код 2, база не изменена; строк `daily` меньше `RAD_MIN_KEEP_RATIO` от имеющихся → отказ, код 1, с `--force` → импорт | qa-engineer | integration |
| AC-29 | После импорта на «чистую» базу `rad sync` при неизменных источниках → 2 условных запроса с `ETag`/`Last-Modified` из выгрузки (`304`), 0 скачиваний | qa-engineer | integration |
| AC-30 | `GET /api/v2/rovers/curiosity/radiation` → 200, `items` по возрастанию `sol`, поля по разделу 4; дозы ×24 до 0,01, `count_rate_b_per_s` до 0,001, `coverage` до 0,01 | qa-engineer | api |
| AC-31 | `sol_min=4800&sol_max=4810` → только солы 4800–4810 включительно; `sol_min > sol_max`, нечисло, `-1`, больше 2 147 483 647, повтор параметра → `422 validation_error` | qa-engineer | api |
| AC-32 | `/rovers/perseverance/radiation` (и `spirit`, `opportunity`, `ingenuity`) → `404 not_found`; `/rovers/pluto/radiation` → `404 rover_not_found`; `CURIOSITY` → 200 | qa-engineer | api |
| AC-33 | Пустые таблицы → 200, `items: []`, `updated_at`, `source_max_sol`, `source_release` = `null`, `credit`, `citation`, `source_url` есть | qa-engineer | api |
| AC-34 | Успешный ответ → `Cache-Control: public, max-age=3600`, `ETag`; повтор с `If-None-Match` → 304; `HEAD` → те же заголовки без тела; ошибка → `no-store` | qa-engineer | api |
| AC-35 | Любой элемент → `source.file` — путь в томе, `source.link` = `RAD_BASE_URL` + путь (абсолютный https); `timing_caveat` — `true`, `false` или `null` из БД | qa-engineer | api |
| AC-36 | `GET /api/v2/rovers/curiosity/manifest` до и после этапа → тело без изменений | qa-engineer | api |
| AC-37 | Миграция `0017` на пустой и на заполненной базе: `upgrade` → `downgrade -1` → `upgrade` без ошибок; после `downgrade` таблиц `rad_*` нет | qa-engineer | integration |
| AC-38 | `uv run python -m app.cli openapi --output docs/openapi.json` → путь `/api/v2/rovers/{rover}/radiation` и схемы `RadiationResponse`, `RadiationSol` есть; `examples/types.ts` обновлён (`openapi-ts`) | qa-engineer | api |
| AC-39 | База после полной загрузки (≈ 4700 солов), `GET /radiation` без параметров, тёплый кэш БД → p50 ≤ 200 мс на сервере, тело gzip ≤ 400 КБ (`/perf-check`) | data-engineer | ручная проверка |
| AC-40 | Любая страница Sol Index → в шапке пункт «Data» (ведёт на `/data`) вместо «Samples»; вкладка «Samples» на странице Perseverance и путь `/rovers/perseverance/samples` работают как раньше | frontend-qa | компонентный |
| AC-41 | `/data` без хеша → открыта первая секция («Rock samples»); `/data#radiation` → открыта и прокручена «Radiation (RAD)»; щелчок по заголовку меняет `aria-expanded` и хеш; секции «Weather» нет | frontend-qa | компонентный |
| AC-42 | API отдаёт 3 сола → в секции радиации: карточка-сводка (охват, дата обновления, отставание от `max_sol` Curiosity текстом, `credit` и источник из ответа), график доз от нуля (E сплошная, B пунктир), серая зона «not published yet» от `source_max_sol` до `max_sol` Curiosity, малый график скорости счёта B, ползунок, таблица последних 3 солов (E, B, скорость, покрытие), ссылка на архив; кнопки «Open full view» нет | frontend-qa | компонентный |
| AC-43 | Ползунок: стрелки двигают на 1 сол, показ — сол, дата, E, B (мкГр/сут), скорость (событий/с); сол без данных → «No data for sol N» | frontend-qa | компонентный |
| AC-44 | `items: []` → «Radiation data not loaded yet»; ошибка v2 или 429 → сообщение и «Try again», секция образцов работает | frontend-qa | компонентный |
| AC-45 | `/data#samples`, API образцов отдаёт 30 образцов → сводка, ступенчатый график «запечатано нарастающим итогом» до 30, ссылка на страницу образцов | frontend-qa | компонентный |
| AC-46 | `/data#radiation` в браузере на 360 px и в тёмной теме → без горизонтальной прокрутки, оси и подписи читаемы (Playwright, скриншоты `/ui-check`) | frontend-qa | e2e |
| AC-47 | `deploy/vps/systemd/mars-rad-sync.{service,timer}` → `systemd-analyze verify` без ошибок, `Persistent=true`, коды 0 — успех; `deploy/vps/rad-import.sh` на выгрузке из AC-27 → данные на месте, повтор без изменений | devops | ручная проверка |

## 3. Решения

1. **Сол = `START_SOL` файла** (ADR-2). Солы по часам внутри файла не делятся, `START_OBS_MARS` не используется.
2. **Один файл на сол** (ADR-3): основной — последний по имени (позже `CREATION_TIME`); остальные файлы сола проверяются пробой хвоста (`Range: bytes=-65536`): тот же размер и хвост — дубль, не качается. Иначе — качается и сливается по ключу наблюдения (`START_SOL`, `START_OBS_UTC` как строка в файле).
3. **Длительность — живое время** `BU-x8` из `COUNTER_L1` для всего: веса средней дозы, скорость счёта, покрытие (ADR-4). `OBS_DURATION` не используется (в 2012 его нет).
4. **Годное наблюдение:** есть обе секции дозы; обе дозы — конечные числа ≥ 0; `QC` — 8 символов `0/1` (с кавычками или без), бит 0 (правый символ) = 0; биты 2 (DAN) и 3 (Solar Event) не исключают; строка `entry` 4 с логикой `BU` и `count` ≥ 0; живое время от 1 до 7200 с, не `-1`/`-2`; скорость BU ≥ 0,5 × медианы скоростей прошедших остальные проверки наблюдений сола (ADR-5). Блок без секций дозы — не дозовое наблюдение (близнец PHA), не считается ни учтённым, ни отброшенным.
5. **Сводка сола:** `mean = Σ(доза × live) / Σ live`, `min`/`max` — по учтённым; `bu_count = Σ count`, скорость = `bu_count / Σ live`; `coverage = min(1, Σ live / 88 775,244)`. Счётчики `used`, `dropped_qc`, `dropped_low_rate`, `dropped_invalid`; `qc_bits` — побитовое ИЛИ `QC` учтённых. Солы без учтённых наблюдений в ответе отсутствуют.
6. **`earth_date`** — дата UTC `START_TIME` шапки основного файла; если он дальше 2 суток от медианы `START_OBS_UTC` учтённых — дата самого раннего учтённого наблюдения в пределах ±2 суток от медианы.
7. **Единицы:** в БД мкГр/ч (как в источнике), в API мкГр/сут (×24) — как в макете; скорость — событий/с живого времени, «сырая», без пересчёта в поток.
8. **Ошибки времени:** `timing_caveat` из `TIMING_ERRORS.TXT` по имени основного файла.
9. **Перечни — только `CHECKSUMS.TXT` и `TIMING_ERRORS.TXT`**, оба условными запросами (`ETag`/`Last-Modified` в `rad_dataset`). Листинги каталогов не читаем.
10. **Вежливость:** один запрос за раз, пауза ≥ `RAD_PAUSE_SECONDS` (2,0; меньше 1,0 не принимается) от конца одного до начала следующего; таймаут бездействия чтения 120 с, предел на файл 30 мин; до `RAD_FILE_ATTEMPTS` (3) полных попыток файла (обрыв, таймаут, 5xx, 429) с паузами 30 и 120 с; затем остывание `RAD_COOLDOWN_SECONDS` (900 с) и одна попытка; снова сбой сервера → остановка, код 3. 4xx (кроме 429), md5, формат — сбой файла, прогон идёт дальше.
11. **Без диска:** тело кусками идёт в парсер, md5 и кольцевой буфер хвоста; в памяти — наблюдения файлов текущего сола (≈ 90 маленьких объектов на файл).
12. **Единица работы — сол:** все файлы сола обрабатываются, затем одна транзакция: `rad_files` группы + `rad_daily` сола. Прерывание не оставляет следов. Возобновление: сол пропускается, если у всех его файлов в `rad_files` тот же md5, что в перечне, и статус не `failed`/`md5_mismatch`.
13. **Ручка только для Curiosity**, у остальных `404 not_found` (как `/flights`, `/samples`). Манифест не трогаем.
14. **История — на домашнем ПК владельца**, в локальную базу разработки; на VPS переносится файлом `rad export` → `rad import` (ADR-6). Обновления — раз в месяц `rad sync` на VPS по systemd-таймеру (ADR-7, вопрос 2).
15. **CLI — своя команда `rad`** с подкомандами (ADR-8), как `locations`/`samples`.
16. **Интерфейс:** раздел «Data» по макету `data.html` с отступлениями: нет кнопки «Open full view» (полного вида нет, решение владельца 5) — вместо неё ссылка на архив (`source_url`); скорость счёта B — малым графиком под графиком доз (своя шкала от нуля) и колонкой в таблице и показе ползунка (вопрос 3); секции погоды нет.

## 4. Текст для `API.md` (если меняется контракт)

Изменение только добавляющее: новая ручка, новые объекты. Существующие ответы (в том числе манифест) не меняются, клиенты не ломаются. В таблицу общих ошибок ничего не добавляется. В сравнении с редакцией 1 убраны `unit`, `rdr_version`, `observed_s`; добавлены `count_rate_b_per_s`, `observations` как объект.

Вставить после раздела `/samples/{number}`:

---

**RadiationSol** (этап 21; суточная сводка прибора RAD марсохода Curiosity; данные NASA PDS, набор `MSL-M-RAD-3-RDR-V1.0`)
```json
{"sol": 1649, "earth_date": "2017-03-26",
 "dose_b_ugy_day": {"mean": 226.51, "min": 205.30, "max": 248.92},
 "dose_e_ugy_day": {"mean": 212.08, "min": 190.44, "max": 236.15},
 "count_rate_b_per_s": 9.437,
 "coverage": 0.86,
 "observations": {"used": 84, "dropped_qc": 5, "dropped_low_rate": 0, "dropped_invalid": 0},
 "timing_caveat": false,
 "source": {"file": "DATA/SOL_01649_01772/RAD_RDR_2017_085_16_28_1649_V00.TXT",
            "link": "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/DATA/SOL_01649_01772/RAD_RDR_2017_085_16_28_1649_V00.TXT"}}
```
(значения в примере условные)
- `dose_b_ugy_day` — мощность поглощённой дозы в кремниевом детекторе B, `dose_e_ugy_day` — в пластиковом детекторе E (по составу ближе к ткани человека), **микрогрей в сутки**. Источник даёт мкГр/ч по каждому наблюдению (≈ 16 минут); здесь они умножены на 24. `mean` — среднее за сол, взвешенное по живому времени наблюдений; `min` и `max` — наименьшее и наибольшее значение отдельного наблюдения, в тех же мкГр/сут. Округление до 0,01. Всплески от солнечных событий не сглаживаются.
- Это поглощённая доза в детекторах прибора. Эквивалентной дозы (зиверты) нет: в данных её нет, свой пересчёт не делаем.
- `count_rate_b_per_s` — сырая скорость счёта детектора B: сколько событий детектор B зарегистрировал за секунду живого времени (счётчик `BU` прибора), среднее за сол, до 0,001. Это не калиброванный поток частиц и не доза: величина полезна для сравнения солов между собой.
- `coverage` — доля сола (88 775 с), покрытая живым временем учтённых наблюдений, от 0 до 1, до 0,01. В 2012 году прибор измерял раз в час (покрытие около 0,3), позже почти непрерывно (около 0,9).
- `observations` — сколько наблюдений сола учтено (`used`) и сколько отброшено: `dropped_qc` — с флагом качества источника, `dropped_low_rate` — скорость счёта детектора B ниже половины медианы сола (прибор почти не считал), `dropped_invalid` — неполные или неразборчивые данные. Солы, где не учтено ни одного наблюдения, в ответе отсутствуют.
- `earth_date` — дата UTC начала суточного файла источника.
- `timing_caveat` — `true`, если файл сола значится в перечне ошибок времени набора (`TIMING_ERRORS.TXT`; время наблюдений может быть смещено, на суточную сводку это влияет слабо); `false` — не значится; `null` — неизвестно.
- `source.file` — путь суточного файла в наборе, `source.link` — его адрес в архиве NASA PDS (12–72 МБ). Если у сола в наборе несколько копий файла, указана самая поздняя.

### `GET /api/v2/rovers/{rover}/radiation` (этап 21)
Только `curiosity`. Параметры (необязательные): `sol_min`, `sol_max` — диапазон включительно, границы общие; `sol_min > sol_max` → `422`. Без параметров — вся история. Без пагинации (около 4700 элементов, ≈ 300 КБ со сжатием gzip). Неизвестные параметры игнорируются.
```json
{"rover": "curiosity",
 "credit": "MSL RAD (SwRI) / NASA PDS Planetary Plasma Interactions Node, MSL-M-RAD-3-RDR-V1.0",
 "citation": "J. Peterson, S. Rafkin, C. Zeitlin, B. Ehresmann, E. Weigle, S. Jeffers, and D.M. Hassler, MSL MARS RADIATION ASSESSMENT DETECTOR RDR V1.0, MSL-M-RAD-3-RDR-V1.0, NASA Planetary Data System, 2013.",
 "source_url": "https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/",
 "updated_at": "2026-12-01T01:42:10Z", "source_max_sol": 4843, "source_release": "2026-08-03",
 "items": [RadiationSol, ...]}
```
- Сортировка `sol ASC`. Пропуски в солах — дни, за которые в наборе нет данных или нет ни одного учтённого наблюдения.
- `credit` и `citation` есть всегда — при использовании данных указывайте их (данные команды RAD, SwRI; проект неофициальный).
- `updated_at` — когда сводки последний раз изменились у нас; `source_max_sol` — наибольший сол в наших сводках; `source_release` — дата последнего изменения перечня файлов набора (`Last-Modified` у `CHECKSUMS.TXT`, дата UTC). Все три `null` и `items: []`, пока данных нет.
- Набор публикуется с задержкой в несколько месяцев и пополняется раз в 4–5 месяцев: последних солов в ответе нет. Мы проверяем его раз в месяц.
- `200` · `404 rover_not_found` · `404 not_found` — существующий марсоход без RAD (`perseverance`, `opportunity`, `spirit`, `ingenuity`) · `422 validation_error`.

В «Кэширование (v1 и v2)» к перечню `public, max-age=3600` добавить `/rovers/{rover}/radiation`.

---

## 5. ADR

**ADR-1. Загрузка — команда приложения или скрипт в `scripts/`.**
- А) `app.cli` + сервис + источник — по слоям, общий клиент, тесты respx, обновления на VPS той же командой.
- Б) Скрипт как `weather_download.py` — быстрее, но дублирует HTTP-политику, вне слоёв, не тестируется.
- **Рекомендация: А.** Загрузка повторяется раз в месяц; правило `CLAUDE.md` «HTTP только через `app/sources/http.py`».

**ADR-2. К какому солу относится наблюдение.**
- А) `START_SOL` файла. Фаза A: у всех наблюдений `START_OBS_MARS` = `START_SOL`, хвоста в чужом файле нет, наблюдения соседних файлов не совпадают.
- Б) `START_OBS_MARS` — то же значение, но в 1649 у части наблюдений `N/A`.
- В) Делить по часам UTC — UTC скачет и бывает мусором.
- **Рекомендация: А.** Таблица вкладов «файл × сол» не нужна.

**ADR-3. Несколько файлов одного сола.**
- А) Качать все и сливать по ключу наблюдения — надёжно, но +654 файла ≈ 40 ГБ и ≈ 10 ч впустую.
- Б) `HEAD` и `Content-Length` — дёшево, но поддержка сервером не проверена, а один размер не доказывает равенство.
- В) Проба `GET` с `Range: bytes=-65536`: `206` даёт полный размер (`Content-Range`) и хвост тела; сравнение с основным файлом. `Range` на этом хосте проверен (этап 15). Запасной ход: ответ `200` → размер из `Content-Length`, соединение закрывается после первого куска; длины нет или другая → полное скачивание и слияние по ключу (А для этого файла).
- Г) Верить, что все копии — дубли, и брать последнюю — проверено только на соле 1649.
- **Рекомендация: В.** ≈ 654 пробы × 64 КиБ ≈ 42 МБ; на непроверенных солах (222, 1028/1029) решает сравнение, а не догадка. Сол 222 (даты в именах 2013-081 и 2013-123) сравнение обработает так же.

**ADR-4. Длительность наблюдения.**
- А) `OBS_DURATION` — нет в 2012; это «стенное» время, а счёт идёт только в живое время (скорость занижается на 5–6 %).
- Б) Живое время `BU-x8` из `COUNTER_L1` — есть во всех форматах, правильный знаменатель скорости.
- **Рекомендация: Б для всего** (веса, скорость, покрытие): одно определение на всю историю; покрытие ниже «стенного» на ≈ 3 %, это оговорено в контракте.

**ADR-5. Фильтр плохих наблюдений.**
- А) Только `QC` бит 0 — пропускает наблюдение 13 сола 10 (доза 0,22 при ≈ 9).
- Б) `QC` бит 0 + скорость BU < 0,5 × медианы сола — независимый признак «прибор почти не считал»; на 4 файлах ловит все аномалии, нормальные не трогает. Порог относительный, всплески SEP (рост скорости) не отбрасываются.
- В) Порог по дозе — отбросил бы реальные провалы дозы.
- **Рекомендация: Б.** Отброшенные видны в `observations`.

**ADR-6. Перенос истории с домашнего ПК на VPS.**
- А) `rad sync` на ПК прямо в базу VPS через SSH-туннель — нет шага переноса, но многочасовой прогон зависит от туннеля, а порт базы прода открывается для ПК.
- Б) `pg_dump --data-only -t 'rad_*'` на ПК и `psql` на VPS — без кода, но без проверок содержимого и защиты от усечения, процедура вне тестов.
- В) `rad export` → файл gzip JSON Lines (≈ 0,5 МБ) → копия на VPS → `rad import`: проверка формата и строк, отказ при усечении, одна транзакция под блокировкой, повтор безопасен; тестируется (AC-27–AC-29). Выгрузка несёт и `rad_files` с md5, и `ETag` перечней — VPS продолжает ежемесячные обновления без перекачки.
- **Рекомендация: В.** Цена — две маленькие подкоманды.

**ADR-7. Где идут ежемесячные обновления.**
- А) `rad sync` на VPS по systemd-таймеру раз в месяц: без нового релиза — 2 запроса (`304`); с релизом — ≈ 130 файлов, ≈ 9 ГБ, ≈ 30 мин. Без ручных шагов.
- Б) На ПК по расписанию Windows + выгрузка и импорт вручную — ПК не всегда включён, ручные шаги забываются.
- В) APScheduler в процессе API — 9 ГБ и полчаса в процессе, который отвечает пользователям; перезапуск API рвёт загрузку.
- **Рекомендация: А** (вопрос 2). Отступление от таблицы решений «планировщик в процессе» — в `ARCHITECTURE.md` после «ок».

**ADR-8. Интерфейс CLI.**
- А) `backfill --source rad` / `sync --source rad` (редакция 1) — ветвление в разборе существующих команд, взаимоисключающие `--rover`/`--source`.
- Б) Своя команда `rad` с подкомандами `sync`, `status`, `export`, `import` — как `locations`, `samples`, `mer-download`.
- **Рекомендация: Б.** Проще разбор и справка; одна логика `rad sync` для истории и обновлений (разница — диапазон и удаление пропавших).

**ADR-9. Докачка по `Range` после обрыва.**
- А) Нет: файл с начала (на ПК трафик не ограничен, на VPS ≈ 9 ГБ раз в 4–5 месяцев).
- Б) Да: хранить состояние парсера и md5 между попытками — сложно.
- **Рекомендация: А.**

**ADR-10. Скорость счёта на экране.**
- А) Третьей линией на графике доз — другие единицы, вторая ось запутает.
- Б) Малый график под графиком доз, своя шкала от нуля, общая ось солов и ползунок; значения в показе ползунка и в таблице.
- В) Только числом в таблице и показе ползунка.
- **Рекомендация: Б** (вопрос 3): владелец взял её как ряд, а ряд виден только на графике; компонент линии тот же.

## 6. Модель данных, миграция, поток

### Миграция `20261128_0017_rad.py` (ревизия 0017, `down_revision` 0016)

`rad_files` — файлы набора (≈ 5351 строка):
| Столбец | Тип | Примечание |
|---|---|---|
| `path` | `text` PK | от корня тома, без `./` |
| `sol` | `integer` not null | из имени (= `START_SOL`, проверяется при разборе) |
| `md5` | `char(32)` null | из перечня; `NULL` при `failed`, `md5_mismatch` (CHECK) |
| `status` | `text` not null, CHECK in (`loaded`, `duplicate`, `empty`, `failed`, `md5_mismatch`) | |
| `role` | `text` null, CHECK in (`primary`, `extra`) | основной файл сола или дополнительный |
| `duplicate_of` | `text` null | путь основного файла, если `duplicate` |
| `dup_check` | `text` null, CHECK in (`tail`, `size`) | как установлен дубль |
| `size_bytes` | `bigint` null | полный размер (из тела или `Content-Range`) |
| `creation_time` | `timestamptz` null | `CREATION_TIME` шапки |
| `rdr_version` | `smallint` null | |
| `blocks`, `dose_obs` | `integer` not null default 0 | блоков всего / с дозой (диагностика) |
| `timing_listed` | `boolean` not null default false | имя есть в `TIMING_ERRORS.TXT` |
| `error` | `text` null | |
| `processed_at` | `timestamptz` not null | |

Индекс `ix_rad_files_sol (sol)` — группа файлов сола при обработке и удалении.

`rad_daily` — ответ ручки (≈ 4700 строк, ≈ 1 МБ):
| Столбец | Тип |
|---|---|
| `sol` | `integer` PK |
| `earth_date` | `date` not null |
| `dose_b_mean`, `dose_b_min`, `dose_b_max`, `dose_e_mean`, `dose_e_min`, `dose_e_max` | `double precision` not null, мкГр/ч |
| `bu_count` | `bigint` not null |
| `live_s` | `double precision` not null, CHECK > 0 |
| `n_used` | `integer` not null, CHECK > 0 |
| `n_dropped_qc`, `n_dropped_low_rate`, `n_dropped_invalid` | `integer` not null default 0 |
| `qc_bits` | `smallint` not null default 0 |
| `timing_caveat` | `boolean` null |
| `source_path` | `text` not null (без FK: файл может уйти из перечня раньше пересчёта; пересчёт — в той же транзакции) |
| `updated_at` | `timestamptz` not null |

PK `sol` обслуживает единственный запрос (`WHERE sol BETWEEN … ORDER BY sol`); столбца `rover_id` нет — набор только Curiosity (как `ingenuity_flights`). Скорость и покрытие считаются при чтении. Строка переписывается только при изменении значений (иначе `updated_at` не меняется — AC-13).

`rad_dataset` — одна строка (PK `id smallint`, CHECK `id = 1`): `checked_at`, `succeeded_at`, `changed_at`, `checksums_etag`, `checksums_last_modified`, `checksums_sha256`, `timing_etag`, `timing_last_modified`, `timing_sha256`, `files_listed`, `last_error`, `imported_at`, `import_meta` (`jsonb`: `exported_at`, хост-источник выгрузки, счётчики).

`downgrade` — `DROP` трёх таблиц; другие таблицы не затрагиваются. После `downgrade` на проде данные возвращаются только повторным импортом выгрузки — `devops` хранит последний файл выгрузки рядом с бэкапами.

### Поток

```
CHECKSUMS.TXT, TIMING_ERRORS.TXT (условные GET, ≤ RAD_LIST_MAX_BYTES)
  → app/sources/rad.py: parse_checksums(), parse_timing_errors()
  → app/services/rad_sync.py: RadLoader.run(from_sol, to_sol)
      план: группы файлов по солу ∩ диапазон − группы, где все md5 совпали и статусы годные
      для каждого сола по возрастанию:
        основной = последний по имени → UpstreamClient.stream_into(url, RadParser) → RadFile
          (md5 сверяется с перечнем, START_SOL = сол из имени)
        остальные → UpstreamClient.probe_tail(url, 65536) → дубль | stream_into + слияние по ключу
        combine_sol(observations) → SolSummary | None
        одна транзакция: upsert rad_files группы, upsert/delete rad_daily сола
        пауза RAD_PAUSE_SECONDS между запросами
      без диапазона: удалить файлы, пропавшие из перечня (порог RAD_MIN_KEEP_RATIO), пересчитать их солы
  → app/repositories/rad.py (SQL)
  → app/services/radiation.py (чтение, ×24, скорость, покрытие, округление) → app/api/v2/radiation.py
```

**Новые методы общего клиента** (`app/sources/http.py`):
- `stream_into(url, sink_factory, *, max_bytes, allowed_origin) -> T`: каждая попытка создаёт свежий приёмник, отдаёт ему `response.aiter_raw()` (`Accept-Encoding: identity`), проверяет `Content-Length` (короче/длиннее → `IncompleteResponse`, ретраится), предел `max_bytes`, хост как в `get_bytes`; возвращает `sink.result()`. Протокол `ChunkSink` (`feed(bytes)`, `result()`).
- `probe_tail(url, n, *, allowed_origin) -> TailProbe(status, total_size, tail)`: `GET` с `Range: bytes=-n`; `206` → размер из `Content-Range`, хвост ≤ n байт; `200` → размер из `Content-Length` (или `None`), `tail = None`, тело не дочитывается.
- Конструктор: необязательные `max_retries`, `min_interval`, `timeout` (по умолчанию прежнее поведение). Для RAD — свой экземпляр: `timeout` — чтение 120 с, общий предел файла `RAD_FILE_DEADLINE_SECONDS` — в `RadLoader` (`asyncio.timeout`).

**Парсер (`RadParser`, `app/sources/rad.py`):** буфер незавершённой строки ≤ `RAD_MAX_LINE_BYTES`; строки по `\n`, `\r` срезается; `[ИМЯ: NN]` — смена секции; `KEY=VALUE` — в `[FILE]` и `[OBSERVATION]` (кавычки снимаются); `COUNTER_L1` — строка `BU-x8`, `COUNTER_L2` — строка `entry` 4 (колонки — по раскладке из `DATA-SOURCES.md`, раздел 7, `data-engineer`); секции дозы — первая непустая строка; прочие секции (PHA, гистограммы, LET) пропускаются без декодирования. md5 (`usedforsecurity=False`) и хвост 64 КиБ — по всем байтам. Выход — `RadFile(start_sol, start_time, creation_time, rdr_version, md5, size, tail, blocks, observations: list[RadObservation])`. Формат NASA знает только `app/sources/rad.py`; `combine_sol` — в сервисе, работает с `RadObservation`.

**Идемпотентность и сбои:** сол — одна транзакция; повтор пересчитывает только изменившееся. Блокировка `pg_try_advisory_lock(7_250_004)` на весь прогон `rad sync` и `rad import`; занято → код 2. Коды `rad sync`: 0 — диапазон обработан; 1 — остались `failed`/`md5_mismatch` или отказ удаления по порогу; 2 — неверный ввод или блокировка; 3 — сервер недоступен после остывания; 130 — Ctrl+C (сол в работе откатывается, повтор продолжит). Неуспешный прогон (`1`, `3`) пишет `rad_dataset.last_error` и шлёт алерт через `app/services/alerts.py` (если Telegram настроен).

**Выгрузка и импорт:** `rad export --output PATH` — gzip JSON Lines: 1-я строка `{"t": "meta", "format": "mars-rad-export", "version": 1, "schema_revision": "0017", "exported_at", "counts": {"file", "daily"}, "dataset": {...}}`, далее `{"t": "file", ...}` и `{"t": "daily", ...}` (все столбцы, время ISO 8601 UTC). `rad import --input PATH [--force]` — читает целиком (предел `RAD_IMPORT_MAX_BYTES`, 64 МиБ распакованного), проверяет pydantic-моделями, затем под блокировкой в одной транзакции заменяет содержимое трёх таблиц (`DELETE` + `INSERT`; `updated_at` — из файла). Запись файла — только в `rad export`.

**Объёмы и время:** 4697 солов; размер файла 12–72 МБ (2012 — около 12 МБ, с 2017 — 42–72 МБ) → история ≈ 230–340 ГБ (точнее не оценить: в перечне нет размеров); при 2,4–8 МБ/с — 9–40 ч плюс паузы ≈ 2,6 ч; обрывы (1 из 5 запросов дополнения) добавят до 20 % трафика. Разбор 72 МБ — доли секунды-секунды. Обновление — ≈ 130 файлов, ≈ 9 ГБ. В БД — ≈ 3 МБ с индексами.

**Настройки (`Settings`, `.env.example`):** `RAD_BASE_URL` (`https://pds-ppi.igpp.ucla.edu/data/MSL-M-RAD-3-RDR-V1.0/`), `RAD_PAUSE_SECONDS` (2.0, ≥ 1.0), `RAD_FILE_ATTEMPTS` (3), `RAD_COOLDOWN_SECONDS` (900), `RAD_READ_TIMEOUT_SECONDS` (120, ≥ 120), `RAD_FILE_DEADLINE_SECONDS` (1800), `RAD_MAX_FILE_BYTES` (256 МиБ), `RAD_MAX_LINE_BYTES` (64 КиБ; максимум в образцах 1250 Б), `RAD_LIST_MAX_BYTES` (16 МиБ), `RAD_MIN_KEEP_RATIO` (0.9), `RAD_LOW_RATE_RATIO` (0.5), `RAD_IMPORT_MAX_BYTES` (64 МиБ). User-Agent — общий `UPSTREAM_USER_AGENT`.

### Порядок выката

1. Разработка и тесты; пробная загрузка на базе разработки (раздел 9, фаза B), сверка `data-engineer`.
2. Полная загрузка на ПК: владелец запускает `uv run python -m app.cli rad sync` в отдельном окне (многочасовой процесс, не агентом); прерывание и повтор безопасны; ход — `rad status`.
3. `data-engineer` проверяет итог (отчёт), `rad export --output data/rad-export-ГГГГММДД.jsonl.gz`.
4. Выкатка кода на VPS (`/release`, миграция 0017 обычным `update.sh`), копия файла на VPS, `deploy/vps/rad-import.sh` — с «ок» владельца.
5. Включение `mars-rad-sync.timer` на VPS.

## 7. Задания ролям и общие имена

**Общие имена:**
- `app/sources/rad.py`: `ChecksumEntry(path, md5, sol)`, `parse_checksums(text) -> list[ChecksumEntry]`, `parse_timing_errors(text) -> set[str]`, `RadObservation(index, start_utc_raw, start_utc, qc_raw, dose_b, dose_e, live_s, bu_count, bu_read)`, `RadFile`, `RadParser` (`feed`, `result`), `RadFormatError`, `parse_qc(raw) -> int | None`.
- `app/sources/http.py`: `ChunkSink`, `UpstreamClient.stream_into(...)`, `UpstreamClient.probe_tail(...)`, `TailProbe`, аргументы конструктора `max_retries`, `min_interval`, `timeout`.
- `app/services/rad_sync.py`: `RadLoader`, `RadRunResult`, `SolSummary`, `combine_sol(observations, low_rate_ratio) -> SolSummary | None`, `RAD_LOCK_NAMESPACE = 7_250_004`, `EXIT_SERVER_DOWN = 3`; `app/services/rad_transfer.py`: `export_rad(session, path)`, `import_rad(session, path, force)`.
- `app/repositories/rad.py`; `app/services/radiation.py` (`RadiationService.list(rover, sol_min, sol_max)`); `app/api/v2/radiation.py`; схемы `RadiationResponse`, `RadiationSol`, `DoseStats`, `ObservationCounts`, `RadiationSource` в `app/api/v2/schemas.py`.
- ORM: `RadFile`, `RadDaily`, `RadDataset` (`app/db/models.py`).
- CLI: `rad sync [--from-sol N] [--to-sol M]`, `rad status`, `rad export --output PATH`, `rad import --input PATH [--force]`.
- Фикстуры `tests/fixtures/upstream/rad/` (обрезанные, md5 в `CHECKSUMS.TXT` — от обрезанных): `CHECKSUMS.TXT` (8–12 строк: солы 10, 1649 ×2, 4842, 4843, сол 222 ×2, `.LBL`, `.FMT`, `CRUISE`, битая строка); `TIMING_ERRORS.TXT` (2 `Timing Error` + 1 `Date Range Error`, одно имя — файл сола 10); `RAD_RDR_2026_080_17_18_4843_V00.TXT` (шапка, наблюдение 0 + близнец, QC=1 пара 22/23, последнее с «завёрткой» времени); `RAD_RDR_2012_228_20_41_0010_V00.TXT` (наблюдения 0, 12 — мусорный UTC, 13 — низкая BU, 25); пара сола 1649 `…085_15_54…` и `…085_16_28…` (разные шапки, одинаковое тело из 2–3 наблюдений, одинаковый размер), одно наблюдение со скачком UTC ≈ +6 ч; файл для 4842 — копия 4843 с правкой `START_SOL` (синтетика помечается в `README` фикстур).

- `backend-dev`: всё из «Общих имён» в `app/`; миграция `0017` (автогенерацию проверить глазами, CHECK-ограничения); настройки и `.env.example`; подкоманда `rad` в `app/cli.py` (docstring с кодами выхода); `stream_into` и `probe_tail` без записи на диск; правило «сол — одна транзакция»; ручка с `Cache-Control: public, max-age=3600` в `app/api/caching.py`, у других марсоходов `NotFound` (как `/flights`); вставка текстов `tech-writer` в `/developers` и `/llms.txt`; `docs/openapi.json` и `examples/types.ts`. Манифест не трогать.
- `qa-engineer`: тесты AC-1…AC-38; фикстуры по списку выше через `/record-fixtures rad` из `data/rad_sample/` (новых запросов нет); поддельные часы и sleep для пауз, попыток и остывания; respx с потоковым телом, обрывом и `206`/`Content-Range`; `migrated_db`. Граничные: пустой перечень, файл без дозовых блоков, сол с одним наблюдением (медиана), все наблюдения отброшены, `START_SOL` ≠ сол имени (`RadFormatError`), повторный ключ наблюдения внутри файла, `CREATION_TIME` равны у двух копий (побеждает больший путь), импорт выгрузки поверх непустой базы.
- `data-engineer`: (1) до разработки, без запросов — дописать в `DATA-SOURCES.md` точную раскладку строк `COUNTER_L1` (`BU-x8`, колонки `duration`, `alive %`) и `COUNTER_L2` (`entry`, `count`, `read`, логика) по `data/rad_sample/`, передать `qa-engineer` обрезанные фикстуры; (2) фаза B — пробная загрузка (раздел 9) на базе разработки, сверка: сол 1649 — 1 полный + 16 проб, все 16 `duplicate`; значения доз и скорости совпадают с отчётом фазы A (B 6,95 мкГр/ч ≈ 167 мкГр/сут у 4843; скорость 7,85 / 9,44 / 7,05); (3) после полной загрузки на ПК — отчёт `docs/reports/stage-21-rad-full-run.md`: солов, файлов по статусам, сколько `extra` оказалось не дублями, распределение `coverage` и отброшенных по годам, солы с `qc_bits` 4/8, выбросы (дневная доза за пределами 0,5–3 × скользящей медианы — список для глаз, не фильтр), трафик и время; AC-39 (`/perf-check`); выгрузка `rad export`.
- `frontend-dev`: пункт шапки «Data» вместо «Samples» (`Layout.tsx`), маршрут `/data`, аккордеон по `design/mockups/data.html` (хеши `#samples`, `#radiation`, `aria-expanded`/`aria-controls`, «Expand all / Collapse all»); секция образцов — по `/rovers/perseverance/samples`; секция радиации — один запрос `/api/v2/rovers/curiosity/radiation` без параметров при раскрытии секции + `/api/v2/rovers/curiosity` (для `max_sol`); линейный SVG без новых зависимостей (по образцу `BarChart.tsx`), ось Y от 0, E сплошная, B пунктир, серая зона «not published yet», малый график скорости B, ползунок «Inspect a sol», таблица 3 последних солов; ссылка на архив вместо «Open full view»; состояния загрузки, «not loaded yet», ошибки; секции погоды нет; типы из `openapi-ts`.
- `frontend-qa`: AC-40…AC-46 (Vitest — компонентные, Playwright — AC-46 и сквозной «шапка → Data → #radiation → ползунок»).
- `tech-writer`: раздел о радиации для `/developers` и `/llms.txt` (текст в отчёте): что такое B, E и скорость счёта, мкГр/сут, нет зивертов, задержка набора, `credit`/`citation`; `README.md` — источник и авторство RAD.
- `devops`: `deploy/vps/systemd/mars-rad-sync.service` (`Type=oneshot`, `User=deploy`, `ExecStart` — `docker compose … run --rm app python -m app.cli rad sync` по образцу `backup.sh`/`lib.sh`, `TimeoutStartSec=6h`) и `mars-rad-sync.timer` (`OnCalendar=*-*-03 02:30`, `Persistent=true`, `RandomizedDelaySec=30min`), включать после импорта; `deploy/vps/rad-import.sh PATH` (копия файла в контейнер или том только для чтения, `rad import`, вывод счётчиков); последняя выгрузка хранится рядом с бэкапами; строки в `deploy/README.md`.
- `reviewer`: весь этап; `security-reviewer` — новая публичная ручка, новый внешний хост (проверка хоста, пределы размера, запрет записи на диск), разбор файла импорта, systemd-unit.

## 8. Проверка и бюджет

- Команды: `uv run ruff check .`, `uv run ruff format .`, `uv run pytest -q -n 12`, `uv run --group tools bandit -r app -ll`, в `frontend/` — `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`; `uv run python -m app.cli openapi --output docs/openapi.json` и `openapi-ts`; `/migration-check`.
- Живая проверка (фаза B, раздел 9): `uv run python -m app.cli rad sync --from-sol 10 --to-sol 10`, затем `--from-sol 1649 --to-sol 1649`, `--from-sol 4842 --to-sol 4843`, повтор последнего (ожидается 2 запроса, `304`); `rad export` и `rad import` на временной базе (`/migration-check`); `GET /api/v2/rovers/curiosity/radiation` — 4 сола, дозы ≈ 150–250 мкГр/сут, покрытие ≈ 0,3 (сол 10) и ≈ 0,9; `/ui-check` страницы `/data#radiation` (светлая и тёмная тема, 360 px, «not loaded yet», ошибка).
- Ревью: `reviewer`; `security-reviewer` — раздел 7.
- Ожидаемый расход токенов: редакция 2 ≈ 1,5 % недельного лимита; `data-engineer` — раскладка и фикстуры ≈ 1 %, фаза B ≈ 1 %, отчёт полной загрузки ≈ 1–2 %; разработка — этап выше среднего (бэкенд с загрузчиком и переносом + новая страница фронтенда).

## 9. Внешние запросы (для «ок» владельца)

Хост `pds-ppi.igpp.ucla.edu`, том `MSL-M-RAD-3-RDR-V1.0`, User-Agent `mars-rover-api/0.1 (personal project)`, строго по одному, пауза ≥ 2 с, ретраи — раздел 3, п. 10.

**Разработка и фикстуры:** нет запросов (образцы фазы A лежат в `data/rad_sample/`).

**Фаза B — пробная загрузка на базе разработки (ПК, `data-engineer`):**
| № | Запуск | Запросов | Объём |
|---|---|---|---|
| B1 | `rad sync --from-sol 10 --to-sol 10` | 2 перечня + 1 файл | ≈ 13 МБ |
| B2 | `rad sync --from-sol 1649 --to-sol 1649` | 2 условных (`304`) + 1 файл + 16 проб `Range` | ≈ 67 МБ |
| B3 | `rad sync --from-sol 4842 --to-sol 4843` | 2 условных + 2 файла | ≈ 143 МБ |
| B4 | повтор B3 | 2 условных | ≈ 0 |

Итого ≈ 28 запросов, ≈ 225 МБ (с ретраями при обрывах — до ≈ 300 МБ).

**Полная история — на домашнем ПК, запускает владелец, после успешной фазы B:** 2 перечня + ≈ 4697 файлов + ≈ 654 пробы ≈ **5350 запросов**, **≈ 230–340 ГБ** (пробы ≈ 42 МБ; файлы-«не дубли» среди дополнительных, если найдутся, — сверх; обрывы — до +20 %), 12–45 ч с паузами, можно частями.

**Ежемесячно на VPS (таймер):** 2 условных запроса (обычно `304`, ≈ 0); раз в 4–5 месяцев новый релиз — ≈ 130 файлов, ≈ 9 ГБ.

## 10. Вопросы владельцу

**Ответы владельца (2026-11-23): все четыре — как рекомендовано.** 1 — контракт принят как есть (перенесён в `API.md`); 2 — обновления раз в месяц на VPS по systemd-таймеру; 3 — скорость счёта B малым графиком под графиком доз; 4 — фаза B одобрена, полная история — после успешной фазы B и отчёта `data-engineer`.

1. **Контракт раздела 4** — `GET /api/v2/rovers/curiosity/radiation`: дозы B и E в мкГр/сут (`mean`/`min`/`max`), `count_rate_b_per_s` с подписью «сырая скорость счёта детектора B», `coverage`, `observations` (учтено и отброшено по причинам), `timing_caveat`, без пагинации, `404 not_found` для других марсоходов. Рекомендую принять как есть.
2. **Где идут ежемесячные обновления.** Рекомендую на VPS по systemd-таймеру (`rad sync`, без релиза — 2 запроса; с релизом ≈ 9 ГБ): ПК нужен только для разовой истории, ручных шагов потом нет. Альтернатива — обновлять на ПК и каждый раз переносить выгрузку вручную.
3. **Скорость счёта B на экране.** Рекомендую малый график под графиком доз (своя шкала от нуля) плюс значение в показе ползунка и колонка в таблице; в макете `data.html` её пока нет, `frontend-dev` добавит по этому описанию без нового макета.
4. **Запросы раздела 9:** фаза B (≈ 28 запросов, ≈ 225 МБ) — «ок» сейчас; полная история на ПК (≈ 5350 запросов, ≈ 230–340 ГБ) — «ок» сейчас с условием «только после успешной фазы B и отчёта `data-engineer`», чтобы не возвращаться за вторым подтверждением. Рекомендую так.
