# Совместимый API v1 — поведение старого Mars Rover Photos API

Контракт для `/api/v1/...` и `/mars-photos/api/v1/...`. Цель — чтобы любой клиент старого API заработал, если поменять только хост.

**Откуда взято:** код `github.com/corincerami/mars-photo-api`, последний коммит `12223ed` («Goodbye», 2025-10-08). Ruby on Rails 8, ActiveModel::Serializers 0.9.8, Kaminari 1.2.1. Ссылки ниже — на файлы этого репозитория. Для сверки его можно склонировать рядом с проектом, но **код не копируем** (лицензия GPL-3.0) — воспроизводим только поведение.

Пометки: **[код]** — прочитано в коде или тестах оригинала; **[память]** — по известным ответам API, в коде напрямую не видно, проверить по архивным примерам.

## Пути

Оригинал жил по двум адресам: `https://api.nasa.gov/mars-photos/api/v1/...` (через шлюз NASA, с `api_key`) и `https://mars-photos.herokuapp.com/api/v1/...` (без ключа, с CORS). Мы отдаём один и тот же роутер по обоим префиксам:

| Путь | Ответ | Источник |
|---|---|---|
| `GET /api/v1/rovers` | `{"rovers": [Rover]}` | `rovers_controller.rb#index` [код] |
| `GET /api/v1/rovers/{rover}` | `{"rover": Rover}` | `rovers_controller.rb#show` [код] |
| `GET /api/v1/rovers/{rover}/photos` | `{"photos": [Photo]}` | `photos_controller.rb#index` [код] |
| `GET /api/v1/rovers/{rover}/latest_photos` | `{"latest_photos": [Photo]}` | `latest_photos_controller.rb` [код] |
| `GET /api/v1/photos/{id}` | `{"photo": Photo}` | `photos_controller.rb#show` [код] |
| `GET /api/v1/manifests/{rover}` | `{"photo_manifest": Manifest}` | `manifests_controller.rb` [код] |

Плюс те же пути с префиксом `/mars-photos`. Параметр `api_key` принимаем и игнорируем. CORS: любые источники, только `GET` (`config/application.rb`) [код].

## Имя марсохода

Без учёта регистра: `curiosity`, `Curiosity`, `CURIOSITY` — одно и то же (`titleize` / `capitalize` в контроллерах) [код].
Неизвестный марсоход → **`400`** и тело ровно `{"errors": "Invalid Rover Name"}` — для `photos`, `latest_photos`, `rovers/{rover}` и `manifests/{rover}` [код, есть тесты].

Марсоходы — все четыре, как в оригинале (`db/seeds.rb`) [код]:

| name | landing_date | launch_date | status |
|---|---|---|---|
| Curiosity | 2012-08-06 | 2011-11-26 | active |
| Perseverance | 2021-02-18 | 2020-07-30 | active |
| Opportunity | 2004-01-25 | 2003-07-07 | complete |
| Spirit | 2004-01-04 | 2003-06-10 | complete |

Даты посадки взяты из сидов [код], дата запуска Curiosity — из фабрики тестов [код]. Остальные даты запуска и статусы — [память], сверить.

## `photos`: параметры и правила (`app/models/photo.rb`)

1. **Нужен `sol` или `earth_date`.** Если нет ни того ни другого — `200 {"photos": []}`, а не ошибка [код, тест «returns an empty collection»].
2. **`sol` важнее `earth_date`:** если переданы оба, используется только `sol` [код].
3. **`earth_date`** разбирается как `YYYY-M-D`, ведущие нули необязательны: в README пример `earth_date=2015-6-3` [код].
4. **`camera`** — без учёта регистра (`upcase`). Неизвестная для этого марсохода камера → `200` и пустой список, не ошибка [код].
5. **Пагинация только при `page`.** Без `page` возвращаются **все** фото под фильтр. С `page` — по 25 на страницу; `per_page` меняет размер, но только вместе с `page` [код, 4 теста].
6. **Сортировка:** по `camera_id`, затем по `id` фото (`order(:camera_id, :id)`) [код]. У нас — по порядку камеры в справочнике, затем по `id`.
7. **В выдачу попадают только полнокадровые снимки:** Perseverance — `sample_type == "Full"`, Curiosity — `extended.sample_type == "full"` [код скраперов]. Субкадры, миниатюры и уменьшенные копии в v1 не показываются.

`latest_photos` — то же самое, но `sol` = максимальный сол марсохода; `camera`, `page` и `per_page` работают так же [код].

## `earth_date` — вычисляется из сола, а не берётся из даты съёмки

```
earth_date = landing_date + floor(sol * 88775.244 / 86400)   # дни
```

Вычисление с плавающей точкой, в том же порядке операций (`photo.rb`, `SECONDS_PER_SOL`) [код]. **Тестовые векторы** из тестов оригинала (Curiosity, посадка 2012-08-06):

| sol | earth_date |
|---|---|
| 1 | 2012-08-07 |
| 30 | 2012-09-05 |
| 100 | 2012-11-16 |
| 829 | 2014-12-05 |

Фильтр `earth_date=D` у нас переводится в сол: `d = (D − landing_date).days`, `s = ceil(d / k)`, где `k = 88775.244 / 86400`; если `floor(s·k) == d` — берём сол `s`, иначе пустой список. Каждой дате соответствует не больше одного сола: сол длиннее суток, поэтому примерно раз в 37 дней дата пропускается.

В v2 `earth_date` — это реальная дата съёмки по UTC. Это разные поля, их не путать.

## Объекты

Числа в примерах ниже — иллюстрация, важны только поля и их типы.

**Photo** [код: `photo_serializer.rb` — `attributes :id, :sol, :camera, :img_src, :earth_date`, `has_one :rover`]
```json
{
  "id": 102693,
  "sol": 1000,
  "camera": {"id": 20, "name": "FHAZ", "rover_id": 5, "full_name": "Front Hazard Avoidance Camera"},
  "img_src": "https://mars.nasa.gov/msl-raw-images/...JPG",
  "earth_date": "2015-05-30",
  "rover": {
    "id": 5, "name": "Curiosity", "landing_date": "2012-08-06", "launch_date": "2011-11-26",
    "status": "active", "max_sol": 4102, "max_date": "2024-02-19", "total_photos": 695670,
    "cameras": [{"name": "FHAZ", "full_name": "Front Hazard Avoidance Camera"}]
  }
}
```
- `camera` — все поля модели: `id`, `name`, `rover_id`, `full_name`. Это `attributes :camera`, а не сериализатор камеры [код + память].
- `rover` — как объект Rover ниже, вместе с `cameras` [код: `has_one :rover` → `RoverSerializer`].
- `img_src`: Perseverance — `image_files.large` (JPG 1200 px), Curiosity — `https_url` [код скраперов].
- `id` фото, марсоходов и камер у нас свои — с числами старой базы они не совпадут. Это допустимое расхождение.

**Rover** [код: `rover_serializer.rb`]
`id`, `name`, `landing_date`, `launch_date`, `status`, `max_sol`, `max_date`, `total_photos`, `cameras: [{"name", "full_name"}]`.
`max_sol`, `max_date` и `total_photos` считаются только по полнокадровым снимкам; `max_date` — по формуле выше.

**Manifest** [код: `photo_manifest.rb`, `photo_manifest_serializer.rb`, тест]
```json
{"photo_manifest": {
  "name": "Curiosity", "landing_date": "2012-08-06", "launch_date": "2011-11-26", "status": "active",
  "max_sol": 4102, "max_date": "2024-02-19", "total_photos": 695670,
  "photos": [{"sol": 0, "earth_date": "2012-08-06", "total_photos": 3702, "cameras": ["CHEMCAM", "FHAZ", "MARDI", "RHAZ"]}]
}}
```
`photos` — по записи на каждый сол с фото: `sol`, `earth_date` (по формуле), `total_photos`, `cameras` (уникальные имена). В оригинале порядок не задан — у нас по возрастанию сола. Оригинал кэшировал манифест в Redis на сутки; у нас он читается из `sol_summaries`.

## Камеры

Имена камер в v1 — это `cameras.code` (как в источнике), `full_name` — из справочника.

- **Perseverance:** в оригинале 17 камер из сидов (`EDL_RUCAM`, `EDL_RDCAM`, `EDL_DDCAM`, `EDL_PUCAM1`, `EDL_PUCAM2`, `NAVCAM_LEFT`, `NAVCAM_RIGHT`, `MCZ_RIGHT`, `MCZ_LEFT`, `FRONT_HAZCAM_LEFT_A`, `FRONT_HAZCAM_RIGHT_A`, `REAR_HAZCAM_LEFT`, `REAR_HAZCAM_RIGHT`, `SKYCAM`, `SHERLOC_WATSON`, `SUPERCAM_RMI`, `LCAM`). Снимки других камер старый скрапер **отбрасывал** [код]. Мы храним все — лишние камеры клиентам не мешают.
- **Curiosity:** в README старые сокращения `FHAZ`, `RHAZ`, `MAST`, `CHEMCAM`, `MAHLI`, `MARDI`, `NAVCAM`. Но после перехода на новый фид скрапер заводил камеры по именам инструментов (`MAST_LEFT`, `MAST_RIGHT` и т.п.) [код], поэтому `camera=fhaz` для свежих солов в оригинале уже ничего не находил.
  **Улучшение:** в v1 сокращение работает как группа: `FHAZ` → все `FHAZ_*`, `RHAZ` → `RHAZ_*`, `NAVCAM` → `NAV_*`, `MAST` → `MAST_*`, `CHEMCAM` → `CHEMCAM_*`; `MAHLI` и `MARDI` — как есть. Точные имена инструментов MSL проверить по фикстурам.
- **Opportunity и Spirit:** `FHAZ`, `RHAZ`, `NAVCAM`, `PANCAM`, `MINITES`, `ENTRY` [код].
- Опечатку оригинала `"Read Hazard Avoidance Camera"` в `full_name` для `RHAZ` не повторяем — пишем «Rear».

## Где мы сознательно отличаемся

Для корректных запросов форма ответа совпадает со старой. Для некорректных вместо `500` и странностей — понятная ошибка:

| Ситуация | Оригинал | У нас |
|---|---|---|
| `earth_date` не разбирается | исключение → `500` [код] | `400 {"errors": "Invalid earth_date"}` |
| `sol` не число | приводилось к `0` → фото сола 0 [код] | `400 {"errors": "Invalid sol"}` |
| `/photos/{id}` не найден | стандартный `404` Rails | `404 {"errors": "Photo not found"}` |
| `per_page` | без ограничения | максимум 200, больше — обрезаем без ошибки |
| числовые `id` | старой базы | свои |

## Источники данных старого скрапера (для справки)

- Perseverance: сводка `...&latest=true`, затем по каждому солу `...&feedtype=json&sol=<N>` (`perseverance_scraper.rb`) [код].
- Curiosity: последний сол — `order=sol desc ...&per_page=1&page=0&condition_1=msl:mission` → `items[0].sol`; по каждому солу — `condition_2=<N>:sol:in`, `per_page=200` (`curiosity_scraper.rb`) [код]. **Внимание:** старый скрапер брал только `page=0`, то есть максимум 200 снимков на сол; в соле 4327 их 508. Мы проходим все страницы, пока `more == true`.
- Opportunity и Spirit: HTML-галерея `https://mars.nasa.gov/mer/gallery/all/` (`opportunity_spirit_scraper.rb`) [код]. Жива ли она сейчас, неизвестно.

Проход по солам в старом коде начинался с максимального уже загруженного сола — без перекрытия. Поэтому снимки, которые доходили за сол позже, могли теряться. У нас перекрытие `SYNC_OVERLAP_SOLS`.
