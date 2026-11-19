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

- **Положение — работает у обоих.** GeoJSON `https://mars.nasa.gov/mmgis-maps/{M20|MSL}/Layers/json/{M20|MSL}_waypoints.json` (точки стоянок, ~600 КБ) и `..._traverse.json` (линия маршрута, 1,4–1,7 МБ). В точке: `sol`, `site`, `drive` (те же, что у снимков — можно связать фото с местом), `lat`, `lon`, `elev_geoid`, `easting`/`northing`, `roll`/`pitch`/`yaw`/`tilt`, `dist_m`, `dist_total_m`. Свежесть: M20 — до сола 2022 (последний сол снимков ~2031), MSL — до сола 5073.
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

## Старый API

Формат и поведение старого API, которые воспроизводит наш v1, — в `LEGACY-API.md`.
