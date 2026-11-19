# Контракт API

Источник правды для `backend-dev` и `qa-engineer`. Меняется только с моего подтверждения: роль, которой нужно отступить от контракта, останавливается и сообщает об этом.

## Две версии

- **v1 — совместимая со старым Mars Rover Photos API.** Пути `/api/v1/...` и `/mars-photos/api/v1/...`, формат и поведение старого API. Контракт v1 — **`docs/LEGACY-API.md`**, а не этот файл. Это главный продукт: старые клиенты переезжают сменой хоста.
- **v2 — наш собственный API**, описан ниже: все типы снимков, размеры изображений, реальная дата съёмки, единый формат ошибок. Под него будет делаться фронтенд.

Обе версии читают одни и те же таблицы через одни и те же сервисы; различаются только роутеры и схемы ответов.

## Общие правила v2

- Базовый путь `/api/v2`, JSON, UTF-8. Даты — `YYYY-MM-DD`, время — ISO 8601 в UTC с `Z`.
- `{rover}` — slug: `curiosity`, `perseverance`, `opportunity`, `spirit`. Регистр не важен.
- Пагинация: `page` (с 1, по умолчанию 1), `per_page` (по умолчанию 25, от 1 до 100). Ответ-список:
  ```json
  {"items": [], "page": 1, "per_page": 25, "total": 0}
  ```
- Ошибки — всегда в одном формате:
  ```json
  {"error": {"code": "rover_not_found", "message": "Rover 'spirit' not found", "details": null}}
  ```
  | HTTP | code | когда |
  |---|---|---|
  | 404 | `rover_not_found`, `photo_not_found`, `not_found` | нет сущности или пути |
  | 422 | `validation_error` | неверные параметры; `details` — список `{"field", "message"}` |
  | 401 | `unauthorized` | admin-ручка без верного `X-Admin-Token` |
  | 409 | `sync_in_progress` | синхронизация марсохода уже идёт |
  | 502 | `upstream_error` | источник (NASA PDS) недоступен или отдал неразборчивые данные — конвертация снимка MER |
  | 503 | `converter_busy` | очередь конвертации снимков MER заполнена; `Retry-After` |
  | 504 | `upstream_timeout` | конвертация снимка MER не уложилась во время |
  | 429 | `rate_limited` | превышен лимит запросов (см. «Ограничение частоты») |
  | 503 | `db_unavailable` | база недоступна |
- В каждом фото есть `credit` — авторство NASA/JPL-Caltech и партнёров.
- Границы чисел: `sol`, `from_sol`, `to_sol` — от 0 до 2 147 483 647; `page` — от 1 до 1 000 000; за пределами → `422 validation_error`. `id` снимка вне диапазона (≤ 0 или больше 2⁶³−1) → `404 photo_not_found`. `{rover}`, который не может быть slug (не только латинские буквы), → `404 rover_not_found` без обращения к базе.
- `sample_type` в фильтре — только `full`, `subframe`, `downsampled`. В ответах встречаются и другие значения источника (у Curiosity — `mixed`, `chemcam prc`): такие снимки видны без фильтра.

## Объекты

**Rover**
```json
{"id": "perseverance", "name": "Perseverance", "mission": "mars2020",
 "launch_date": "2020-07-30", "landing_date": "2021-02-18", "status": "active",
 "max_sol": 2031, "max_date": "2026-11-06", "total_photos": 579518,
 "cameras_count": 23, "last_synced_at": "2026-11-17T06:00:12Z"}
```
`max_sol`, `max_date` и `last_synced_at` равны `null`, пока данных нет.

**Camera**
```json
{"code": "NAVCAM_LEFT", "name": "Left Navigation Camera (Navcam)", "total_photos": 12345}
```

**Photo** (в списках)
```json
{"id": 81234, "rover": "perseverance", "sol": 2031, "earth_date": "2026-11-06",
 "taken_at": "2026-11-06T12:16:25Z",
 "camera": {"code": "SUPERCAM_RMI", "name": "SuperCam Remote Micro Imager"},
 "sample_type": "full", "title": "Mars Perseverance Sol 2031: SuperCam Camera",
 "img_src": "https://mars.nasa.gov/.../LRF_2031_..._0100I4J01.png",
 "images": {"small": "..._320.jpg", "medium": "..._800.jpg", "large": "..._1200.jpg", "full": "...png"},
 "credit": "NASA/JPL-Caltech/LANL/CNES/IRAP",
 "source": {"id": "LRF_2031_0847237865_241EBY_N0930000SCAM01031_0100I4J",
            "link": "https://mars.nasa.gov/mars2020/multimedia/raw-images/LRF_..."}}
```
`img_src` всегда равен `images.full`. У Curiosity `small`, `medium` и `large` — `null`. У Spirit и Opportunity (этап 9a, решение владельца): `images.small` — уменьшенный JPEG архива NASA PDS (превью), `images.full` (и `img_src`) — абсолютная ссылка на нашу конвертацию полного кадра `{origin}/api/v2/photos/{id}/image?size=full` (`origin` — от хоста текущего запроса, как на странице `/`), `medium`, `large` и `title` — `null`, `source.link` — исходный продукт PDS `.img`.

**PhotoDetail** = Photo + `caption`, `received_at`, `site`, `drive`, `extra` (объект с техническими полями источника).

## Эндпоинты

### `GET /health`
- `200 {"status": "ok", "db": "ok"}`
- `503 {"status": "degraded", "db": "error"}`
- `HEAD /health` — тот же статус без тела (для внешнего мониторинга, этап 9).

### `GET /health/sync`
Свежесть данных — для внешнего мониторинга (этап 9). `HEAD` — тот же статус и заголовки без тела (UptimeRobot по умолчанию проверяет методом `HEAD`). Без авторизации, без лимита частоты, `Cache-Control: no-store`, секретов и текстов ошибок не содержит.
Учитываются только марсоходы с синхронизацией (`curiosity`, `perseverance`); Spirit и Opportunity — нет.
```json
{"status": "ok",
 "rovers": [{"rover": "curiosity", "last_success_at": "2026-11-18T20:15:03Z", "newest_photo_date": "2026-11-17",
             "sync_stale": false, "photos_stale": false}]}
```
- `last_success_at` — `finished_at` последнего запуска синхронизации со статусом `success` (любого режима), `null`, если такого нет.
- `newest_photo_date` — самая поздняя `earth_date` снимков марсохода, `null`, если снимков нет.
- `sync_stale` — `last_success_at` равно `null` или старше `SYNC_STALE_HOURS` (по умолчанию 24 ч).
- `photos_stale` — `newest_photo_date` равно `null` или старше `PHOTOS_STALE_DAYS` (по умолчанию 21 день: JPL публикует снимки Perseverance с задержкой больше 10 дней, а при соединении Марса с Солнцем связи нет до двух недель; решение владельца, этап 9).
- Результат кэшируется в памяти на `HEALTH_SYNC_CACHE_SECONDS` (по умолчанию 30 с, `0` — без кэша): ответ может отставать от базы на это время. Ошибки базы не кэшируются.
- `200` и `"status": "ok"`, если ни один флаг не поднят; иначе `503` и `"status": "stale"` с тем же телом. База недоступна → `503 {"status": "degraded", "db": "error"}`.

### `GET /`
Публичная страница (HTML, английский): что это за сервис и что он неофициальный, как переехать со старого API (сменить хост), отличия от старого API, примеры запросов v1 и v2, ссылка на `/docs`, авторство снимков. Адреса в примерах — от хоста текущего запроса; некорректный или враждебный `Host` в страницу не попадает (подставляется адрес сервера), всё выводимое экранируется. Без логотипов NASA и JPL. `Cache-Control: public, max-age=3600`, лимит частоты не действует.

### `GET /api/v2/rovers`
`200 {"items": [Rover, ...]}` — без пагинации, сортировка по `id`.

### `GET /api/v2/rovers/{rover}`
`200 Rover` · `404 rover_not_found`

### `GET /api/v2/rovers/{rover}/cameras`
`200 {"items": [Camera, ...]}`, сортировка по `code`. · `404 rover_not_found`

### `GET /api/v2/rovers/{rover}/photos`
Параметры (все необязательные):
- `sol` — целое ≥ 0;
- `earth_date` — `YYYY-MM-DD`; вместе с `sol` → `422`;
- `camera` — код камеры без учёта регистра; неизвестный для этого марсохода код → `422`, в `details` — список допустимых кодов;
- `sample_type` — `full`, `subframe`, `downsampled`; без параметра — все типы;
- `page`, `per_page`.

Сортировка всегда: `sol DESC, taken_at DESC, id DESC`.
`200` список Photo · `404 rover_not_found` · `422 validation_error`

### `GET /api/v2/rovers/{rover}/photos/latest`
Фото за `max_sol` марсохода. Параметры: `camera`, `page`, `per_page`.
`200 {"sol": 2031, "items": [...], "page": 1, "per_page": 25, "total": 52}`. Если данных нет — `sol: null`, пустой список.

### `GET /api/v2/photos/{id}`
`200 PhotoDetail` · `404 photo_not_found`

### `GET /api/v2/photos/{id}/image?size=full|large|medium|small`
`size` по умолчанию `full`. Ответ `302`, `Location` — URL NASA, `Cache-Control: public, max-age=86400`. Если нужного размера нет — редирект на `full`.
`404 photo_not_found` · `422` на неверный `size`.

**Spirit и Opportunity, `size=full` (этап 9a, решение владельца).** Полного JPEG у MER нет — только продукт PDS3 `.img`. Для них ответ — сама картинка, не редирект:
- сервер скачивает `.img` по `source.link`, разбирает PDS3 (метка, растр; 8 или 16 бит), растягивает яркость по снимку (отсечка 0,5% самых тёмных и 0,5% самых светлых точек, линейно в 0–255) и отдаёт `200 image/jpeg`, качество 90;
- `Cache-Control: public, max-age=86400`, слабый `ETag`, `304` по `If-None-Match`; `HEAD` — те же заголовки без тела;
- готовые картинки хранятся в кэше на диске (`MER_IMAGE_CACHE_DIR`, лимит `MER_IMAGE_CACHE_MAX_MB`, по умолчанию 5120; при переполнении удаляются давно не запрошенные); повторный запрос отдаётся из кэша без обращения к PDS;
- одновременно не больше `MER_CONVERT_CONCURRENCY` конвертаций (по умолчанию 2), в очереди ждут не больше `MER_CONVERT_QUEUE` запросов разных снимков (по умолчанию 8); сверх этого — сразу `503 converter_busy` с `Retry-After: 10` и `no-store`; одинаковые запросы одного снимка ждут одну конвертацию; запросы к PDS — через свой экземпляр клиента источников (ретраи, лимит `MER_PDS_MAX_RPS`, только хост `MER_PDS_BASE_URL`);
- `size=small` → `302` на превью PDS; `medium` и `large` у MER нет → откат на `full` (конвертация), как у всех;
- ошибки: PDS недоступен или вернул ошибку → `502 upstream_error`; не уложились в `MER_CONVERT_TIMEOUT_SECONDS` (по умолчанию 60) → `504 upstream_timeout`; файл не разбирается как PDS3-изображение → `502 upstream_error`. Ошибки не кэшируются, `Cache-Control: no-store`.
- Curiosity и Perseverance — без изменений (`302`).
- Снимок MER без `source.link` (исходного `.img` нет): `images.full` и `img_src` — превью PDS, `size=full` → `302` на превью.
- Запросы к PDS у конвертера идут через свой экземпляр общего клиента источников со своим лимитом частоты (`MER_PDS_MAX_RPS`, по умолчанию 2) — синхронизация JPL с ним не делит лимит.

### `GET /api/v2/rovers/{rover}/manifest`
Параметры: `from_sol`, `to_sol` (необязательные, ограничивают диапазон).
```json
{"rover": Rover,
 "sols": [{"sol": 2031, "earth_date": "2026-11-06", "total_photos": 52, "cameras": ["NAVCAM_LEFT", "SUPERCAM_RMI"]}]}
```
Сортировка `sol DESC`. · `404 rover_not_found` · `422`, если `from_sol > to_sol`.

## Admin

Заголовок `X-Admin-Token`. Если `ADMIN_TOKEN` не задан в настройках, этих ручек нет (`404`).

### `POST /api/v2/admin/sync`
Тело: `{"rover": "perseverance", "from_sol": 2025, "to_sol": 2031}`. `from_sol` и `to_sol` необязательны: без них — инкремент.
`202 {"run_id": 17, "status": "running"}` · `401` · `404 rover_not_found` · `409 sync_in_progress` · `422`
`422 validation_error` также для `spirit` и `opportunity`: миссии завершены (Spirit — 2011, Opportunity — 2019), синхронизация не поддерживается, их снимки загружаются разовым импортом (этап 8).

### `GET /api/v2/admin/sync/runs?rover=&page=&per_page=`
`200` список:
```json
{"id": 17, "rover": "perseverance", "mode": "incremental", "from_sol": 2024, "to_sol": 2031,
 "status": "success", "started_at": "...", "finished_at": "...", "photos_upserted": 412, "failed_sols": [], "error": null}
```
Сортировка `started_at DESC`. `failed_sols` — солы, которые не удалось загрузить (по возрастанию). `401` · `404 rover_not_found` (неизвестный `rover`) · `422`.

## Ограничение частоты (v1 и v2)

Решение владельца, этап 9.
- Действует на все запросы к `/api/v1/*`, `/mars-photos/api/v1/*` и `/api/v2/*`, включая `HEAD`, `304` и admin. Не действует на `/`, `/health`, `/health/sync`, `/docs`, `/openapi.json`.
- Ключ — IP клиента. За доверенным прокси (`FORWARDED_ALLOW_IPS`) — из `X-Forwarded-For`, иначе — адрес соединения. `api_key` по-прежнему игнорируется, ключей нет.
- Два окна одновременно: `RATE_LIMIT_PER_MINUTE` (по умолчанию 60) и `RATE_LIMIT_PER_HOUR` (по умолчанию 2000). Превышение любого → `429`. Отклонённые запросы в счётчики не идут.
- Ответ `429`: v2 — `{"error": {"code": "rate_limited", "message": "Rate limit exceeded", "details": null}}`, v1 — `{"errors": "Rate limit exceeded"}`; заголовок `Retry-After` — целые секунды до освобождения места в окне (не меньше 1); `Cache-Control: no-store`.
- `RATE_LIMIT_ENABLED=false` выключает лимит (тесты, разработка).
- Admin, неверный токен: после `ADMIN_AUTH_MAX_FAILURES` (по умолчанию 10) ответов `401` с одного IP за `ADMIN_AUTH_WINDOW_MINUTES` (по умолчанию 15) все admin-запросы с этого IP получают `429 rate_limited` с `Retry-After` до конца окна — токен при этом не сверяется, даже верный. Успешная авторизация счётчик своего IP не сбрасывает.
- Счётчики в памяти процесса (приложение работает одним процессом), после перезапуска обнуляются.

## Кэширование (v1 и v2)

- `HEAD` поддерживается на всех `GET`-ручках v1 и v2: те же заголовки, без тела.
- Успешные `GET` (`200`) отдают `ETag`; повторный запрос с совпадающим `If-None-Match` получает `304` без тела.
- `Cache-Control`: списки, марсоходы, камеры и манифест — `public, max-age=300`; карточка снимка (`/photos/{id}`) — `public, max-age=3600`; редирект на изображение — `public, max-age=86400`; ошибки и admin-ручки — `no-store`.
- CORS — любые источники, только `GET`; заголовок `ETag` виден клиенту.
- Сжатие: при `Accept-Encoding: gzip` ответы от 1 КБ сжимаются; во всех ответах API — `Vary: Accept-Encoding`. `ETag` слабый и считается по несжатому телу, поэтому один и тот же для сжатого и несжатого ответа; `304` работает при любом кодировании.

## Черновики (уточнить перед своим этапом)

- **Положение** (этап 11): `/api/v2/rovers/{rover}/location`, `/api/v2/rovers/{rover}/traverse`, координаты в карточке снимка — контракт согласовать перед этапом. Погода отложена: живых источников нет (`DATA-SOURCES.md`).
