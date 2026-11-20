# Источники данных

Проверено 2026-11-17. Пометки: **[проверено]** — поля и параметры видели в живом ответе; **[старый код]** — так делал скрапер архивированного API до октября 2025 (`LEGACY-API.md`), проверить фикстурой; **[не проверено]** — известно из сторонних источников.

## Почему не прокси поверх api.nasa.gov

Официальный Mars Rover Photos API (`api.nasa.gov/mars-photos/...`) **архивирован**: на api.nasa.gov висит объявление об этом. Его исходник (`corincerami/mars-photo-api`) переведён в архив 2025-10-08. Поэтому сервис сам забирает данные из фидов raw-изображений JPL, складывает их в Postgres и отдаёт через собственный стабильный API.

Фиды ниже — **неофициальные и недокументированные** (их использует сайт mars.nasa.gov). Отсюда правила:
- весь разбор ответов — только в адаптерах `app/sources/`, остальной код видит нормализованную запись;
- на каждый адаптер есть записанные фикстуры реальных ответов и тесты на них;
- бережная нагрузка: не больше 1–2 запросов в секунду, таймауты, ретраи с паузой, свой `User-Agent` с контактом;
- в каждом ответе нашего API сохраняется поле `credit` (авторство NASA/JPL-Caltech и партнёров).

## Perseverance (Mars 2020)

**База:** `https://mars.nasa.gov/rss/api/?feed=raw_images&category=mars2020&feedtype=json`

### Сводка по последнему солу [проверено]
`...&latest=true` → верхний уровень без массива изображений:

```json
{"latest": "2026-11-06T15:59:53Z", "new_count": 52, "sol_count": 6,
 "latest_sols": [2031], "total": 1055868, "type": "mars2020-latest-images-1.1", "latest_sol": 2031}
```

### Список изображений [проверено]
`...&num=<на странице>&page=<с 0>&order=sol+desc`. Размер страницы по умолчанию 25, **максимум 100**: на `num=200` фид отдаёт `per_page: "100"` [проверено 2026-11-17].

Верхний уровень: `type`, `page` (страница 0 — число `0`, ненулевая — **строка**: `"1"` в `m2020/sol_2029_page1.json`, `"3"` в `m2020/range_1000_page3.json`), `per_page` (**строка**), `total_results` (**число**; без фильтра — 579518 при `total_images` 1055868; в сводке `latest=true` вместо него `total: 1055868`), `total_images` (число), `mission`, `images` (массив). `sample_type` в 80 полученных снимках — только `"Full"`; миниатюр в списке не встретилось.

Поля изображения:
| Поле | Пример / тип |
|---|---|
| `imageid` | `"LRF_2031_0847237865_241EBY_N0930000SCAM01031_0100I4J"` — уникальный ключ |
| `sol` | `2031` (число) |
| `camera.instrument` | `"SUPERCAM_RMI"` |
| `camera.filter_name`, `camera.camera_vector` | часто `"UNK"` |
| `camera.camera_model_component_list`, `camera.camera_position`, `camera.camera_model_type` | строки, `"CAHV"` |
| `image_files.small/medium/large/full_res` | URL: `_320.jpg`, `_800.jpg`, `_1200.jpg`, `.png` |
| `sample_type` | `"Full"` (с заглавной), бывает `"Thumbnail"` |
| `date_taken_utc` | `"2026-11-06T12:16:25.848"` — **без часового пояса**, считать UTC |
| `date_taken_mars` | `"Sol-02031M11:30:55.785"` |
| `date_received` | `"2026-11-06T15:59:53Z"` |
| `title`, `caption`, `credit` | строки |
| `link` | страница изображения на mars.nasa.gov |
| `json_link` | JSON одного изображения: `...&id=<imageid>` |
| `site` | число; `drive` — **строка** (`"0"`) |
| `attitude` | строка-кортеж |
| `extended.mastAz/mastEl/sclk/scaleFactor/xyz/subframeRect/dimension` | всё строки, бывает `"UNK"` |

### Фильтр по солу [проверено]
`&sol=<N>` (как в старом скрапере) **больше не работает — параметр игнорируется**: в `m2020/sol_2029.json` снимки солов 2031 и 2024, `total_results` тот же, что без фильтра.

Работает фильтр по диапазону: `...&order=sol+desc&num=100&page=<k>&condition_2=<from>:sol:gte&condition_3=<to>:sol:lte`. Сол 1000 → `total_results: 385`, все снимки сола 1000, страницы 0–3 по 100/100/100/85 (разбивка по страницам — проба; `page`/`total_results` — в `m2020/range_1000_page0.json`, `range_1000_page3.json`); сол 2024 → 316 (проба, без фикстуры). Страницы идут, пока число полученных снимков меньше `total_results` и страница не пустая. Второго ключа сортировки внутри сола нет, поэтому стабильность порядка между страницами не гарантирована — адаптер убирает повторы `imageid` внутри сола. Стык страниц проверен 2026-11-17: все 4 страницы сола 1000 — 385 снимков, 385 уникальных `imageid`, повторов и потерь нет (проба, без фикстуры). Это одна выборка по старому солу; у свежих солов, куда снимки ещё доходят, сдвиг между страницами возможен — его закрывает перекрытие `SYNC_OVERLAP_SOLS`. Так адаптер и забирает как один сол, так и диапазон солов. В фиде бывают пропуски солов: на 2026-11-17 после 2031 сразу идёт 2024.

## Curiosity (MSL)

**База:** `https://mars.nasa.gov/api/v1/raw_image_items/`

### Список изображений [проверено]
Параметры:
- `order=sol desc,instrument_sort asc,sample_type_sort asc, date_taken desc`
- `per_page=200&page=0` (страницы с 0)
- `condition_1=msl:mission`
- `condition_2=<sol>:sol:in` — фильтр по солу

Верхний уровень: `items` (массив), `more` (bool), `total` (число), `page`, `per_page`. Пример: сол 4327 → `total: 508`.

Поля элемента:
| Поле | Пример / тип |
|---|---|
| `id` | `1387954` (число) — уникальный ключ |
| `imageid` | `"4327MR1065450012201536E01_DXXX"` |
| `sol` | `4327` |
| `instrument` | `"MAST_RIGHT"` |
| `url`, `https_url` | полный URL изображения; уменьшенных версий нет |
| `date_taken` | `"2024-10-08T06:50:23.000Z"` |
| `date_received` | `"2024-10-09T21:30:32.000Z"` |
| `extended.sample_type` | `"subframe"`, `full`, `thumbnail`, `downsampled`, а также `chemcam prc` и `mixed` (`msl/sol_4327_page1.json`, `page2`) — сохраняем как есть в нижнем регистре |
| `extended.contributor`, `extended.bucket`, `extended.url_list` | строки |
| `is_thumbnail` | bool |
| `title`, `description`, `image_credit` | строки |
| `link` | **относительный**: `"/raw_images/1387954"` |
| `site`, `drive`, `xyz`, `attitude`, `camera_*`, `extended.mast_az/mast_el/lmst` | часто `null`; `site`, `drive` — числа или `null` |
| `mission` | `"msl"`; поля с названием марсохода **нет** |
| `created_at`, `updated_at`, `instrument_sort`, `sample_type_sort`, `scale_factor`, `spacecraft_clock`, `subframe_rect`, `extended.filter_name` | служебные поля фида, в документе раньше не было; в модель не переносим |

### Последний сол [проверено]
Аналога `latest=true` нет. `order=sol desc,...&per_page=1&page=0&condition_1=msl:mission` → `items[0].sol` — так делал старый скрапер.

### Пагинация внутри сола [проверено]
В одном соле бывает больше 200 снимков (сол 4327 — 508). Старый скрапер брал только `page=0` и терял остальное. Мы идём по страницам, пока `more == true`.

### Исключить миниатюры [проверено]
`extended=thumbnail::sample_type::noteq` работает: сол 4327 → `total: 280` вместо 508 (проба, без фикстуры; записанные фикстуры сола 4327 — без этого параметра) (508 − 228 миниатюр). Адаптер передаёт этот параметр и всё равно отбрасывает `is_thumbnail: true`. `is_thumbnail` истинно ровно тогда, когда `extended.sample_type == "thumbnail"` (1192 снимка солов 4327 и 5073).

### Камеры (`instrument`) [проверено]
Солы 4327 и 5073: `MAST_LEFT`, `MAST_RIGHT`, `NAV_LEFT_B`, `NAV_RIGHT_B`, `FHAZ_LEFT_B`, `FHAZ_RIGHT_B`, `RHAZ_LEFT_B`, `RHAZ_RIGHT_B`, `CHEMCAM_RMI`, `MAHLI`, `MARDI`. Легаси-коды `FHAZ`, `RHAZ`, `MAST`, `CHEMCAM`, `NAVCAM` в свежем фиде не встречаются — группы для v1 (`LEGACY-API.md`) делаются на этапе 4.

Последний сол на 2026-11-17 — 5073 (`msl/latest.json`); в нём 684 снимка на 4 страницах.

## Сопоставление с нашей моделью

| Наше поле | Perseverance | Curiosity |
|---|---|---|
| `source_id` | `imageid` | `id` (строкой) |
| `sol` | `sol` | `sol` |
| `camera_code` | `camera.instrument` | `instrument` |
| `taken_at` | `date_taken_utc` (+UTC) | `date_taken` |
| `earth_date` | дата из `taken_at` (UTC) | дата из `taken_at` (UTC) |
| `received_at` | `date_received` | `date_received` |
| `sample_type` | `sample_type`, в нижнем регистре | `extended.sample_type` |
| `img_full` | `image_files.full_res` | `https_url` (иначе `url`) |
| `img_small/medium/large` | `image_files.small/medium/large` | `null` |
| `title` | `title` | `title` |
| `caption` | `caption` | `description` |
| `credit` | `credit` | `image_credit` |
| `source_link` | `link` | `https://mars.nasa.gov` + `link` (проверено: 301 → `.../raw_images/<id>/` → 200) |
| `site`, `drive` | `site`, `int(drive)` | `site`, `drive` |
| `extra` (jsonb) | `extended`, `attitude`, `camera` без `instrument` | `imageid`, `extended`, `attitude`, `xyz`, `camera_*` |

Общие правила нормализации:
- `"UNK"`, пустые строки и `null` → `None`; поля с `None` в `extra` не сохраняем;
- миниатюры (`Thumbnail` / `is_thumbnail: true`) не сохраняем;
- числа, пришедшие строками, приводим к числам; если не парсится — `None` и предупреждение в лог, запись не теряем;
- `earth_date` у нас — дата съёмки по UTC. В старом API NASA её вычисляли из сола, поэтому на границе суток значения могут отличаться — это нормально.

## Дополнительные данные (этап 7, сначала проверить)

Проверено 2026-11-18, по одному запросу на адрес.

- **Положение (MMGIS, JPL) — работает у обоих.** Проверено 2026-11-19, **4 GET** (по одному на адрес, без редиректов, 200, `Content-Type: application/json`, без сжатия в ответе), полные файлы сохранены в `data/locations/` (в `.gitignore`), подробности — `docs/reports/stage-11-mmgis.md`. Адрес: `https://mars.nasa.gov/mmgis-maps/{M20|MSL}/Layers/json/{M20|MSL}_{waypoints|traverse}.json`.

  | файл | размер | Last-Modified | объектов | свежесть |
  |---|---|---|---|---|
  | M20_waypoints | 603 126 Б | 2026-10-28 | 708 Point | сол 13…2022 |
  | M20_traverse | 1 723 218 Б | 2026-10-28 | 559 LineString (36 661 точек) | сол 14…2022 |
  | MSL_waypoints | 665 124 Б | 2026-11-13 | 1 389 Point | сол 3…5073 |
  | MSL_traverse | 1 357 951 Б | 2026-11-13 | 1 317 (1 316 LineString + 1 MultiLineString из 5 частей; 27 018 точек) | без сола |

  Есть `ETag` (md5) и `Last-Modified` — условные запросы возможны, [не проверено, сервер мог бы вернуть 304]. Время ответа 0,3–0,6 с.
  - **Общее.** Корень — `FeatureCollection` (`name`: `M20_waypoints_sol2022_all`, `M20_Traverse_Path_tosol2022`, у MSL оба `"SELECT"`); у `M20_traverse` есть `crs` = `urn:ogc:def:crs:OGC:1.3:CRS84` (порядок lon, lat), у остальных `crs` нет. Все координаты **трёхмерные `[lon, lat, elev]`** (3 числа в каждой из точек). Долгота **восточная, 0…180** (M20 77,2…77,5; MSL 137,35…137,45): значений > 180 нет, пересчёт 0…360 не нужен, но защиту оставить. Широта планетоцентрическая/планетографическая — источник не говорит [не проверено]. Свойства: `null`, `"UNK"`, строки-числа в числовых полях — **не встречаются** (все 100% заполнены, числа — `float`/`int`).
  - **Waypoints, свойства.** Общие (оба): `RMC` str (`"{site}_{drive}"`, совпадает с `site`,`drive` у всех), `site` int, `drive` int, `sol` int, `easting`, `northing`, `elev_geoid`, `lon`, `lat`, `roll`, `pitch`, `yaw`, `yaw_rad`, `tilt`, `dist_m`, `dist_total_m`, `dist_km`, `dist_mi` — все float. Только M20: `elev_radii`, `radius` (float; у 96 из 708 значение-заглушка `-9999.0`), `final` str (`y` 554, `m` 132, `i` 22), `Note` str (напр. `Site increment, no motion.`, `End-of-drive localization`), `images` list объектов (панорамы: `name`, `isPanoramic`, `url`, `rows`, `columns`... — **строки**, у всех 708 — непустой список, тяжёлое поле). Только MSL: `drive_type` str (везде `final`). `geometry` — `Point`, `[lon, lat, elev_geoid]`, совпадает со свойствами `lon`/`lat`/`elev_geoid` у всех точек. Единицы: `lat`/`lon` — градусы, `elev_geoid` и `dist_*` — метры (`dist_km`, `dist_mi` — производные), `roll`/`pitch`/`yaw`/`tilt` — градусы, `yaw_rad` — радианы, `easting`/`northing` — м в проекции карты.
  - **Waypoints, ключ.** Пара `(site, drive)` **уникальна** у обоих (0 повторов). Файл идёт по возрастанию `(site, drive)`, `sol` по файлу не убывает (0 нарушений). Несколько точек на сол: до 4 (M20: 708 точек на 559 солов; MSL: 1 389 на 1 382). Пропусков `site` в диапазоне нет (M20 3…93, MSL 2…125). Последняя стоянка: M20 `(93, 0)`, сол 2022; MSL `(125, 2490)`, сол 5073. `dist_total_m` по порядку не монотонна — на неё не полагаться.
  - **Traverse M20.** `LineString`, свойства: `sol` int (у всех 559, по одному объекту на сол с проездом, сол в файле возрастает), `fromRMC`, `toRMC` str (`"site_drive"`; `toRMC` каждого сегмента есть среди стоянок), `length` float (м), `SCLK_START`, `SCLK_END` float. Сегмент — от одной стоянки до следующей. В `z` встречаются мусорные значения (минимум 2,2e-5 при высотах около −2500) — высоту не использовать.
  - **Traverse MSL.** Единственное свойство `Length` float (м); **ни `sol`, ни `site`/`drive` нет** — фильтр по солам для Curiosity невозможен (`sol = null`). Первый объект — `MultiLineString` из 5 частей длиной 935,64 м, остальные — `LineString`. Сумма `Length` = 38,29 км (в waypoints `dist_total_m` = 38,64 км). Все сегменты ≥ 2 точек.
  - **Свежесть относительно снимков.** M20: источник до сола 2022 (файл изменён 2026-10-28), в нашей базе снимки до сола 2031 — стоянка после сола 2022 нет, 728 снимков (сол > 2022) лежат на последней стоянке `(93, 0)`. MSL: источник до 5073, в базе снимки до 5077.
  - **Связь со снимками (dev-база, 2026-11-19).** Perseverance: 579 518 снимков, все с `site`/`drive`; точное совпадение `(site, drive)` — 99,2%; правило «последняя стоянка того же `site` с `drive ≤`» — 99,8%; без совпадения 1 120 (`site` 0, 1, 2 — до первой стоянки в источнике). Curiosity: 397 402 снимка с `site`/`drive` (50% всех); точное — 71,2%; по правилу — 99,4%; без совпадения 2 444 (`site` 0, 1 и `site` 43 с `drive` < 216 — первая стоянка этого `site`). Правило даёт +111 882 снимка Curiosity и +3 267 Perseverance.
- **Погода Perseverance (MEDA)**: `https://mars.nasa.gov/rss/api/?feed=weather&category=mars2020&feedtype=json` отвечает, но отдаёт только 7 солов (1127–1133, апрель 2024), часть значений `--`: фид заморожен. Поля: `sol`, `terrestrial_date`, `ls`, `season`, `min_temp`, `max_temp`, `pressure`, `sunrise`, `sunset`.
- **Погода Curiosity (REMS)**: `...?feed=weather&category=msl&feedtype=json` сломан — вместо данных ошибка WordPress.
- Погода, видимо, доступна только из архивов PDS (Atmospheres Node) с задержкой в месяцы — не проверено.

## Spirit и Opportunity (MER, этап 8)

Разведка 2026-11-18 — **36 HTTP-запросов** (`scripts/probe_mer_sources.py`). Проверка по «ок» владельца (В4) 2026-11-18 — ещё **49 запросов** (`scripts/mer_fetch.py`, счётчик в `data/mer/requests.count`, жёсткий лимит 60): 20 GET — все индексы, 25 HEAD — browse-JPEG, 2 листинга (`mw`, `om`), 2 `aareadme.txt`. Всё ≤ 1 в секунду, `User-Agent: mars-rover-api-dev`, только `planetarydata.jpl.nasa.gov`. Дальше — офлайн по кэшу (`scripts/mer_index_stats.py`, сводка в `data/mer/stats.json`, ~15 с на все 10 файлов). Миссии завершены, новых снимков не будет — нужен разовый импорт.

### Старая галерея — мертва [проверено]
- `https://mars.nasa.gov/mer/gallery/all/` → `302` на `science.nasa.gov/mission/mars-exploration-rovers-spirit-and-opportunity/` (обычная страница миссии, без галереи).
- `.../mer/gallery/all/opportunity.html` → `302` на поиск `science.nasa.gov/mars/resources/?search=spirit+opportunity&types=images` — «0 Search Results».
- Старые адреса картинок тоже мертвы: `.../mer/gallery/all/1/f/001/1F128287243EFF0000P1214L0M1-BR.JPG` → `302` на ту же страницу миссии. Значит, `img_src` из старого API не открываются, и сохранить их «как было» нельзя. Копию в web.archive.org не проверяли [не проверено]: скрапить архив хуже, чем PDS.

### PDS Imaging Node — источник [проверено]
`https://pds-imaging.jpl.nasa.gov/data/mer/` → `302` на **`https://planetarydata.jpl.nasa.gov/img/data/mer/`** (дальше ходим сразу сюда). Обычные листинги каталогов (HTML «Index of»). Тома EDR: `mer{1|2}{xx}_0xxx`, где `1` — Opportunity (MER-1, MERB), `2` — Spirit (MER-2, MERA); `no` — Navcam, `ho` — Hazcam (передние и задние), `po` — Pancam, `mo` — Microscopic Imager, `do` — Descent (EDL). Каталоги `opportunity/`, `spirit/` — те же тома + `*_md5.txt`.

**`mw` и `om` — не EDR** [проверено]: `aareadme.txt` обоих — «MER Operations Data Archives — Camera RDR Mosaics» (операционные мозаики MIPL; индекс там `index/index.tab`, поэтому `edrindex.lbl` → `404`). Для импорта снимков не нужны, в список томов не входят.

Структура тома: `index/`, `data/solNNNN/edr/*.img` (сырые PDS-изображения, в браузере не открываются), `browse/solNNNN/edr/*.img.jpg`, `calib/`, `catalog/`, `document/`. Листинг `browse/` целиком не отдаётся за 60 с — ходить только в конкретный `solNNNN`.

### Индекс `index/edrindex.tab` + `.lbl` [проверено на полных файлах]
Скачаны все 10 пар (20 GET, 2–4 МБ/с, ~4 мин суммарно; 756 МБ в `data/mer/<том>/index/`, `data/` в `.gitignore`). У каждого `.tab` число строк = `ROWS` из `.lbl`, размер = `content-length`.

Формат — **не тот, что обещает метка**:
- Строки разделены `CRLF` и имеют **переменную длину**: у 55-колоночных томов ~910 байт вместо `ROW_BYTES = 933`, у Descent — 774 вместо 773. Поэтому **`ROWS × ROW_BYTES` ≠ размер файла** (Opportunity Pancam: 341 953 830 против настоящих 342 288 096). Целостность проверять по `content-length` и по числу строк = `ROWS`. `START_BYTE`/`BYTES` из метки для нарезки строки не годятся.
- Поля через табуляцию, строки в кавычках и дополнены пробелами, числа без кавычек. `ROVER_MOTION_COUNTER` в метке — одна колонка с `ITEMS = 5`, а в файле это **5 полей через табуляцию**: в строке 59 полей при `COLUMNS = 55` (Descent: 52 при 48). Если раскрыть `ITEMS` в отдельные поля, сопоставление по именам из метки работает во всех 10 файлах (0 строк с другим числом полей).
- `ROVER_MOTION_COUNTER` — всегда 5 целых (`2,0,0,1,0`, `2,0,2,1669,128`): первые два — site и drive.
- Индекс почти отсортирован по солу (по одному нарушению порядка на марсоход), на порядок не опираться — группировать по солу.
- **Повторённые строки:** у Opportunity **12 `PRODUCT_ID` встречаются дважды** (Navcam 3, Hazcam 2, Pancam 7; у Spirit — нет). Строки отличаются только `PRODUCT_CREATION_TIME` (2014/2015) и `TELEMETRY_SOURCE_NAME` (`..._Partial-35.pdat` / `-38`), путь и имя файла одни и те же — перевыпуск того же продукта. Брать строку с более поздним `PRODUCT_CREATION_TIME`; один `source_id` дважды в пачке upsert недопустим.

**Descent (`do`) — другой набор колонок** [проверено]: 48 колонок, **нет `PATH_NAME` и `FILE_NAME`**; есть `PRODUCT_ID`, `PLANET_DAY_NUMBER` (= 1 у всех), `START_TIME`, `INSTRUMENT_ID = DESCAM`, `SPACECRAFT_CLOCK_START_COUNT`, `ROVER_MOTION_COUNTER`. 9 строк на марсоход: 3 `EDN` (256×1024, `4X1SUMMATION_FRAME`), 3 `ERP`, 3 `ETH`. Путь — `/<том>/data/sol0001/edr/` + `PRODUCT_ID` в нижнем регистре + `.img`; browse по этому правилу — `200` у обоих марсоходов.

**Объём и типы продуктов** (полные индексы; тип — символы 12–14 `PRODUCT_ID`; длина `PRODUCT_ID` = 27 во всех 850 165 строках):

| Том | строк | `ETH` | `EFF` | `ESF` | `EDN` | служебные `ERP/EHG/ERS/ECS` | солы |
|---|---|---|---|---|---|---|---|
| `mer1no` | 119 929 | 70 948 | 24 048 | 836 | 23 506 | 591 | 1–5086 |
| `mer1ho` | 37 616 | 18 846 | 12 260 | 3 839 | 2 592 | 79 | 1–5104 |
| `mer1po` | 366 510 | 188 678 | 47 555 | 74 253 | 17 168 | 38 856 | 1–5111 |
| `mer1mo` | 24 765 | 13 011 | 11 448 | 94 | 27 | 185 | 2–5104 |
| `mer1do` | 9 | 3 | — | — | 3 | 3 | 1 |
| `mer2no` | 59 821 | 29 572 | 10 286 | 5 090 | 12 042 | 2 831 | 1–2186 |
| `mer2ho` | 25 629 | 14 716 | 5 274 | 908 | 4 601 | 130 | 1–2174 |
| `mer2po` | 203 550 | 102 050 | 29 449 | 39 440 | 11 682 | 20 929 | 1–2209 |
| `mer2mo` | 12 327 | 6 153 | 5 933 | 120 | — | 121 | 2–2174 |
| `mer2do` | 9 | 3 | — | — | 3 | 3 | 1 |

Последний сол: Opportunity **5111** (Pancam; совпадает со старым `max_sol`), Spirit **2209** (2 подкадра Pancam; старый API помнится как 2208 [память]). Солов со снимками: Opportunity 4842, Spirit 2122. Самый большой сол: Opportunity 60 (346 продуктов-снимков), Spirit 433 (415).

`INSTRUMENT_ID`: `NAVCAM_LEFT/RIGHT`, `FRONT_HAZCAM_LEFT/RIGHT`, `REAR_HAZCAM_LEFT/RIGHT`, `PANCAM_LEFT/RIGHT`, **`MI`**, **`DESCAM`**.

### Ключ кадра и дедупликация [проверено на полных индексах]
Ключ `pid[:11] + pid[14:25]` (без типа, создателя и версии), группировка внутри тома:
- **Разные кадры не склеиваются:** ни в одной группе нет разных солов, инструментов, `SEQUENCE_ID`, `START_TIME` или полного `SPACECRAFT_CLOCK_START_COUNT` (с дробной частью).
- **Копии не теряются:** ни один набор «инструмент + полный SCLK + фильтр» не распался на два ключа.
- **Но склеивать почти нечего:** `EFF`, `ESF` и `EDN` одного кадра в индексе практически не встречаются вместе — у MER кадр передан одним продуктом. На 850 тыс. строк — 3 группы `ESF+EDN` (Pancam: 2 у Opportunity, 1 у Spirit) и 12 групп «тот же `PRODUCT_ID` дважды» (см. выше). Разных версий (`M1`/`M2`) одного кадра нет. Кадры только `EDN`/`ESF` — отдельные съёмки в уменьшенном виде или подкадром, а не копии полных.

Кадров после дедупликации (по типу выбранного продукта):

| Камера | Opportunity: кадров / `EFF` / `ESF` / `EDN` | Spirit: кадров / `EFF` / `ESF` / `EDN` |
|---|---|---|
| `NAVCAM` | 48 387 / 24 046 / 836 / 23 505 | 27 418 / 10 286 / 5 090 / 12 042 |
| `FHAZ` + `RHAZ` | 12 740 + 5 949 / 12 259 / 3 838 / 2 592 | 7 432 + 3 351 / 5 274 / 908 / 4 601 |
| из них `EFF` FHAZ / RHAZ | 7 761 / 4 498 | 3 378 / 1 896 |
| `PANCAM` | 138 967 / 47 552 / 74 249 / 17 166 | 80 570 / 29 449 / 39 440 / 11 681 |
| `MI` | 11 569 / 11 448 / 94 / 27 | 6 053 / 5 933 / 120 / 0 |
| `ENTRY` | 3 / 0 / 0 / 3 | 3 / 0 / 0 / 3 |
| **Всего** | **217 615 / 95 305** | **124 827 / 50 942** |
| без `MI` (набор старого API) | 206 046 / 83 857 | 118 774 / 45 009 |
| отброшено строк | 331 214 = `ETH` 291 486 + служебные 39 714 + склеено 14 | 176 509 = 152 494 + 24 014 + 1 |

**Сравнение со старым API** (`total_photos` [память]: Opportunity ~198 439, Spirit ~124 550): все кадры без `MI` — 206 046 и 118 774 (+4 % и −5 %); только `EFF` — 83 857 и 45 009 (**42 % и 36 %**). Старая галерея, судя по числам, показывала снимки любого типа, а не только полные. Для В3: при v1 «только `full`» Spirit и Opportunity в v1 покажут меньше половины снимков старого API.

### Browse-JPEG [проверено, 25 HEAD]
Правило `PATH_NAME` с `/data/` → `/browse/` + `FILE_NAME` + `.jpg` (для Descent — путь по `PRODUCT_ID`, см. выше): **25 из 25 — `200 image/jpeg`**. Оба марсохода, все источники (`no`, `ho` передняя и задняя, `po`, `mo`, `do`), типы `EFF`/`ESF`/`EDN`, солы от ранних (1–84) до поздних (4763–5037 у Opportunity, 2080–2156 у Spirit). Размеры: `EFF` 68–202 КБ, `ESF` 0,8–18 КБ, `EDN` 1,3–19 КБ, Descent 6,5–8 КБ. Это выборка: что browse есть у **каждого** продукта — [не проверено], но пропусков в ней нет. Полноразмерных JPEG/PNG нет — только `.img`.

**Формат `.img` (этап 9a)** [проверено: 6 файлов Spirit, сол 1–557 — Navcam, Pancam, Hazcam, MI EFF, ESF, EDN; образцы ESF и EDN в `tests/fixtures/upstream/mer/img/`]: PDS3, `RECORD_TYPE = FIXED_LENGTH`, метка с CRLF, комментариями `/* */` и многострочными строками; перед `OBJECT = IMAGE` идут группы со своими `LINES`/`LINE_SAMPLES` — считать только значения объекта IMAGE. `^IMAGE_HEADER` (заголовок VICAR) стоит перед `^IMAGE`, оба — номера записей с 1; `RECORD_BYTES` разный (EFF — 2048, EDN — 512, ESF — 126). `SAMPLE_TYPE = MSB_INTEGER`, `SAMPLE_BITS = 16`, `BANDS = 1`, префиксов и суффиксов строк нет. Пиксели не сжаты (сжатие ICER/LOCO — только на борту, в метке лишь `GROUP = COMPRESSION_PARMS`). Диапазоны: EFF Navcam/Pancam/MI — значения 0–255 в 16-битных отсчётах (8-битная маска), Hazcam EFF — 12 бит (20–2268), EDN и ESF — до ~3200. Размеры: EFF 1024×1024 — 2,1 МБ, EDN 256×256 — 166 КБ, ESF 63×63 — 42 КБ. Варианты LSB, многополосные и с префиксами в реальных файлах не встречены — поддержаны по стандарту PDS3 и проверены только на синтетике.

**Лицензия/авторство:** в `aareadme.txt` томов (`mer1no`, `mer1mw`, `mer1om`) строки об авторстве нет; листинги PDS в подвале ссылаются на `jpl.nasa.gov/imagepolicy` (не PDS-хост, не запрашивали). Предлагаемый `credit`: `NASA/JPL-Caltech`, Pancam — `NASA/JPL-Caltech/Cornell`, MI — `NASA/JPL-Caltech/USGS` [не проверено: в документах томов не найдено].

### Камеры против старого набора
| Старый код | Источник в PDS | `INSTRUMENT_ID` |
|---|---|---|
| `FHAZ` | `ho` | `FRONT_HAZCAM_LEFT/RIGHT` [проверено] |
| `RHAZ` | `ho` | `REAR_HAZCAM_LEFT/RIGHT` [проверено, оба марсохода] |
| `NAVCAM` | `no` | `NAVCAM_LEFT/RIGHT` [проверено] |
| `PANCAM` | `po` | `PANCAM_LEFT/RIGHT` [проверено] |
| `ENTRY` | `do` | `DESCAM`, сол 1, 3 кадра `EDN` на марсоход [проверено] |
| `MINITES` | — | Mini-TES — спектрометр, его данных в Imaging Node нет (Geosciences Node) [не проверено]; камера останется пустой |
| — (в старом наборе нет) | `mo` | `MI` [проверено] — в старом API не было |

### Импорт
Источник — 10 файлов `edrindex.tab` + `.lbl` (5 томов × 2 марсохода), 756 МБ, 20 запросов; уже лежат в `data/mer/`. Загружать через команду приложения (`import-mer`, план — `docs/proposals/stage-8-mer-import.md`), не скриптом в обход синхронизации. Настоящие выборки для фикстур (метка + 9–30 строк на том: `EFF/ESF/EDN/ETH/ERP/EHG/ERS/ECS`, группы `ESF+EDN`, повторённые `PRODUCT_ID`) — `data/mer-samples/<том>/` (не в git).

## Полёты Ingenuity (этап 12, проверка источника)

Проверено 2026-11-20, **2 GET** (по «ок» владельца: не больше 2), `science.nasa.gov`, `User-Agent: sol-index-research/1.0`, без редиректов. Ответы — `data/ingenuity/` (в `.gitignore`).

| адрес | ответ | вывод |
|---|---|---|
| `https://science.nasa.gov/mission/mars-2020-perseverance/ingenuity-mars-helicopter/ingenuity-flight-log/` | 404, 224 680 Б | адрес угадан неверно; страницы с таким путём нет |
| `https://science.nasa.gov/mission/mars-2020-perseverance/ingenuity-mars-helicopter/` | 200, 371 334 Б | страница миссии; **журнала полётов в HTML нет** |

- На странице миссии 2 таблицы — обе не про полёты (запуск/посадка/конец миссии 2024-01-25; масса, габариты, питание). Конец миссии: 25 января 2024.
- **Журнал — PDF** «Ingenuity's Flight Log Details» (Jun 21, 2024, 535,42 КБ): `https://science.nasa.gov/wp-content/uploads/2024/06/ingenuity-helicopter-flight-log.pdf` (в ссылке есть хвост `?emrc=59be3e`). Также на странице: картинка `.../2024/03/ingenuity-flight-log-by-the-numbers.jpg`, постер «72 pathbreaking flights» (`/resource/ingenuity-72-pathbreaking-flights-poster/`), страница `.../ingenuity-mars-helicopter/mars-helicopter-updates/` (блог-хроника). PDF скачан позже отдельным разрешением (см. ниже), разбирается в 72 строки. Машиночитаемого JSON/CSV на странице нет.
- Лицензия и credit: на странице явного текста лицензии нет [не проверено]; общий порядок NASA — материалы без ограничений, но с указанием авторства (NASA/JPL-Caltech), без намёка на одобрение. Credit в журнале указывать «NASA/JPL-Caltech», уточнить по `nasa.gov/nasa-brand-center/images-and-media/` [не проверено].
- **Снимки HELI_NAV / HELI_RTE в нашей базе отсутствуют.** `photos`: 0 снимков с кодами `HELI*`; в списке камер Perseverance (20 штук) их нет; в `app/` и `app/core/cameras.py` кодов `HELI_*` нет. Фид `category=mars2020` отдаёт `total_results` 579 518 = `sol_source_counts.received` (dropped = 0), то есть вертолётных снимков в этом фиде нет. Подтверждено: они в отдельной категории `category=ingenuity` (см. ниже). Привязка по солам полёта к снимкам без этого невозможна. Полёты шли с сола 58 (2021-04-19) по сол 1035 (2024-01-18) (по журналу).

### Журнал полётов (PDF) — проверено 2026-11-20, 1 GET
`GET https://science.nasa.gov/wp-content/uploads/2024/06/ingenuity-helicopter-flight-log.pdf` (`User-Agent: sol-index-research/1.0`) → 200, `application/pdf`, 548 273 Б, `Last-Modified: Mon, 01 Jul 2024 20:35:23 GMT`, `ETag: "c5aa4c661e823127"`, sha256 `dcf0b9e59eb8cb805e484c17f9cab8d56b5a03ca92f2facf708be59c45bf4e11`. Файл: `data/ingenuity/flight-log.pdf`; результат разбора — `data/ingenuity/flights.csv` (72 строки). PDF сделан печатью веб-страницы в Chrome (Skia/PDF, 2024-06-17), 4 страницы, текстовый слой есть, OCR не нужен.

- **Состав:** 72 полёта (совпадает с шапкой «72 flights»). Сверка с шапкой: сумма длительностей 128,86 мин (в шапке ~128,8), сумма расстояний 16 971 м (~17,0 км), максимум высоты 24 м (полёт 61, 2023-10-05), максимум скорости 10 м/с. Всё сходится.
- **Колонки:** `Flight`, `Sol`, `Date`, `Horizontal Distance` (m, ft), `Max. Altitude` (m, ft), `Max. Groundspeed` (m/s, mph), `Duration` (seconds), `Route of Flight` (From, To). Заметок по отдельным полётам нет; единственная сноска: «Dates based on Pacific time zone at the time of Ingenuity's flight».
- **Причуды разбора:**
  - Страницы: 1 — полёты 1–15, 2 — 16–42, 3 — 43–69, 4 — 70–72. `pdfplumber.extract_tables()` даёт чистые строки (двухстрочная шапка пропускается по первому полю-числу). `extract_text()` ломает порядок колонок (дата и маршрут переносятся на соседние строки): использовать только таблицы. На странице 4 таблица продублирована (две таблицы, одни и те же строки): дедуплицировать по номеру полёта.
  - Ячейка даты многострочная: `"April 19,\n2021"`, `"March\n10, 2022"`; месяцы сокращены непоследовательно: `Jan.`, `Feb.`, `Aug.`, `Sept.`, `Oct.`, `Nov.`, `Dec.`, но `March`, `April`, `May`, `June`, `July` целиком.
  - В `Sol` затесался символ из области частного использования `` (иконка), в шапке `Route of Flight` — ``: чистить.
  - Лигатура «fi» вытащена как `\x00`: `Air\x00eld H` = `Airfield H`. Названия площадок переносятся на две строки (`Air\x00eld\nBeta`): схлопывать пробелы.
  - Футы и мили/ч приблизительные, с префиксом `~` (`~328`), тысячи через запятую (`~2,051`). Метры и м/с — без `~`; хвостовые нули (`0.50`, `4.30`) — точность источника.
  - Маршрут: `To` пуст в 27 полётах из 72 — взлёт и посадка на одной площадке (1–4, 12–14, 22, 24, 30, 34–36, 39, 41, 54, 57, 59, 61, 62, 65, 66, 68–72). Площадки: `Wright Brothers Field` (полёты 1–5 как «From»), затем `Airfield B…Z`, греческие `Alpha…Chi`; 44 разных значения `From`. Названия площадок в базу — как строки.
  - Нулевая дистанция в полётах 1, 34, 54, 59, 61, 71, 72 (подъём и посадка на месте).
  - Дата — по тихоокеанскому времени на момент полёта (не UTC, не LMST); надёжный ключ связи с марсианским временем — `Sol` (целое, строго возрастает).
  - Пропусков нет: все 72 строки заполнены, кроме пустого `To`.
  - **Полёт 72:** сол 1035, 2024-01-18, 32,0 с, высота 12 м, дистанция 0, скорость 0, `Airfield Chi` — последний. Журнал ничего не говорит о повреждении лопастей и конце миссии (дата конца, 2024-01-25, только на странице миссии).
- Примеры (flight, sol, date_pacific, distance_m, max_alt_m, max_speed_ms, duration_s, from → to):
  - 1, 58, 2021-04-19, 0, 3, 0, 39.1, Wright Brothers Field → (пусто)
  - 5, 76, 2021-05-07, 129, 10, 2, 108.2, Wright Brothers Field → Airfield B
  - 9, 133, 2021-07-05, 625, 10, 5, 166.4, Airfield E → Airfield F
  - 40, 681, 2023-01-19, 178, 10, 3.20, 91.6, Airfield Z → Airfield Beta
  - 72, 1035, 2024-01-18, 0, 12, 0, 32.0, Airfield Chi → (пусто)
- Журнал заморожен (миссия закончена): достаточно разовой выгрузки в сид (CSV/JSON в репозитории с указанием источника и sha256), парсить PDF в рантайме не нужно. Лицензия: материалы NASA, credit «NASA/JPL-Caltech» (в фиде `credit` именно такой) [текст условий не проверен].

### Фид снимков `category=ingenuity` — проверено 2026-11-20, 1 GET
`GET https://mars.nasa.gov/rss/api/?feed=raw_images&category=ingenuity&feedtype=json&num=100&page=0&order=sol+desc` → 200, 213 675 Б. Файл: `data/ingenuity/raw_images_ingenuity_p0.json`.

- Формат тот же, что у `mars2020` (хост, параметры, поля). В шапке: `type: "ingenuity-images-list-1.1"`, `mission: "ingenuity"`, `total_images` = `total_results` = **14 553**; `per_page` строкой. `num=100` принят. Влияет ли `order=sol+desc`, по одной странице не проверить (все 100 записей — сол 1069).
- Поля записи (как у Perseverance): `imageid`, `sol`, `site`, `drive`, `title`, `caption`, `credit`, `sample_type`, `date_taken_utc`, `date_taken_mars`, `date_received`, `link`, `json_link`, `image_files{small,medium,large,full_res}`, `camera{instrument,filter_name,camera_vector,camera_model_type,camera_position,camera_model_component_list}`, `extended{mastAz,mastEl,sclk,scaleFactor,xyz,subframeRect,dimension}`, `attitude`. Здесь `site` = 0, `drive` = строка `"None"`, `attitude`/`xyz`/`mastAz` и др. = `"UNK"`.
- Камеры на странице: `HELI_NAV` 98, `HELI_RTE` 2 (title «Color Camera»). Других кодов на странице нет; остальные страницы [не проверено]. `sample_type` = `Full` у всех 100, `credit` = `NASA/JPL-Caltech`.
- `imageid` вида `HNM_1069_0761837420_372ECM_N0720001HELI00173_0000A0J` (`HNM` — навигационная, `HSF` — цветная; сол — второе поле). Картинки — тот же CDN, что у Perseverance, каталог `.../sol/01069/ids/edr/browse/heli/<имя>{,_320,_800,_1200}.jpg|.png`. `link` у вертолёта **абсолютный** (`https://mars.nasa.gov/mars2020/multimedia/raw-images/<imageid>`), у Perseverance — относительный `/raw_images/<id>`.
- Сол на странице один — 1069 (2024-02-22, принято 2024-02-23…2024-03-11): это на 34 сола **после** последнего полёта (1035). Что это за съёмка, [не проверено]. Нижняя граница солов и распределение — [не проверено]: нужны остальные страницы (146 по 100) или фильтр по солу.
- **Связи с номером полёта в записях нет:** ни поля, ни упоминания в `title` («Mars Helicopter Sol 1069: Navigation Camera») и `caption`. Ключ — только сол и время `date_taken_utc`.
- **В нашей базе их нет:** из 100 `imageid` совпадений по `photos.source_id` — 0; кодов `HELI*` в `cameras` нет; снимков с `/heli/` в `img_full` — 0. У Perseverance в солах 1060…1080 — 4 774 снимка (без HELI).

### Рекомендация: связь полётов, солов и снимков
- Связь «полёт ↔ снимки» — **по солу**: `flights.sol` = `photos.sol`. Сол в журнале уникален, значит для полёта соответствие по солу однозначно. Обратное неверно: у снимков HELI бывают солы без полёта (1069 уже после полёта 72), для них `flight = null`. Если в сол попал не один полёт — в журнале такого нет.
- По времени сузить кадры полёта нельзя: журнал даёт только дату по PT и длительность, но не время взлёта. Не обещать.
- **Модель:** таблица `ingenuity_flights` (flight, sol, date_pacific, distance_m, max_alt_m, max_speed_ms, duration_s, route_from, route_to), сид из `flights.csv`. Снимки: Ingenuity как отдельный «марсоход» (`rovers.id = ingenuity`, камеры `HELI_NAV`, `HELI_RTE`) и адаптер на `category=ingenuity` по образцу `m2020` (отличия: категория в `BASE_PARAMS`, абсолютный `link`). Объём 14 553 снимка, ~146 страниц, около 3 минут при 1 запросе/с.
- **API:** v2 — `GET /api/v2/ingenuity/flights` (все 72 без пагинации) и `GET /api/v2/ingenuity/flights/{n}` с полями журнала и ссылкой на снимки сола (`/photos?rover=ingenuity&sol=N`). В v1 вертолёта нет, не добавлять без решения владельца. Миссия закончена: достаточно разового `backfill`, расписание не нужно.

## Данные для предложений дизайнера (2026-11-20, офлайн: dev-база, фикстуры, адаптеры)

- **Ширина/высота кадра.**
  - Perseverance: `extra.extended.dimension` строкой `"(W,H)"`, 579 518 из 579 518 (100%) подходят под `^\(\d+,\d+\)$`, `UNK` нет. Частые: (1648,1200) 298 722; (1288,968) 37 339; (1280,1024); (2048,2048); (128,128) 20 802. Это размеры полноразмерного PNG; `_320/_800/_1200.jpg` — масштабы.
  - Curiosity: **нет нигде** (в `extended` только `lmst, bucket, mast_az, mast_el, url_list, contributor, filter_name`); получить можно лишь загрузкой файла.
  - Spirit/Opportunity: `extra.lines` / `extra.line_samples` из метки `.img` (342 442 из 342 442 числовые, 100%; 1024×1024 — 50 944 у Spirit, есть 63, 256, 272×320, 512). Это размер EDR, а `img_full` — browse-JPEG: совпадение размеров с JPEG [не проверено].
- **Местное солнечное время.**
  - Perseverance: в фиде есть `date_taken_mars` (`"Sol-02031M11:30:55.785"`, LMST, формат `Sol-NNNNNMHH:MM:SS.mmm`), но адаптер (`app/sources/m2020.py`) его **не сохраняет**: в `extra` только `extended`, `attitude`, `camera`. Для `local_time` нужна правка адаптера и перезагрузка 579 518 снимков (~5 800 страниц по 100) либо вывод из `taken_at` расчётом.
  - Curiosity: `extra.extended.lmst` того же формата (`Sol-05073M11:25:43.222`), заполнен у 388 029 из 791 788 (49%), 4 665 солов из диапазона 0…5077; остальное — `null` в фиде.
  - Spirit/Opportunity: `extra.local_true_solar_time` (`HH:MM:SS`, 100%). Это LTST (истинное, не среднее) — другая шкала, чем LMST; смешивать под одним полем без пометки нельзя.
- **Миниатюры.** Perseverance: `img_small` (_320), `img_medium` (_800), `img_large` (_1200) заполнены у 100%. Curiosity: в списке `small/medium/large` нет. Фид MSL отдаёт миниатюры **отдельными записями** (`is_thumbnail: true`, `extended.sample_type: "thumbnail"`, своё имя файла с кодом продукта `I01` вместо `E01`: `4327MR1065450012201536I01_DXXX.jpg`), адаптер их отбрасывает (`NO_THUMBNAILS` в запросе). Сопоставление миниатюры с кадром по имени — гипотеза; в фикстуре (13 записей, 5 миниатюр) однозначного ключа не видно [не проверено]. Spirit/Opportunity: только browse-JPEG.
- **`end_date` MER.** В базе: Spirit `max_sol` 2209, `max_date` 2010-03-21; Opportunity 5111, 2018-06-10 (при этом `full_max_date` 2018-06-11 — расхождение на сутки в границе UTC/сол, разобрать отдельно). Поля `end_date` в `rovers` нет. Дат последнего контакта в источниках нет; из публичных сообщений NASA (по памяти, не проверено источником): Spirit — последний контакт 2010-03-22 (сол 2210), объявлена завершённой 2011-05-25; Opportunity — последний контакт 2018-06-10, завершена 2019-02-13. Значение придётся задавать константой/миграцией.
- **Счётчики камер за сол.** Таблица `camera_sol_counts (rover_id, sol, camera_id, total_photos, full_photos)` уже есть: 69 114 строк (Curiosity 37 426, Perseverance 14 259, Opportunity 12 182, Spirit 5 247), 8,9 МБ, PK по `(rover_id, sol, camera_id)`. Выборка одного сола — 0,8 мс; живой `GROUP BY` по `photos` на самом большом соле Perseverance (1717, 3 950 снимков) — 19 мс, на Curiosity сол 0 (4 380) — 6,5 мс. Вся Curiosity из `camera_sol_counts` — 8,5 мс. Вывод: счётчики за сол почти бесплатны.
- **Соседи внутри сола.** Индекс `ix_photos_rover_id_sol_taken_at (rover_id, sol DESC, taken_at DESC, id DESC)` совпадает с сортировкой списка. Сол 1717, середина (позиция 2000 из 3 950): `prev`/`next` по `(taken_at, id) <|> (...)` с `LIMIT 1` — Index Only Scan, 0,04–0,14 мс, 4 буфера; `position` как `count(*)` по `(taken_at,id) >` — 0,33 мс, 18 буферов. С фильтром `camera` порядок `taken_at` из индекса `(rover_id, camera_id, sol)` и частичного `ix_photos_full_rover_id_sol_camera_id_id` (порядок по `id`, не `taken_at`) не получить без сортировки внутри сола; для сола в несколько тысяч снимков это единицы миллисекунд [не проверено EXPLAIN-ом].

## Старый API

Формат и поведение старого API, которые воспроизводит наш v1, — в `LEGACY-API.md`.

## Образцы Perseverance (этап 13, проверка источника)

Проверено 2026-11-20, **2 GET** (разрешение владельца: не больше двух, без ретраев и переходов по ссылкам, общий User-Agent). Ответы — `data/samples/` (в `.gitignore`). Отчёт: `docs/reports/stage-13-samples.md`.

- **Слой MMGIS с образцами — нет.** В `DATA-SOURCES.md` и отчёте этапа 11 слоя образцов не упоминалось. Проба `https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_samples.json` (имя угадано по схеме `M20_waypoints`): **403**, 5 546 Б, `text/html` — стандартная страница «Page not found» от S3/CloudFront, то есть файла нет. Других имён слоя не пробовали (лимит запросов); существование слоя под другим именем [не проверено].
- **Страница «Mars Rock Samples» — работает.** `https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/`: **200**, 494 867 Б, `text/html; charset=UTF-8`, без редиректа, `cache-control: max-age=300`. Машиночитаемого JSON/CSV на странице нет (ссылок на `.json`/`.csv` в HTML нет); данные — одна HTML-таблица `<figure class="wp-block-table"><table>` (в той же странице те же образцы повторены карточками для карусели). `article:modified_time` = 2026-06-20.
- **Колонки таблицы (11):** `Sample No.`, `Date Sealed`, `Sol Sealed`, `Sample Name`, `Feature Name`, `Sampling Location`, `Current Location`, `Science Notes` (ссылка PDF на досье в PDS: `pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_sample_dossier/initial_reports/initial_reports_volumeN.pdf#page=K`; у образцов 26–30 пусто), `Sample Type`, `Sample Height` (см и дюймы одной строкой через `&nbsp;`), `Rock Type`.
- **Объём:** 30 строк, `Sample No.` 1…30 без пропусков. Сол запечатывания 164…1441 у 28 образцов; у № 29 и 30 `Date Sealed` и `Sol Sealed` = `TBD`. Актуальность отстаёт: сегодня сол ~2030, а последняя дата — 10.03.2025 (сол 1441); подпись к фото образца № 30 — 1 июля 2025 (сол 1551).
- **Статус «на борту / в депо» есть:** `Current Location` = `Perseverance Rover` (21) или `Sample Depot` (9: №№ 1, 2, 5, 7, 9, 11, 13, 15, 18). Других значений нет.
- **Тип материала:** `Sample Type` — `Rock Core` 27, `Regolith` 2 (№ 17, 18), `Atmospheric` 1 (№ 1). `Rock Type` — свободный текст: Sedimentary 13, Igneous 8, Igneous/impactite 3, `Likely mixed sedimentary and igneous grains` 2, `n/a` 1, `Silica-cemented Carbonate`, `Serpentinite`, `Igneous or sedimentary`.
- **Серийных номеров пробирок нет** — только порядковый `Sample No.` (это не номер трубки). Witness-трубки в таблице не перечислены (описаны текстом).
- **Координат и site/drive нет.** Место — только текст: `Feature Name` («Cheyava Falls») и `Sampling Location` («Neretva Vallis»), по-разному написанные (`Ch’ał`, `Séítah` с диакритикой и типографским апострофом).
- **Причуды:** даты в формате AP-style («Aug. 6, 2021», «Sept. 6, 2021», «March 7, 2022»); `Sol Sealed` — число строкой или `TBD`; высота — «6.14 2.42» (см, дюймы через `&nbsp;`), у № 1 — `n/a n/a`, у № 29 — `Est. 5-7 cm (2-2.8 in)`, у № 30 — `1.91 cm (0.75 inches)`; в имени бывает псевдоним: `Ha'ahóni (aka "Hahonih")`; в `Sample Name` и `Feature Name` у № 1 одно и то же слово. Один `<table>` на странице, разметка WordPress-блока — может измениться при редизайне.
- **Лицензия и авторство:** материалы NASA, подпись изображений — `NASA/JPL-Caltech`; фото образцов на странице не копируем. Условия использования текста страницы [не проверены].

### Образцы Perseverance: дополнительная проверка (2026-11-20, ещё 2 GET)

- **PDS Geosciences, корень набора досье:** `https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_sample_dossier/` — **200**, 973 Б, `text/html` (листинг IIS). Содержимое: `bundle_sample_dossier.lblx` (27.09.2023, 4 383 Б), папка `document` (11.09.2023), папка `initial_reports` (**изменена 13.02.2026**), `readme.txt` (11.09.2023), `urn-nasa-pds-mars2020_sample_dossier.md5` (13.02.2026, 1 382 Б). Машиночитаемой таблицы образцов (CSV/TAB) в корне нет: данные — PDF-тома `initial_reports/initial_reports_volumeN.pdf` (это те же PDF, что в колонке «Science Notes» страницы NASA). Внутрь папок не заходили (лимит). Свежесть: папка обновлена 13.02.2026, но какие образцы и солы в ней — не видно (число образцов в томах [не проверено]); координат в виде таблицы нет.
- **Конфигурация слоёв MMGIS:** проба `https://mars.nasa.gov/mmgis-maps/M20/config.json` (имя угадано) — **403**, 5 546 Б, `text/html`, «Page not found». Список слоёв не получен, слой образцов (если есть) и его URL **неизвестны**. Статический путь к конфигурации на этом хосте мы не знаем; динамический API MMGIS (`/API/configure/...`) на `/mmgis-maps/` не пробовали [не проверено].

## Звуки Марса (этап 14, проверка источника)

Проверено 2026-11-20, **3 GET** (2 + 2 разрешённых владельцем; четвёртый не понадобился), общий User-Agent, без ретраев и переходов по ссылкам. Ответы — `data/sounds/` (`.gitignore`). Отчёт: `docs/reports/stage-14-sounds.md`. **Итог: полного машиночитаемого списка записей на NASA-страницах нет; найденная страница — редакционная, с 3 избранными записями.**

- **Запрос 1** `https://science.nasa.gov/mission/mars-2020-perseverance/participate/sounds-of-mars/` (угадан) — **404**, 224 656 Б. **Запрос 2** `https://mars.nasa.gov/mars2020/participate/sounds/` — **302** (не следовали) на `https://science.nasa.gov/mission/mars-2020-perseverance/sounds-of-mars/`.
- **Запрос 3** `https://science.nasa.gov/mission/mars-2020-perseverance/sounds-of-mars/` — **200**, 303 389 Б, `text/html; charset=UTF-8`, 0,99 с, `article:modified_time` = 2026-05-07 (последнее изменение страницы). Страница WordPress (`?p=390775`).
- **Содержимое:** текст про звук на Марсе + три кнопки «Listen» и одно встроенное видео. Тегов `<audio>`: 0; JSON-списка, `data-`атрибутов с URL, ссылок на `wp-json` с данными нет (только `wp-json/oembed/1.0/embed` самой страницы и `wp-json/pum/v1` плагина всплывающих окон). Записи:
  1. микрофон SuperCam — «First Acoustic Recording of Laser Shots on Mars», ссылка на страницу ресурса `https://science.nasa.gov/resource/first-acoustic-recording-of-laser-shots-on-mars/`;
  2. микрофон EDL — «Sounds of Perseverance Mars Rover Driving - Sol 16 (90-second highlights)», `https://science.nasa.gov/resource/sounds-of-perseverance-mars-rover-driving-sol-16-90-second-highlights/`;
  3. «Hello from the children of planet Earth» (Voyager, не запись Perseverance) — прямой `.mp4` `https://science.nasa.gov/wp-content/uploads/2024/04/sounds-of-mars-voyager-hello-earth.mp4`;
  плюс встроенное видео «Sample More Sounds From Mars» (No Voiceover, 1280x720, 290 МБ, mp4) `https://assets.science.nasa.gov/content/dam/science/psd/mars/downloadable_items/4/8/48249_SoundsofMars_NoVoiceover_1280x720.mp4`.
- **Поля:** только название (в тексте кнопки), URL, подпись авторства `NASA/JPL-Caltech` (у Voyager — `NASA/JPL-Caltech/Voyager Golden Record`). **Длительности нет** (кроме «90-second highlights» в названии), формата аудио нет (файлы — `.mp4`, т.е. видео со звуком), описаний на запись нет (общий абзац на микрофон). Сол: только в названии одной записи (сол 16); у остальных нет.
- **Хосты файлов:** `science.nasa.gov/wp-content/uploads/...` и `assets.science.nasa.gov/content/dam/...` (CDN, путь с версионными цифрами `4/8/48249_` — стабильность [не проверена]). Страницы ресурсов `science.nasa.gov/resource/<slug>/` не открывали (лимит «никаких других ссылок»): прямой URL аудио, длительность и дата там [не проверены].
- **Полный каталог:** ни на странице, ни в видимых ссылках нет «всех опубликованных записей»; страница обновлялась редко (2026-05-07). Раздел «Audio» в меню мультимедиа (`science.nasa.gov/multimedia/audio/`?) [не проверен]. PDS (`mars2020_supercam`, сырые продукты микрофона, `.wav`) [не проверено, из памяти]: полный охват, но без описаний; продукты привязаны к солу через имя/метки.
- **Связь с солами/остановками/фото:** только по `sol`, и то не у всех записей.
- **Лицензия:** материалы NASA, авторство сохраняем; условия использования [не проверены].
