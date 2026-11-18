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
`img_src` всегда равен `images.full`. У Curiosity `small`, `medium` и `large` — `null`. У Spirit и Opportunity `images.full` — уменьшенный JPEG архива NASA PDS (низкое разрешение), `small`, `medium`, `large` и `title` — `null`, `source.link` — исходный продукт PDS `.img`.

**PhotoDetail** = Photo + `caption`, `received_at`, `site`, `drive`, `extra` (объект с техническими полями источника).

## Эндпоинты

### `GET /health`
- `200 {"status": "ok", "db": "ok"}`
- `503 {"status": "degraded", "db": "error"}`

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

## Кэширование (v1 и v2)

- `HEAD` поддерживается на всех `GET`-ручках v1 и v2: те же заголовки, без тела.
- Успешные `GET` (`200`) отдают `ETag`; повторный запрос с совпадающим `If-None-Match` получает `304` без тела.
- `Cache-Control`: списки, марсоходы, камеры и манифест — `public, max-age=300`; карточка снимка (`/photos/{id}`) — `public, max-age=3600`; редирект на изображение — `public, max-age=86400`; ошибки и admin-ручки — `no-store`.
- CORS — любые источники, только `GET`; заголовок `ETag` виден клиенту.

## Черновики (уточнить перед своим этапом)

- **Положение** (этап 10): `/api/v2/rovers/{rover}/location`, `/api/v2/rovers/{rover}/traverse`, координаты в карточке снимка — контракт согласовать перед этапом. Погода отложена: живых источников нет (`DATA-SOURCES.md`).
