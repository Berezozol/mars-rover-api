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

Работает фильтр по диапазону: `...&order=sol+desc&num=100&page=<k>&condition_2=<from>:sol:gte&condition_3=<to>:sol:lte`. Сол 1000 → `total_results: 385`, все снимки сола 1000, страницы 0–3 по 100/100/100/85 (разбивка по страницам — проба; `page`/`total_results` — в `m2020/range_1000_page0.json`, `range_1000_page3.json`); сол 2024 → 316 (проба, без фикстуры). Страницы идут, пока число полученных снимков меньше `total_results` и страница не пустая. Второго ключа сортировки внутри сола нет, поэтому стабильность порядка между страницами не гарантирована — адаптер убирает повторы `imageid` внутри сола. Проверить на живом фиде, не теряются ли снимки на стыке страниц, — открытый вопрос. Так адаптер и забирает как один сол, так и диапазон солов. В фиде бывают пропуски солов: на 2026-11-17 после 2031 сразу идёт 2024.

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

- **Погода Curiosity (REMS)** [не проверено]: `https://mars.nasa.gov/rss/api/?feed=weather&category=msl&feedtype=json` — найдено в 2021 году через инспектор страницы REMS. Неофициально.
- **Положение Perseverance** [не проверено в 2026]: GeoJSON `https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json` и `.../M20_traverse.json` (ещё использовались в ноябре 2025). Для Curiosity найти аналог через страницу `science.nasa.gov/mission/msl-curiosity/location-map/`.

## Spirit и Opportunity (MER, этап 8)

Миссии завершены, новых снимков не будет — нужен разовый импорт.
- Старый скрапер разбирал HTML-галерею `https://mars.nasa.gov/mer/gallery/all/` (`opportunity.html`, `spirit.html`, страницы по камерам и солам) [старый код]. Жива ли она сейчас — не проверено.
- Запасной вариант — архивы NASA PDS (Imaging Node). Объём и формат выяснить на этапе 8.

## Старый API

Формат и поведение старого API, которые воспроизводит наш v1, — в `LEGACY-API.md`.
