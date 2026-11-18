# Этап 8. Spirit и Opportunity — разовый импорт из PDS (черновик)

Автор: architect, 2026-11-18. Опирается на `DATA-SOURCES.md` → «Spirit и Opportunity (MER, этап 8)» (36 живых запросов data-engineer), `LEGACY-API.md`, `API.md`, код `app/sources/`, `app/services/sync.py`, `backfill.py`, `reconcile.py`. Пометка **[проверить]** — факт об источнике, который ещё не подтверждён и поставлен задачей `data-engineer`.

## 1. Цель и границы

**Входит**
- Скачивание индексов PDS Imaging Node (`edrindex.tab` + `.lbl`, 5 томов × 2 марсохода, ~790 МБ, ~22 запроса) **один раз** в локальный кэш.
- Новый адаптер `app/sources/mer.py`, который читает **только кэш** (в сеть не ходит) и реализует существующий протокол `SourceAdapter`.
- Импорт через существующие `BackfillService` → `SyncService`: та же блокировка, журнал `sync_runs` (режим `backfill`), upsert, `sol_summaries`, `camera_sol_counts`, `sol_source_counts`, пересчёт счётчиков марсохода. Сверка — существующей командой `reconcile`.
- Камеры `FHAZ`, `RHAZ`, `NAVCAM`, `PANCAM`, `ENTRY` (если Descent-индекс разбирается), `MINITES` — пустая; `MI` — по ответу владельца (В2).
- v1 и v2 начинают отдавать снимки Spirit и Opportunity без изменения роутеров и схем.

**Не входит**
- Хранение или проксирование `.img` и конвертация их в JPEG/PNG (полноразмерных картинок нет — только browse-JPEG).
- Обход листингов `browse/` по солам, web.archive.org, данные Mini-TES (Geosciences Node).
- Периодическая синхронизация: архив заморожен (2016/2018), Spirit/Opportunity остаются вне `SYNCABLE_ROVERS`; `POST /admin/sync` для них по-прежнему `422`.
- Миграции схемы: не нужны (см. «Модель данных»).

**Готово, когда**
- `GET /api/v1/rovers/spirit/photos?sol=1` и то же для `opportunity` отдают снимки; `/api/v2/rovers/{spirit|opportunity}/photos?sol=N` — тоже; манифесты v1 и v2 заполнены.
- `reconcile --rover spirit --offline` и `--rover opportunity --offline` — без необъяснённых расхождений; `received` по всем солам равен сумме `ROWS` из `.lbl` (индекс прочитан целиком).
- Повторный `import-mer` ничего не меняет (`photos_upserted` = 0 строк с изменениями, число фото то же).
- Выборка 20–30 browse-URL открывается (`200 image/jpeg`) — В4.

## 2. Модель данных

Схема не меняется. Используются существующие таблицы; новые камеры создаются уже существующим `get_or_create_cameras` (в конце порядка).

### Строка индекса → снимок

Разбор: `.lbl` (PDS3) читается первым — из объектов `COLUMN` берутся `NAME` и `COLUMN_NUMBER`/`START_BYTE`; строки `.tab` разбираются модулем `csv` (табуляция, кавычки), значения — `strip()`. Колонки ищутся **по имени** из метки, а не по позиции: так один парсер годится и для 55-колоночного формата, и для Descent (773 байта), если нужные колонки там есть [проверить].

| Поле `PhotoRecord` | Откуда |
|---|---|
| `source_id` | `PRODUCT_ID` выбранного продукта кадра (ADR-5, ADR-6) |
| `sol` | `PLANET_DAY_NUMBER` |
| `camera_code` | по тому и `INSTRUMENT_ID`, таблица ниже |
| `taken_at` | `START_TIME` (UTC, `normalize.parse_datetime_utc`) |
| `earth_date` | дата `taken_at` по UTC (как у всех марсоходов) |
| `received_at` | `EARTH_RECEIVED_START_TIME` |
| `sample_type` | тип продукта = символы 12–14 `PRODUCT_ID`: `EFF` → `full`, `ESF` → `subframe`, `EDN` → `downsampled` |
| `img_full` | browse-JPEG: `MER_PDS_BASE_URL` + `PATH_NAME` с `/data/` → `/browse/` + `FILE_NAME` (в нижнем регистре, как в индексе) + `.jpg` |
| `img_small/medium/large` | `null` |
| `title`, `caption` | `null` (источник их не даёт; не выдумываем) |
| `credit` | константа по камере (ADR-8) |
| `source_link` | URL `.img`: `MER_PDS_BASE_URL` + `PATH_NAME` + `FILE_NAME` |
| `site`, `drive` | первые два значения `ROVER_MOTION_COUNTER`, если формат `(site,drive,...)` [проверить]; иначе `null` |
| `extra` | `instrument_id`, `eye` (`L`/`R`), `filter_name`, `product_type`, `products` (все `PRODUCT_ID` кадра), `volume_id`, `sequence_id`, `sclk` (`SPACECRAFT_CLOCK_START_COUNT`), `local_true_solar_time`, `lines`, `line_samples`, `frame_type`, `rover_motion_counter`; `None` не сохраняем |

Отбрасываются (считаются в `dropped`): `ETH` (миниатюры), служебные `ERP`, `EHG`, `ERS`, `ECS`, любые неизвестные типы (одно предупреждение в лог на тип со счётчиком, не на строку), строки без обязательных полей, неизвестный инструмент, «вторые» продукты кадра после дедупликации.

### Камеры

| Том | `INSTRUMENT_ID` | `cameras.code` |
|---|---|---|
| `ho` | `FRONT_HAZCAM_LEFT/RIGHT` | `FHAZ` |
| `ho` | `REAR_HAZCAM_LEFT/RIGHT` | `RHAZ` |
| `no` | `NAVCAM_LEFT/RIGHT` | `NAVCAM` |
| `po` | `PANCAM_LEFT/RIGHT` | `PANCAM` |
| `do` | любой [проверить] | `ENTRY` |
| `mo` | MI [проверить точное значение] | `MI` — если владелец согласен (В2) |
| — | — | `MINITES` — остаётся в справочнике, снимков нет |

Левый/правый глаз сохраняется в `extra.eye` и `extra.instrument_id` (ADR-4). В `app/core/cameras.py` добавить `"MI": "Microscopic Imager"` **до** импорта: имя камеры берётся из справочника в момент автосоздания.

### Дедупликация кадра (EFF / ESF / EDN)

Один и тот же кадр встречается несколькими продуктами (полный, уменьшенный, подкадр), а в browse-разрешении они почти неотличимы. Сохраняем **один снимок на кадр**:
- ключ кадра — `PRODUCT_ID` без типа и без двух последних символов (создатель + версия): `pid[:11] + pid[14:25]`, т. е. марсоход, камера, SCLK, site/drive-код, последовательность, глаз, фильтр. Пример: `1N128285132EDN0000P1500L0M1` → `1N128285132` + `0000P1500L0` [проверить на полном индексе: длина 27 у всех строк, у EFF и EDN одного кадра один SCLK];
- выбор: `EFF` > `ESF` > `EDN`, при равенстве — старшая версия (последний символ), затем лексикографически больший `PRODUCT_ID` (детерминированно);
- кадр не пересекает сол и том, поэтому дедупликация идёт внутри сола одного тома — без глобальной памяти.

### Объём
- Строк индекса: Opportunity 548 829, Spirit 301 336. Снимков после фильтра типов — оценка ~250 тыс. и ~135 тыс.; после дедупликации меньше (насколько — посчитает `data-engineer` офлайн). Для БД (у Curiosity уже сотни тысяч строк) это немного.
- Память адаптера: индекс марсохода читается один раз за запуск команды, в памяти остаются только нужные колонки отобранных строк, сгруппированные по солу: ~250 тыс. кортежей, оценка 150–300 МБ (ADR-3). Время разбора — порядка минуты на марсоход [оценка].

### Марсоходы
Сиды не меняются: `status = "complete"`, даты посадки 2004-01-04 / 2004-01-25. `max_sol`, `max_date`, `total_photos` и `full_*` пересчитывает `refresh_rover_stats` в конце каждого запуска; `last_synced_at` = время последнего импорта.

## 3. Контракт

Пути, параметры и формы ответов **не меняются**. Меняются только данные.

### v1 (`LEGACY-API.md`)
- Камеры — точные коды `FHAZ`, `RHAZ`, `NAVCAM`, `PANCAM`, `MINITES`, `ENTRY` (+ `MI`): групп как у Curiosity не нужно, `LEGACY_CAMERA_GROUPS` не трогаем. Порядок — `sort_order` сидов, `MI` — седьмой.
- Только `sample_type = full` (правило 7, без изменений) — после дедупликации это кадры, у которых есть `EFF`. Кадры, переданные на Землю только как `EDN`/`ESF`, в v1 не видны — см. В3.
- `earth_date` — по формуле из сола и даты посадки (`legacy_dates.py`, без изменений). Тестовые векторы для `LEGACY-API.md` (посчитано по формуле):

  | марсоход | sol | earth_date |
  |---|---|---|
  | Spirit | 1 | 2004-01-05 |
  | Spirit | 100 | 2004-04-15 |
  | Spirit | 2208 | 2010-03-21 |
  | Opportunity | 1 | 2004-01-26 |
  | Opportunity | 1000 | 2006-11-17 |
  | Opportunity | 5111 | 2018-06-11 |

  Последние строки совпадают с `max_sol`/`max_date` старого API, которые помнятся как Spirit 2208 / 2010-03-21 и Opportunity 5111 / 2018-06-11 (**[память]**; там же `total_photos` ≈ 124 550 и 198 439 — ориентир для сверки, не требование).
- `img_src` = `img_full` = browse-JPEG PDS (правило «прочие марсоходы — full image», `legacy.py` без изменений).
- Предлагаемые строки в таблицу «Где мы сознательно отличаемся» (вносит тимлид после «ок»):
  - Spirit/Opportunity: `img_src` ведёт на browse-JPEG архива PDS (низкое разрешение) — старые ссылки галереи `mars.nasa.gov/mer/gallery` мертвы;
  - Spirit/Opportunity: состав и число снимков по солам — по архиву PDS, а не по старой галерее; `id` свои;
  - `MINITES` — снимков нет (данных спектрометра в Imaging Node нет), `camera=minites` → пустой список;
  - `MI` — новая камера, в старом API её не было (если В2 = да).

### v2 (`API.md`)
- `GET /api/v2/rovers/spirit` → `status: "complete"`, `mission: "mer"`, реальные счётчики.
- `photos` с фильтрами `camera=FHAZ|RHAZ|NAVCAM|PANCAM|ENTRY|MI`, `sample_type=full|subframe|downsampled` — как у всех.
- Пример снимка:
  ```json
  {"id": 912345, "rover": "spirit", "sol": 1, "earth_date": "2004-01-05",
   "taken_at": "2004-01-05T...Z",
   "camera": {"code": "NAVCAM", "name": "Navigation Camera"},
   "sample_type": "downsampled", "title": null,
   "img_src": "https://planetarydata.jpl.nasa.gov/img/data/mer/mer2no_0xxx/browse/sol0001/edr/2n....img.jpg",
   "images": {"small": null, "medium": null, "large": null, "full": "...img.jpg"},
   "credit": "NASA/JPL-Caltech",
   "source": {"id": "2N128...EDN0000P1500L0M1",
              "link": "https://planetarydata.jpl.nasa.gov/img/data/mer/mer2no_0xxx/data/sol0001/edr/2n....img"}}
  ```
- `/photos/{id}/image?size=*` → `302` на browse-JPEG (откат на `full` уже есть).
- В `API.md` (тимлид) — одна строка: «У Spirit и Opportunity `images.full` — browse-JPEG архива PDS низкого разрешения, `small/medium/large` = `null`, `title` = `null`, `source.link` — исходный PDS-продукт `.img`». Клиентов не ломает.

## 4. Поток данных

```
PDS Imaging Node ──(один раз, ~22 GET, ≤1 rps, только с «ок» владельца)──► MER_CACHE_DIR/
   mer{1,2}{no,ho,po,mo,do}_0xxx/index/edrindex.{tab,lbl}                 (gitignored)
        │  python -m app.cli mer-download [--rover R] [--force]
        ▼
app/sources/mer.py  MerAdapter(rover, cache_dir): читает .lbl → .tab, фильтр типов,
        │           дедупликация кадра, PhotoRecord; latest_sol / source_total / fetch_sols
        ▼
python -m app.cli import-mer --rover R [--from-sol] [--to-sol] [--batch-sols] [--no-resume]
        │  = BackfillService.run(MerAdapter) → SyncService (advisory lock, sync_runs mode=backfill)
        ▼
photos · sol_summaries · camera_sol_counts · sol_source_counts · rovers (refresh_rover_stats)
        ▼
reconcile --rover R --offline   (существующая команда)
```

**`mer-download`** (`app/services/mer_download.py` или функция в `cli.py` поверх `UpstreamClient`):
- список файлов задан в коде (имена томов × `index/edrindex.tab|.lbl`); список томов — константа после проверки `mw`/`om` (В4);
- HTTP только через `UpstreamClient` из `app/sources/http.py`: новый метод `download(url, dest)` — поток в `dest.part`, ретраи из общего клиента, лимит частоты `UPSTREAM_MAX_RPS`, `User-Agent` из настроек; по окончании — переименование;
- идемпотентность: файл пропускается, если он есть и его размер равен `ROWS × ROW_BYTES` из уже скачанной `.lbl` (сверено data-engineer с `content-length`); `--force` — перекачать;
- сеть нужна только этой команде; импорт и тесты работают офлайн.

**`MerAdapter`** (протокол `SourceAdapter` без изменений):
- `latest_sol()` — максимальный сол в индексах марсохода; `source_total()` — сумма `ROWS` из `.lbl` всех томов (объявлено архивом);
- `fetch_sols(from, to)` → `SolStream` по убыванию сола; `SolStats.received` — все строки индекса за сол (включая `ETH`), `dropped` — `received − delivered`, `source_total` = `None` (у архива нет поштучного итога за сол); `stats.complete` и `finish()` — как у MSL;
- индекс разбирается лениво при первом вызове и держится в адаптере до конца команды (один разбор на запуск, а не на пачку);
- нет файла в кэше → понятная ошибка (`MerCacheMissing`, подкласс `UpstreamError`, чтобы синхронизация пометила сбой, а CLI отдал код 2 с подсказкой «run mer-download»).

**`import-mer`** — тонкая обёртка: строит `MerAdapter` и вызывает `BackfillService.run` с теми же опциями и кодами выхода, что `backfill`. Отдельная команда, а не `backfill --rover spirit`: `backfill`/`sync` остаются для синхронизируемых марсоходов, и через `sync --rover spirit` нельзя случайно запустить «инкремент» архива.

**Идемпотентность и сбои:** upsert по `(rover_id, source_id)`; возобновление по покрытию — как у backfill (пачка с загруженными солами пропускается); Ctrl+C → 130, повтор продолжает. Сбой БД на соле → `partial`, сол дозагружается повтором. Блокировка — по марсоходу: импорт Spirit не мешает идущему backfill Curiosity.

**Настройки** (`Settings`, `.env.example`): `MER_PDS_BASE_URL` (по умолчанию `https://planetarydata.jpl.nasa.gov/img/data/mer/`), `MER_CACHE_DIR` (по умолчанию `data/mer`). В `.gitignore` — `data/`; в `.dockerignore` — тоже.

## 5. Решения (ADR)

**ADR-1. Источник.** (а) PDS Imaging Node, индексы; (б) web.archive.org старой галереи; (в) обход листингов PDS по солам. → **(а)**: официальный замороженный архив, полный, машиночитаемый; (б) — дыры и скрапинг HTML; (в) — тысячи запросов и таймауты листинга `browse/`.

**ADR-2. Как брать индекс.** (а) скачать файлы целиком в кэш один раз; (б) читать по сети через `Range` в момент импорта; (в) держать в БД сырые строки (staging-таблица). → **(а)**: ~22 запроса на всю историю, импорт повторяем офлайн, тесты без сети; (б) — повторные сетевые обращения при каждом прогоне; (в) — лишняя таблица и миграция ради разового импорта. Минус (а): ~790 МБ на диске разработчика/сервера — после импорта кэш можно удалить.

**ADR-3. Как встроить.** (а) адаптер `SourceAdapter` поверх кэша + существующие `BackfillService`/`SyncService`; (б) отдельный скрипт, пишущий в БД напрямую; (в) промежуточный компактный файл (`mer-prepare`) + адаптер. → **(а)**: ноль новой логики записи, сводки/счётчики/журнал/сверка/возобновление — уже проверенные; (б) нарушает правило «через синхронизацию» и дублирует пересчёт сводок; (в) экономит память (~100 МБ) ценой ещё одной команды и формата — вернёмся к нему, только если разбор в памяти упрётся в ресурсы.

**ADR-4. Коды камер.** (а) легаси-коды `FHAZ/RHAZ/NAVCAM/PANCAM/ENTRY` в `cameras.code`, глаз — в `extra`; (б) коды `INSTRUMENT_ID` (`NAVCAM_LEFT`, …) + группы для v1, как у Curiosity. → **(а)**: совпадает с сидами и старым API, v1 без новой логики групп, нет пустых «легаси» камер рядом с заполненными; коды `NAVCAM_LEFT/RIGHT` уже заняты по смыслу Perseverance (общий справочник имён). Минус: v2 не фильтрует по глазу — при необходимости позже добавим фильтр по `extra`, не меняя данных.

**ADR-5. Типы продуктов и дубли.** (а) один снимок на кадр, лучший продукт `EFF > ESF > EDN`, остальные — в `extra.products`; (б) все продукты отдельными снимками (как у Curiosity, где full/subframe/downsampled — разные записи). → **(а)**: в browse-разрешении продукты одного кадра выглядят одинаково, (б) удвоит ленты v2 и счётчики без пользы. Риск: ключ кадра неверен (разные кадры склеятся или один не склеится) — проверяет `data-engineer` на полном индексе до импорта; при плохом результате откат на (б) — это только правило в адаптере.

**ADR-6. `source_id`.** (а) `PRODUCT_ID` выбранного продукта; (б) синтетический ключ кадра; (в) `FILE_NAME`. → **(а)**: настоящий идентификатор архива, по нему снимок находится в PDS, совпадает с основой старых имён `-BR.JPG`; архив заморожен, правило выбора фиксировано тестом — значит стабилен. Минус: смена правила выбора потребует удалить снимки MER и импортировать заново (удаление данных — только с «ок» владельца). (б) устойчивее к смене правила, но показывает пользователю несуществующий идентификатор.

**ADR-7. Ссылки на изображение.** `img_full` — browse-JPEG (единственный формат, открывающийся в браузере); `source_link` — `.img` (сам продукт; альтернатива — страница тома, менее точная). Размеры `small/medium/large` = `null`. Бинарники не храним (как везде). Если для какого-то типа продукта browse нет (В4), этот тип проигрывает при выборе продукта кадра; кадр без browse вовсе — пропускается с подсчётом в `dropped`.

**ADR-8. `credit`.** Константы в адаптере: `NASA/JPL-Caltech` (Hazcam, Navcam, Descent), `NASA/JPL-Caltech/Cornell` (Pancam), `NASA/JPL-Caltech/USGS` (MI) — принятые формулировки для MER [проверить по документам тома/фотожурналу]. Альтернатива — везде `NASA/JPL-Caltech`: проще, но теряет соавторов. Правило «авторство всегда сохраняется» выполняется в обоих случаях.

**ADR-9. Что показывает v1.** (а) только `full` (как у остальных марсоходов); (б) для MER — все кадры, раз разрешение всё равно одно (browse). → **(а) по умолчанию**, решение за владельцем после цифр (В3).

**ADR-10. `MINITES` и `ENTRY`.** `MINITES` оставляем пустой камерой (старые клиенты с `camera=minites` получают пустой список, как и раньше для солов без снимков). `ENTRY` импортируем, если Descent-индекс разбирается по меткам (9 строк на марсоход); иначе — пусто и отдельной задачей, этап не блокирует.

## 6. Задания ролям

**data-engineer** (`scripts/`, `docs/DATA-SOURCES.md`, `docs/reports/`)
1. После «ок» владельца (В4) — ~20–30 HEAD к browse-JPEG (по марсоходу × камере × типу `EFF/ESF/EDN` × ранний/поздний сол) и 2 листинга `mw`/`om`; результат — в `DATA-SOURCES.md`.
2. После того как `backend-dev` сделает `mer-download`, запустить её (это и есть разрешённые ~22 запроса) и офлайн по кэшу (скрипт в `scripts/`, без БД): распределение типов продуктов; точные последние солы; формат `ROVER_MOTION_COUNTER`; точный `INSTRUMENT_ID` MI и Descent; есть ли в Descent `PLANET_DAY_NUMBER`/`START_TIME`; проверка ключа кадра (длина `PRODUCT_ID`, совпадение SCLK у EFF/EDN, коллизии одного типа в кадре); число кадров по камерам и по типу лучшего продукта; доля кадров только `EDN`/`ESF`; сравнение числа `full`-кадров с `total_photos` старого API [память] — для В3.
3. Сверить `credit` (ADR-8) по документам тома (`document/`, `catalog/`) — офлайн, если они уже скачаны, иначе 1–2 запроса в рамках В4.
4. После импорта: `reconcile --offline` для обоих марсоходов, отчёт в `docs/reports/`; `EXPLAIN ANALYZE` `photos?sol=N` на самом большом соле MER.
5. Дописать таблицу сопоставления MER в «Сопоставление с нашей моделью».

**backend-dev** (`app/`, `.env.example`, `.gitignore`)
1. `Settings`: `mer_pds_base_url`, `mer_cache_dir`; `.env.example`; `data/` в `.gitignore`.
2. `UpstreamClient.download(url, dest)` — потоковая запись в `.part`, ретраи и лимит частоты общего клиента.
3. `app/sources/mer.py`: разбор PDS3-метки (только `COLUMN`: `NAME`, `COLUMN_NUMBER`/`START_BYTE`/`BYTES`, `ROWS`, `ROW_BYTES`), разбор `.tab`, сопоставление камер, фильтр типов, ключ кадра и выбор продукта, построение URL, `MerAdapter` по протоколу с `SolStream`/`FetchStats`. Чистые функции (`parse_label`, `frame_key`, `pick_product`, `browse_url`) — отдельно, чтобы тестировались без файлов.
4. `cli.py`: `mer-download`, `import-mer` (опции и коды выхода как у `backfill`, код 2 при отсутствии кэша с подсказкой). `SYNCABLE_ROVERS` и `registry.py` не трогать; планировщик и admin не меняются.
5. `cameras.py`: `"MI": "Microscopic Imager"` (если В2 = да).
6. Ничего не запускать против БД `mars`, пока идёт backfill Curiosity, без согласования с тимлидом (импорт другого марсохода блокировкой не конфликтует, но грузит ту же БД).

**qa-engineer** (`tests/`)
- Фикстуры `tests/fixtures/upstream/mer/`: обрезанные `edrindex.lbl` (настоящая метка, `ROWS` исправлен под фикстуру) и `edrindex.tab` на 15–25 строк для `mer2no`, `mer2ho`, `mer1po`, `mer1mo` + Descent-фикстура (если формат подтверждён). Строки — из настоящего индекса после `mer-download` (data-engineer отдаёт выборку), не сочинённые.
- Unit: разбор метки и строк (кавычки, пробелы, пустые значения); сопоставление камер (FRONT/REAR → FHAZ/RHAZ, неизвестный инструмент → отброшен); типы (`ETH`, `ERP`, `EHG`, `ERS`, `ECS`, неизвестный → отброшены, `EFF/ESF/EDN` → `full/subframe/downsampled`); ключ кадра и выбор `EFF > ESF > EDN`, версия `M2 > M1`, детерминизм; URL browse и `.img`; `credit` по камере; `received`/`dropped` по солу; `latest_sol`, `source_total` = сумма `ROWS`; порядок солов по убыванию; кэш отсутствует → `MerCacheMissing`; `download` — `.part`, пропуск готового файла (respx).
- Integration (тестовая БД, без сети): `import-mer` на фикстурах → фото, `sol_summaries`, `camera_sol_counts`, `sol_source_counts`, `rovers.max_sol/full_max_sol/total_photos`; повторный импорт — без дублей и изменений; прерванный импорт возобновляется; `reconcile --offline` — чисто; `MI` создаётся с именем из справочника и `sort_order` 7.
- API: v1 `spirit/photos?sol=1` (только `full`, легаси-`earth_date`, `img_src` = browse), `earth_date=2004-01-05` → сол 1, `camera=minites` → `[]`, `camera=fhaz` регистронезависимо, `latest_photos`, манифест, `rovers/opportunity` (`max_date` по формуле); векторы `earth_date` из раздела 3 — unit к `legacy_dates`; v2 `photos?camera=NAVCAM&sample_type=downsampled`, `photos/{id}` (`extra.eye`, `source.link` на `.img`), `/image?size=small` → `302` на full; `admin/sync` для spirit по-прежнему `422`.

**designer** (`design/`): учесть, что у Spirit/Opportunity только маленькие browse-JPEG (порядка 10 КБ) — не растягивать на всю ширину, подпись «архив PDS, низкое разрешение» и ссылка на исходный продукт. Фронтенд — на своём этапе.

**devops** (этап 9): `data/` в `.dockerignore`; на сервере — либо `mer-download` + `import-mer`, либо перенос таблиц дампом — решить при развёртывании.

**Тимлид** (после «ок»): строки отличий и векторы в `LEGACY-API.md`, одна строка в `API.md`, пункты этапа 8 в `PLAN.md`.

## 7. Вопросы владельцу (с рекомендацией)

- **В1. `MINITES` пустая — нормально?** Рекомендую **да**: данных Mini-TES в Imaging Node нет, это спектрометр, а не камера; клиенты получают пустой список, как и для любых солов без снимков. Строка в отличиях v1.
- **В2. Добавлять Microscopic Imager (`MI`, ~37 тыс. строк индекса)?** Рекомендую **да, в v1 и v2**: так уже сделано для Perseverance («храним все камеры — лишние клиентам не мешают»), а отдельное скрытие в v1 — лишняя логика. Минус: `total_photos` v1 дальше от старого API — это и так другой источник.
- **В3. Что показывать в v1: только полные кадры (`EFF`) или все кадры MER?** Рекомендую **пока только полные** (контракт v1 не меняется) и вернуться после цифр data-engineer: если `full`-кадров заметно меньше, чем `total_photos` старого API (~198 тыс. у Opportunity, ~125 тыс. у Spirit, по памяти), — показывать в v1 все кадры MER как задокументированное отличие (одно условие в `legacy.py`).
- **В4. Внешние запросы (одним «ок»):** ~20–30 HEAD к browse-JPEG + 2 листинга `mw`/`om` + 1–2 документа тома для `credit`; затем ~22 GET на скачивание индексов (~790 МБ, ≤ 1 rps). Рекомендую **да**: всего ~55 запросов на всю историю двух марсоходов, дальше всё офлайн.
- **В5. Только browse-JPEG низкого разрешения + ссылка на `.img` в PDS — устраивает?** Рекомендую **да**: полноразмерных JPEG/PNG в архиве нет, конвертация `.img` и хранение картинок — отдельный этап с диском и трафиком, а старые ссылки галереи всё равно мертвы.
- **В6. Один снимок на кадр (дедупликация EFF/ESF/EDN, ADR-5)?** Рекомендую **да** — при условии, что проверка ключа кадра data-engineer пройдёт; иначе все продукты отдельными снимками, как у Curiosity.
