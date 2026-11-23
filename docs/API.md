# Контракт API

Источник правды для `backend-dev` и `qa-engineer`. Меняется только с моего подтверждения: роль, которой нужно отступить от контракта, останавливается и сообщает об этом.

## Две версии

- **v1 — совместимая со старым Mars Rover Photos API.** Пути `/api/v1/...` и `/mars-photos/api/v1/...`, формат и поведение старого API. Контракт v1 — **`docs/LEGACY-API.md`**, а не этот файл. Это главный продукт: старые клиенты переезжают сменой хоста.
- **v2 — наш собственный API**, описан ниже: все типы снимков, размеры изображений, реальная дата съёмки, единый формат ошибок. Под него будет делаться фронтенд.

Обе версии читают одни и те же таблицы через одни и те же сервисы; различаются только роутеры и схемы ответов.

## Общие правила v2

- Базовый путь `/api/v2`, JSON (кроме `feed.xml` — RSS, XML; этап 16), UTF-8. Даты — `YYYY-MM-DD`, время — ISO 8601 в UTC с `Z`.
- **Ключ API (этап 20).** Ключ необязателен: без ключа действует лимит по IP, с ключом — расширенный лимит учётной записи (см. «Ограничение частоты»). Ключ — строка `si_` и 32 латинские буквы и цифры; выдаёт его администратор (самостоятельная выдача по e-mail — позже). Передаётся заголовком `X-Api-Key` (рекомендуется: не попадает в журналы прокси и историю браузера) или параметром `api_key`. Если есть оба — действует заголовок, параметр не смотрится. Значение `api_key` не нашего формата (например, ключ api.nasa.gov или `DEMO_KEY`) игнорируется — запрос идёт без ключа; значение заголовка `X-Api-Key` не нашего формата → `401 invalid_api_key`. Неверный, отозванный ключ и ключ заблокированной учётной записи не переводят запрос на уровень без ключа — ответ ошибкой. Пока ключи выключены (`API_KEYS_ENABLED=false`), `api_key` и `X-Api-Key` принимаются и игнорируются, как до этапа 20.
- Ошибки ключа (этап 20), у всех `Cache-Control: no-store` и `X-RateLimit-*` лимита по IP:

  | Код | `code` | Когда |
  |---|---|---|
  | 401 | `invalid_api_key` | ключ нашего формата не найден или заголовок `X-Api-Key` не нашего формата |
  | 401 | `api_key_revoked` | ключ отозван |
  | 403 | `account_blocked` | учётная запись ключа заблокирована |
  | 409 | `key_limit_reached` | у учётной записи уже `API_KEY_MAX_PER_ACCOUNT` (2) действующих ключей (admin-выдача) |

  Пример: `{"error": {"code": "api_key_revoked", "message": "API key has been revoked", "details": null}}`.
- `{rover}` — slug: `curiosity`, `perseverance`, `opportunity`, `spirit`, `ingenuity` (этап 12). Регистр не важен.
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
  | 404 | `rover_not_found`, `photo_not_found`, `flight_not_found`, `sample_not_found`, `not_found` | нет сущности или пути |
  | 422 | `validation_error` | неверные параметры; `details` — список `{"field", "message"}` |
  | 401 | `unauthorized` | admin-ручка без верного `X-Admin-Token` |
  | 409 | `sync_in_progress` | синхронизация марсохода уже идёт |
  | 502 | `upstream_error` | источник (NASA PDS) недоступен или отдал неразборчивые данные — конвертация снимка MER |
  | 503 | `converter_busy` | очередь конвертации снимков MER заполнена; `Retry-After` |
  | 504 | `upstream_timeout` | конвертация снимка MER не уложилась во время |
  | 429 | `rate_limited` | превышен лимит запросов (см. «Ограничение частоты») |
  | 503 | `db_unavailable` | база недоступна |
  | 503 | `service_busy` | сервер перегружен: запрос к базе не уложился в лимит времени, нет свободного соединения или заняты все места для тяжёлых поисков; `Retry-After` (этап 18B) |
- В каждом фото есть `credit` — авторство NASA/JPL-Caltech и партнёров.
- Границы чисел: `sol`, `from_sol`, `to_sol`, `sol_min`, `sol_max`, `site_min`, `site_max` (этап 18B) — от 0 до 2 147 483 647; `page` — от 1 до 1 000 000; за пределами → `422 validation_error`. `id` снимка вне диапазона (≤ 0 или больше 2⁶³−1) → `404 photo_not_found`. `{rover}`, который не может быть slug (не только латинские буквы), → `404 rover_not_found` без обращения к базе.
- `fields` (этап 19) — выборка ключей снимка в списках: `GET /api/v2/photos`, `/api/v2/rovers/{rover}/photos`, `/api/v2/rovers/{rover}/photos/latest`, `/api/v2/photos/batch`. Ключи Photo через запятую без пробелов, в нижнем регистре: `id`, `rover`, `sol`, `earth_date`, `taken_at`, `camera`, `sample_type`, `title`, `img_src`, `images`, `credit`, `source`, `width`, `height`, `local_time`, `mast`, `near_stop`. `id` и `credit` есть всегда, даже если не названы. Ключи идут в обычном порядке Photo, а не в порядке запроса; повторы схлопываются. Выбирается ключ целиком: вложенных путей (`images.small`) нет. Обёртка ответа (`page`, `per_page`, `total`, `nearest_sols`, `sol` у `/latest`, `missing` у `/batch`) не меняется. `near_stop` выводится, только если задан `near` и ключ выбран. Пусто, пустой элемент, неизвестный ключ, другой регистр, строка длиннее 300 символов, параметр повторён → `422`. Без `fields` — полный Photo. Другие ручки параметр не знают и игнорируют. `fields` не делает поиск быстрее и не влияет на тяжёлые поиски и лимиты: экономится только размер ответа.
  ```
  GET /api/v2/photos?rovers=perseverance&sol=2031&fields=sol,img_src,camera
  {"items": [{"id": 81234, "sol": 2031,
              "camera": {"code": "SUPERCAM_RMI", "name": "SuperCam Remote Micro Imager"},
              "img_src": "https://mars.nasa.gov/.../LRF_2031_..._0100I4J01.png",
              "credit": "NASA/JPL-Caltech/LANL/CNES/IRAP"}],
   "page": 1, "per_page": 25, "total": 52}
  ```
- `sample_type` в фильтре — только `full`, `subframe`, `downsampled`. В ответах встречаются и другие значения источника (у Curiosity — `mixed`, `chemcam prc`): такие снимки видны без фильтра.

## Объекты

**Rover**
```json
{"id": "perseverance", "name": "Perseverance", "mission": "mars2020",
 "launch_date": "2020-07-30", "landing_date": "2021-02-18", "end_date": null, "status": "active",
 "max_sol": 2031, "max_date": "2026-11-06", "total_photos": 579518,
 "cameras_count": 23, "last_synced_at": "2026-11-17T06:00:12Z", "data_source": "sync"}
```
`max_sol`, `max_date` и `last_synced_at` равны `null`, пока данных нет.
- `end_date` — дата официального завершения миссии; `null` у действующих. Spirit — `2011-05-25`, Opportunity — `2019-02-13` (справочные даты, не из источника). Последний снимок — по-прежнему `max_date`.
- `data_source` — откуда снимки: `"sync"` — регулярная синхронизация с фидом (Curiosity, Perseverance), `"archive"` — разовый импорт архива (Spirit, Opportunity); у `"archive"` `last_synced_at` — время импорта.
- `ingenuity` (этап 12) — вертолёт Ingenuity (миссия Mars 2020). Солы — солы Perseverance; `landing_date` — посадка вместе с Perseverance, `end_date` — `2024-01-25`, `status: "complete"`, `data_source: "archive"` (разовая загрузка фида, `last_synced_at` — время загрузки). Камеры `HELI_NAV`, `HELI_RTE`. В v1 Ingenuity нет.

**Camera**
```json
{"code": "NAVCAM_LEFT", "name": "Left Navigation Camera (Navcam)", "total_photos": 12345}
```
В `/cameras?sol=N` `total_photos` — число снимков камеры за этот сол (см. эндпоинт).

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
            "link": "https://mars.nasa.gov/mars2020/multimedia/raw-images/LRF_..."},
 "width": 1648, "height": 1200,
 "local_time": {"time": "17:40:12", "kind": "lmst", "planet": "mars"},
 "mast": {"azimuth": 309.89, "elevation": 16.9}}
```
`local_time` — местное солнечное время съёмки **на Марсе** (не на Земле: земное время съёмки — `taken_at`, UTC), в точке, где стоял марсоход. `time` — `HH:MM:SS` по марсианским суткам (24 марсианских часа, сол ≈ 24 ч 39 мин 35 с), без долей секунды. `kind`: `"lmst"` — среднее солнечное время (Curiosity, Perseverance, Ingenuity), `"ltst"` — истинное, по положению Солнца (Spirit, Opportunity; отличается от среднего до ~50 мин). `planet` — всегда `"mars"`: явная пометка, что это не земные часы. `null`, если источник время не дал: у Curiosity — примерно у половины снимков, у Perseverance — у единичных снимков. Ключ есть у Photo в списках и у PhotoDetail.

`mast` (этап 19) — направление камеры в момент съёмки по данным источника, градусы: `azimuth` — от 0 (включительно) до 360 (не включительно), `elevation` — от −90 до 90 (отрицательное — вниз), оба округлены до 0,01. Отсчёт — **от корпуса марсохода, а не от севера**: 0 — примерно вперёд по ходу, 180 — назад; сторону света по этому числу не определить. Источник называет поля «mast» (мачта), но даёт их и для неподвижных камер на корпусе (Hazcam) — у них угол постоянный. Perseverance в разные периоды пишет азимут то от 0 до 360, то от −180 до 180; здесь он всегда приведён к 0–360 (исходная строка — в `extra` карточки). `null`, если источник углов не дал или они неразборчивы: у Curiosity — примерно у половины снимков, у Perseverance — у ~6%, у Spirit, Opportunity и Ingenuity — всегда. По отдельности `azimuth` и `elevation` `null` не бывают. Ключ есть у Photo в списках и у PhotoDetail (после `local_time`).

`width`, `height` — размер полного кадра в пикселях по данным источника (у Spirit и Opportunity — исходного продукта PDS); по ним можно заранее задать пропорции любого размера из `images`. Оба `null`, если источник размер не даёт (у Curiosity — всегда) или он неразборчив; по отдельности `null` не бывают.

`img_src` всегда равен `images.full`. У Curiosity `small`, `medium` и `large` — `null`. У Spirit и Opportunity (этап 9a, решение владельца): `images.small` — уменьшенный JPEG архива NASA PDS (превью), `images.full` (и `img_src`) — абсолютная ссылка на нашу конвертацию полного кадра `{origin}/api/v2/photos/{id}/image?size=full` (`origin` — от хоста текущего запроса, как на странице `/`), `medium`, `large` и `title` — `null`, `source.link` — исходный продукт PDS `.img`.

**PhotoDetail** = Photo (включая `local_time` и `mast`, см. выше) + `caption`, `received_at`, `site`, `drive`, `extra` (объект с техническими полями источника), `location` (этап 11), `in_sol`, `flight` (этап 12), `samples` (этап 13).
```json
"local_time": {"time": "11:25:43", "kind": "lmst", "planet": "mars"},
"in_sol": {"position": 17, "total": 52, "newer_id": 81233, "older_id": 81240}
```
- `local_time` — как у Photo; в PhotoDetail ключ стоит на прежнем месте (после `location`), за ним — `mast` (этап 19), затем `in_sol`.
- `in_sol` — место снимка в его соле в порядке списка `/photos` (`taken_at DESC, id DESC`): `position` — номер с 1, `total` — снимков в соле, `newer_id` — предыдущий в списке (снят позже), `older_id` — следующий (снят раньше); `null` у краёв. Учитывает параметры `camera` и `sample_type` запроса `/photos/{id}` (см. эндпоинт). Карточка кэшируется на час — у последних солов, куда снимки ещё доходят, `in_sol` может отставать на это время.
- `flight` (этап 12, последним ключом) — у снимков Ingenuity номер полёта, совершённого в сол снимка, или `null` (в этот сол полёта не было); у остальных марсоходов всегда `null`. Связь только по солу: снимок мог быть сделан до или после полёта в тот же сол. У Ingenuity `location` всегда `null`, `site`/`drive` — `null`.
- `samples` (этап 13, последним ключом) — номера образцов Perseverance, запечатанных в сол снимка, по возрастанию; `[]`, если в этот сол образцов нет, и у остальных марсоходов. Связь только по солу: снимок не обязательно изображает образец (пробирку снимает камера `CACHECAM`).

## Эндпоинты

### `GET /health`
- `200 {"status": "ok", "db": "ok"}`
- `503 {"status": "degraded", "db": "error"}`
- `HEAD /health` — тот же статус без тела (для внешнего мониторинга, этап 9).

### `GET /health/sync`
Свежесть данных — для внешнего мониторинга (этап 9). `HEAD` — тот же статус и заголовки без тела (UptimeRobot по умолчанию проверяет методом `HEAD`). Без авторизации, без лимита частоты, `Cache-Control: no-store`, секретов и текстов ошибок не содержит.
Учитываются только марсоходы с синхронизацией (`curiosity`, `perseverance`); Spirit, Opportunity и Ingenuity — нет.
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

### `GET /developers`
Страница для разработчиков (до этапа 10 была на `/`) (HTML, английский): что это за сервис и что он неофициальный, как переехать со старого API (сменить хост), отличия от старого API, примеры запросов v1 и v2, ссылка на `/docs`, авторство снимков. Раздел «Migrating» (этап 18A): три шага переезда (сменить хост; ключ не нужен; работают оба пути `/api/v1` и `/mars-photos/api/v1`) и таблица отличий от старого API по `LEGACY-API.md`; другие сервисы не упоминаются. Адреса в примерах — от хоста текущего запроса; некорректный или враждебный `Host` в страницу не попадает (подставляется адрес сервера), всё выводимое экранируется. Без логотипов NASA и JPL. `Cache-Control: public, max-age=3600`, лимит частоты не действует.

### `GET /llms.txt` (этап 18A)
Краткая справка о сервисе для LLM-ассистентов в формате llmstxt.org (Markdown), `200 text/plain; charset=utf-8`: что это за сервис и что он неофициальный, базовый адрес (от хоста текущего запроса, по тем же правилам, что у `/developers`), главные ручки v1 и v2 — по одной строке, ссылки на `/openapi.json`, `/docs`, `/developers`, авторство снимков. `HEAD` — те же заголовки без тела. `Cache-Control: public, max-age=3600`, `ETag`, лимит частоты не действует; интерфейс этот путь не перехватывает.

### Веб-интерфейс Sol Index (этап 10)
Решение владельца и тимлида: интерфейс для людей на `/`, только на API v2, код в `frontend/`, раздаёт тот же сервер.
- `GET /` и любые пути интерфейса (`/rovers/{rover}`, `/rovers/{rover}/timeline`, `/photos/{id}` и прочие, которых нет в API) → `200 text/html`, `index.html` интерфейса; маршрутизацию и страницу «не найдено» делает сам интерфейс. `Cache-Control: no-cache`.
- Файлы сборки с хешем в имени (`/assets/*`) → `Cache-Control: public, max-age=31536000, immutable`; несуществующий файл в `/assets/` → `404`.
- Не перехватываются интерфейсом и ведут себя как раньше: `/api/*`, `/mars-photos/*`, `/health`, `/health/sync`, `/docs`, `/redoc`, `/openapi.json`, `/developers`, `/llms.txt`. Неизвестный путь под `/api/` — прежний JSON `404`.
- Интерфейс и статика не входят в лимит частоты.
- Если сборки интерфейса нет (разработка без `npm run build`), `/` отдаёт страницу для разработчиков, как до этапа 10.
- Прочие файлы верхнего уровня сборки (`/favicon.svg`, `/robots.txt`) → `Cache-Control: public, max-age=3600`. `index.html` — с `ETag`, повтор с `If-None-Match` → `304`.
- Методы, кроме `GET` и `HEAD`, на путях интерфейса → JSON `404` в формате v2.
- Наличие сборки определяется при старте сервера: после новой сборки нужен перезапуск.

### `GET /api/v2/rovers`
`200 {"items": [Rover, ...]}` — без пагинации, сортировка по `id`.

### `GET /api/v2/rovers/{rover}`
`200 Rover` · `404 rover_not_found`

### `GET /api/v2/rovers/{rover}/cameras`
Параметр (необязательный): `sol` — целое ≥ 0, границы общие.
`200 {"items": [Camera, ...]}`, сортировка по `code`. Без `sol` `total_photos` — по всей миссии; с `sol` — за этот сол, в списке все камеры марсохода, у не снимавших `total_photos: 0` (сол без снимков — все нули). · `404 rover_not_found` · `422 validation_error`

### `GET /api/v2/rovers/{rover}/photos`
Параметры (все необязательные):
- `sol` — целое ≥ 0;
- `earth_date` — `YYYY-MM-DD`; вместе с `sol` → `422`;
- `camera` — код камеры без учёта регистра; неизвестный для этого марсохода код → `422`, в `details` — список допустимых кодов;
- `sample_type` — `full`, `subframe`, `downsampled`; без параметра — все типы;
- `site` — целое ≥ 0; `drive_from`, `drive_to` — целые ≥ 0, только вместе с `site` (иначе `422`), включительно, любой можно опустить; `drive_from > drive_to` → `422`. Снимки без `site`/`drive` под эти фильтры не попадают. Сочетаются с остальными параметрами (И). Рецепт для стоянки `(S, D)` из `/location`: «снято на стоянке» — `site=S&drive_from=D&drive_to=D`; «как в `location` карточки» (на стоянке и в пути до следующей) — `site=S&drive_from=D&drive_to={drive следующей стоянки того же site} − 1` (у последней стоянки `site` — без `drive_to`). Снимки стоянки могут относиться к нескольким солам;
- `sol_min`/`sol_max`, `date_min`/`date_max`, `site_min`/`site_max`, `sort`, список в `camera`, а также `local_time_min`/`local_time_max`, `golden_hour`, `min_width`/`min_height` (этап 18B-2) — как у `GET /api/v2/photos` (этап 18B), с теми же правилами сочетания; потолка `page × per_page` нет, кроме запросов со списком камер (в `camera` есть запятая, даже если коды повторяются): для них `page × per_page` не больше 10 000, иначе `422` (сузьте диапазон солов или дат);
- `near`/`radius_km` (этап 18B-3) — как у `GET /api/v2/photos`; только для `curiosity` и `perseverance` в пути, для остальных марсоходов → `422`; с `near` `page × per_page` не больше 10 000, иначе `422`;
- `fields` (этап 19) — выборка ключей снимка, см. «Общие правила v2».
- `page`, `per_page`.

Сортировка — параметр `sort`, по умолчанию `sol DESC, taken_at DESC, id DESC`. Для запросов без новых параметров ничего не меняется.
Ответ — список Photo с дополнительным ключом `nearest_sols`:
```json
{"items": [], "page": 1, "per_page": 25, "total": 0, "nearest_sols": {"before": 2028, "after": 2030}}
```
- `nearest_sols` — не `null` только если задан `sol` или `earth_date` и `total = 0`. `before` — наибольший сол со снимками меньше запрошенного, `after` — наименьший больше; `null`, если такого нет. Для `earth_date` сравнивается самая ранняя дата съёмки сола (`manifest.sols[].earth_date`). Фильтры `camera`, `sample_type`, `site`, `drive_from`, `drive_to` не учитываются: это ближайшие солы, где есть хоть какие-то снимки.
- В остальных случаях `"nearest_sols": null`.

`200` список Photo · `404 rover_not_found` · `422 validation_error`

### `GET /api/v2/rovers/{rover}/photos/latest`
Фото за `max_sol` марсохода (по всем типам). Параметры: `camera` (один код, как до этапа 18B), `sample_type` (как у `/rovers/{rover}/photos`), `fields` (этап 19, см. «Общие правила v2»), `page`, `per_page`. `sol` в ответе — всегда `max_sol`, даже если с фильтрами снимков нет (`total: 0`).
`200 {"sol": 2031, "items": [...], "page": 1, "per_page": 25, "total": 52}`. Если данных нет — `sol: null`, пустой список.

### `GET /api/v2/photos` (этап 18B)
Снимки нескольких марсоходов одним списком. Параметры (все необязательные):
- `rovers` — slug через запятую без пробелов, без учёта регистра, повторы схлопываются; без параметра — все марсоходы. Пустой элемент, неизвестный slug, строка длиннее 200 символов → `422`;
- `sol` или диапазон `sol_min`/`sol_max` (включительно, любой край можно опустить). Солы у каждого марсохода свои (у `ingenuity` — солы Perseverance);
- `earth_date` или диапазон `date_min`/`date_max` (`YYYY-MM-DD`, включительно) — реальная дата съёмки (UTC);
- солы и даты в одном запросе не сочетаются; `sol` с `sol_min`/`sol_max`, `earth_date` с `date_min`/`date_max` — тоже; `min > max` → `422`;
- `camera` — один или несколько кодов через запятую (до 10), без учёта регистра; у каждого марсохода отбираются его камеры из списка; код, которого нет ни у одного выбранного марсохода, → `422`, в `details` — допустимые коды по марсоходам;
- `sample_type` — как у `/rovers/{rover}/photos`;
- `site` (с `drive_from`/`drive_to`) или диапазон `site_min`/`site_max` — только при ровно одном марсоходе в `rovers`, иначе `422`; `site` с `site_min`/`site_max` → `422`; снимки без `site` не попадают;
- `sort` — `-sol` (по умолчанию: `sol DESC, taken_at DESC, id DESC`), `sol` (`sol, taken_at, id` по возрастанию), `-taken_at` (`taken_at DESC, id DESC`), `taken_at`; иное → `422`. При нескольких марсоходах `-sol` смешивает разные эпохи — для ленты «новое на Марсе» берите `-taken_at`;
- `local_time_min`, `local_time_max` (этап 18B-2) — местное солнечное время съёмки `HH:MM` (00:00-23:59); отбор `min ≤ t < max`; при `min > max` — через полночь (`t ≥ min` или `t < max`); один край — открытый; `min = max` или другой формат → `422`. Шкала — как `local_time.kind` снимка: LMST у Curiosity, Perseverance и Ingenuity, LTST у Spirit и Opportunity (отличается до ~50 мин) — в запросе по нескольким марсоходам одно окно применяется к каждой шкале как есть. Снимки без местного времени не попадают: у Curiosity ~половина, у Perseverance — единичные;
- `golden_hour` (этап 18B-2) — `true`/`false` (`1`/`0`), по умолчанию `false`; `true` = окна `05:30-07:00` и `17:00-18:30` местного времени. Упрощение: настоящие восход и закат сдвигаются по сезону на десятки минут. Вместе с `local_time_*` → `422`;
- `min_width`, `min_height` (этап 18B-2) — целые 1-32 767, размер полного кадра не меньше заданного; снимки без размера (Curiosity — все) и с размером больше 32 767 по любой стороне (таких в данных нет) не попадают;
- `near=LAT,LON` и `radius_km` (этап 18B-3) — снимки, сделанные на стоянках марсохода в пределах `radius_km` километров от точки. `LAT` — широта от −90 до 90, `LON` — долгота от −180 до 180 (градусы, восточная долгота положительна, как в `/location`); `radius_km` — от 0,01 до 50, по умолчанию 1; `radius_km` без `near` или другой формат → `422`. Только для `curiosity` и `perseverance`: нужен ровно один из них в `rovers`, иначе `422`. Снимок привязывается к стоянке по тому же правилу, что `location` в карточке (стоянка того же `site` с наибольшим `drive` ≤ `drive` снимка); снимки без `site`/`drive`, с `site`, где нет стоянок, или сделанные до первой стоянки своего `site` не находятся (Curiosity — около половины снимков, Perseverance — снимки посадки, солы ≤ 9). Весь район работ марсохода — 5-25 км, так что радиус от 10 км обычно захватывает почти всё. Сочетается с остальными фильтрами и сортировкой;
- `fields` (этап 19) — выборка ключей снимка, см. «Общие правила v2».
- `page`, `per_page` — как везде; `page × per_page` не больше 10 000, иначе `422` (сузьте диапазон).

Ответ — список Photo `{"items": [...], "page": 1, "per_page": 25, "total": 48209}`, без `nearest_sols`. `Cache-Control: public, max-age=300`, лимит частоты действует. `200` · `422 validation_error`.

При `near` у каждого снимка есть ещё ключ `near_stop` (последним) — стоянка, к которой привязан снимок (по тому же правилу, что `location` в PhotoDetail), и расстояние от неё до точки запроса:
```json
"near_stop": {"sol": 2020, "site": 93, "drive": 1840, "lat": 18.4447, "lon": 77.3951, "distance_km": 0.42}
```
`distance_km` — по дуге большого круга (радиус Марса 3389,5 км), округлено до 3 знаков после запятой, не больше `radius_km`. Без `near` ключа нет.

### `GET /api/v2/photos/stats` (этап 18C)
Сводка: сколько снимков и солов, первый и последний сол и дата — по марсоходам, камерам, годам или месяцам. Считается по сводкам синхронизации (как манифест), поэтому быстрый и не относится к тяжёлым поискам. Параметры (все необязательные):
- `rovers` — как у `GET /api/v2/photos`; без параметра — все марсоходы;
- `camera` — один или несколько кодов через запятую (до 10), правила — как у `GET /api/v2/photos`;
- `sol_min`/`sol_max` — диапазон солов, включительно, любой край можно опустить; солы у каждого марсохода свои (у `ingenuity` — солы Perseverance);
- `date_min`/`date_max` (`YYYY-MM-DD`, включительно) — по **дате сола** (`earth_date` в манифесте — самая ранняя дата съёмки сола), а не по дате каждого снимка, как у `/photos`: снимки сола, перешедшего через полночь UTC, считаются в дату его начала. Солы и даты в одном запросе не сочетаются; `min > max` → `422`;
- `sample_type` — только `full` (полные кадры); без параметра — все типы; другое значение → `422`;
- `group_by` — `rover` (по умолчанию), `camera` (марсоход + камера), `year`, `month` (марсоход + год или месяц по дате сола, UTC); иное → `422`. По солам — `GET /api/v2/rovers/{rover}/manifest`.

Другие фильтры `/photos` (местное время, размер кадра, стоянки, `near`, `sample_type` кроме `full`) здесь не поддерживаются и игнорируются как неизвестные параметры.

```json
{"group_by": "camera",
 "total_photos": 48209, "sols_with_photos": 101,
 "first_date": "2021-06-01", "last_date": "2021-09-14",
 "groups": [
   {"rover": "perseverance", "camera": "NAVCAM_LEFT", "period": null,
    "total_photos": 12345, "sols_with_photos": 98,
    "first_sol": 100, "last_sol": 200, "first_date": "2021-06-01", "last_date": "2021-09-14"}
 ]}
```
- `total_photos`, `sols_with_photos` верхнего уровня — по всей выборке (солы разных марсоходов считаются отдельно); `first_date`/`last_date` — по всей выборке. `first_sol`/`last_sol` есть только у групп: солы разных марсоходов несравнимы.
- `camera` — код камеры при `group_by=camera`, иначе `null`; `period` — `"YYYY"` или `"YYYY-MM"` при `group_by=year|month`, иначе `null`.
- Порядок групп: по марсоходу (как в `/api/v2/rovers`), затем по камере (как в `/rovers/{rover}/cameras`) или по `period` по возрастанию. Группы без снимков не выводятся; пустая выборка — `total_photos: 0`, `sols_with_photos: 0`, даты `null`, `groups: []`.
- Числа совпадают с `total` у `GET /api/v2/photos` с теми же `rovers`, `camera`, `sol_min`/`sol_max`, `sample_type` (по датам — нет, см. выше). Сводки обновляются вместе с синхронизацией.

`200` · `422 validation_error`

### `GET /api/v2/photos/batch?ids=…` (этап 18C)
Снимки по списку id одним запросом — например, для избранного или ссылок.
- `ids` — обязательный: id через запятую без пробелов, от 1 до 100 элементов (повторы считаются), строка не длиннее 2000 символов, каждый элемент — от 1 до 19 цифр. Пусто, пустой элемент, не цифры, больше 100 элементов, параметр повторён → `422`. Повторы схлопываются.
- Ответ — `{"items": [Photo, ...], "missing": [id, ...]}`: `items` — найденные снимки (объект Photo, как в списках) в порядке первого появления id в запросе; `missing` — ненайденные id в том же порядке, включая id вне диапазона (`0`, больше 2⁶³−1). Ненайденные id не дают `404`.
- Снимки разных марсоходов в одном ответе допустимы.
- `fields` (этап 19) — выборка ключей снимка, см. «Общие правила v2».

```json
{"items": [{"id": 81234, "rover": "perseverance", "sol": 2031, "...": "..."}],
 "missing": [999999999]}
```
`200` · `422 validation_error`


### `GET /api/v2/photos/{id}`
Параметры (необязательные, влияют только на `in_sol`): `camera` (один код), `sample_type` — как у `/rovers/{rover}/photos`; неизвестная для марсохода снимка камера или неверный `sample_type` → `422`. С фильтрами `in_sol` считается по снимкам сола, подходящим под них; если сам снимок не подходит — `position: null`, а `newer_id`/`older_id` — ближайшие подходящие по порядку списка.
`200 PhotoDetail` · `404 photo_not_found` · `422 validation_error`

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
Сортировка `sol DESC`; солы без снимков в `sols` отсутствуют. · `404 rover_not_found` · `422`, если `from_sol > to_sol`.

**Waypoint** (стоянка марсохода)
```json
{"sol": 2020, "site": 93, "drive": 1840,
 "lat": 18.4447, "lon": 77.3951, "elev_geoid": -2489.7,
 "easting": 4086431.2, "northing": 1092178.4,
 "roll": 1.2, "pitch": -3.4, "yaw": 142.0, "tilt": 3.6,
 "dist_m": 112.4, "dist_total_m": 33874.9}
```
Источник — слои карт MMGIS (JPL). `lat`, `lon` — градусы, долгота восточная от −180 до 180; `elev_geoid` — высота над ареоидом, м; `easting`/`northing` — м в проекции карты источника; `roll`, `pitch`, `yaw`, `tilt` — градусы; `dist_m` — длина проезда до стоянки, м; `dist_total_m` — пройденное расстояние по данным источника, м (не обязательно растёт от стоянки к стоянке). Любое поле, кроме `sol`, `site`, `drive`, `lat`, `lon`, может быть `null`.

### `GET /api/v2/rovers/{rover}/location`
Стоянки марсохода. Параметры (необязательные): `sol` — целое ≥ 0; `from_sol`, `to_sol` — диапазон включительно. `sol` вместе с `from_sol` или `to_sol` → `422`; `from_sol > to_sol` → `422`; границы — общие. Без пагинации.
- `latest` — `true` или `false` (также `1`/`0`), по умолчанию `false`; другое значение → `422`. При `true` в `items` — только последняя стоянка (наибольшие `sol`, `site`, `drive`) или `[]`, если стоянок нет; вместе с `sol`, `from_sol` или `to_sol` → `422`. Остальные члены ответа — как без параметра.
```json
{"rover": "perseverance", "available": true,
 "updated_at": "2026-10-28T15:05:06Z", "source_max_sol": 2022,
 "items": [Waypoint, ...]}
```
- Сортировка `sol ASC, site ASC, drive ASC` (порядок движения).
- `available` — есть ли у марсохода источник положения: `true` у Curiosity и Perseverance, `false` у Spirit и Opportunity (источник неизвестен; тогда `items: []`, `updated_at` и `source_max_sol` — `null`).
- `updated_at` — когда стоянки последний раз изменились у нас; `source_max_sol` — последний сол стоянок у источника. Оба `null`, пока данных нет. Источник обновляется нерегулярно (бывает, неделями), поэтому `source_max_sol` может быть заметно меньше `max_sol` марсохода.
- Данные загружаются не чаще раза в сутки.

`200` · `404 rover_not_found` · `422 validation_error`

### `GET /api/v2/rovers/{rover}/traverse`
Весь маршрут марсохода в формате GeoJSON (RFC 7946), `Content-Type: application/geo+json`. Параметров нет (неизвестные игнорируются): маршрут отдаётся всегда целиком; стоянки за нужные солы — через `/location`.
```json
{"type": "FeatureCollection",
 "rover": "perseverance", "available": true,
 "updated_at": "2026-10-28T15:05:03Z", "source_max_sol": 2022,
 "features": [
   {"type": "Feature",
    "geometry": {"type": "LineString", "coordinates": [[77.4509, 18.4447], [77.4511, 18.4449]]},
    "properties": {"sol": 15, "length_m": 23.6}}]}
```
- Каждый объект — `LineString`, координаты `[lon, lat]` в градусах (долгота восточная, от −180 до 180), до 7 знаков после запятой, без высоты. Объекты — в порядке движения.
- `properties.sol` — сол проезда или `null`, если источник его не указывает (у Curiosity — всегда `null`); `properties.length_m` — длина по данным источника, м, или `null`.
- `rover`, `available`, `updated_at` — как у `/location` (дополнительные члены верхнего уровня, RFC 7946 §6.1); `updated_at` — когда маршрут последний раз изменился у нас; `source_max_sol` — наибольший `sol` среди объектов, `null`, если солов нет (Curiosity). У Spirit и Opportunity — `"available": false`, `"features": []`, остальное `null`.

`200` · `404 rover_not_found`

**PhotoDetail**: добавляется поле `location` (после `extra`, перед `local_time` и `in_sol`):
```json
"location": {"sol": 2020, "site": 93, "drive": 1840, "lat": 18.4447, "lon": 77.3951,
             "elev_geoid": -2489.7, "exact": true}
```
- Берётся стоянка того же марсохода с тем же `site` и наибольшим `drive`, не большим `drive` снимка. `exact: true` — `drive` совпал (снимок сделан на этой стоянке); `false` — снимок сделан позже (в пути или после последней опубликованной стоянки), координаты — последней стоянки перед снимком (погрешность — до длины одного проезда, сотни метров).
- `location: null`, если у снимка нет `site` или `drive`, у марсохода нет источника положения (Spirit, Opportunity) или стоянки этого `site` с `drive` не больше снимкового нет.
- Photo в списках получает только `local_time` (см. «Объекты»); `location` — только в PhotoDetail.

**Flight** (этап 12; полёт Ingenuity, журнал NASA)
```json
{"flight": 9, "sol": 133, "date": "2021-07-05",
 "distance_m": 625, "max_altitude_m": 10, "max_groundspeed_mps": 5.0, "duration_s": 166.4,
 "from": "Airfield E", "to": "Airfield F", "total_photos": 312}
```
- `date` — дата полёта по тихоокеанскому времени (так в журнале), не UTC; надёжный ключ — `sol`.
- `distance_m` — горизонтальная дистанция, м; `max_altitude_m` — м; `max_groundspeed_mps` — м/с; `duration_s` — с. Значения журнала без пересчёта; футы и мили не отдаются.
- `from` — площадка взлёта; `to` — площадка посадки, `null`, если в журнале не указана (в 27 полётах, обычно — посадка на той же площадке).
- `total_photos` — снимков Ingenuity за `sol` полёта (все типы); снимки — `/rovers/ingenuity/photos?sol={sol}`.

### `GET /api/v2/rovers/{rover}/flights`
Только `ingenuity`. `200 {"items": [Flight, ...]}` — все полёты без пагинации, сортировка `flight ASC`. Другой существующий марсоход → `404 not_found`; неизвестный → `404 rover_not_found`. `Cache-Control: public, max-age=300`.

### `GET /api/v2/rovers/{rover}/flights/{n}`
`n` — целое ≥ 1; нечисловое → `422`; нет такого полёта (в т.ч. `0`, отрицательные и любые числа больше 32 767) → `404 flight_not_found`. `200 Flight` · `404 rover_not_found` / `not_found` (как у списка).

**Sample** (этап 13; образец Perseverance; данные страницы NASA «Mars Rock Samples»)
```json
{"number": 25, "name": "Sapphire Canyon", "feature_name": "Cheyava Falls",
 "sampling_location": "Neretva Vallis",
 "status": "on_rover", "current_location": "Perseverance Rover",
 "sample_type": "rock_core", "sample_type_label": "Rock Core", "rock_type": "Sedimentary",
 "sealed_on": "2024-07-21", "sol": 1215, "height_cm": 6.2, "height_note": null,
 "dossier_url": "https://pds-geosciences.wustl.edu/m2020/urn-nasa-pds-mars2020_sample_dossier/initial_reports/initial_reports_volume4.pdf#page=120",
 "total_photos": 289,
 "location": {"sol": 1212, "site": 56, "drive": 0, "lat": 18.4447, "lon": 77.3951,
              "elev_geoid": -2489.7, "match": "exact"}}
```
(координаты в примере условные)
- `number` — порядковый номер образца на странице (не серийный номер пробирки). Строки — как на странице (имена с диакритикой и псевдонимами, `(aka "…")`, не разбираются).
- `feature_name`, `sampling_location`, `rock_type` — текст страницы или `null` (пусто, `n/a`).
- `status` — `on_rover` (на борту) или `depot` (в депо на поверхности); `null`, если на странице незнакомое значение; `current_location` — текст страницы всегда.
- `sample_type` — `rock_core`, `regolith`, `atmospheric` или `null` (незнакомое значение); `sample_type_label` — текст страницы.
- `sealed_on` — дата запечатывания как на странице (часовой пояс не указан), `sol` — сол; оба `null`, пока на странице `TBD`. Надёжный ключ — `sol`.
- `height_cm` — высота керна, см, или `null` (нет или только оценка); `height_note` — текст страницы, если высоту нельзя дать числом (`"Est. 5-7 cm (2-2.8 in)"`), иначе `null`.
- `dossier_url` — PDF научного досье в PDS или `null`.
- `total_photos` — снимков Perseverance за `sol` (все типы); `null` при `sol: null`. Снимки сола — `/rovers/perseverance/photos?sol={sol}`, пробирка — `&camera=CACHECAM`.
- `location` — стоянка, где стоял марсоход в сол запечатывания, или `null` (нет `sol`, нет стоянок): поля как у `location` в PhotoDetail, вместо `exact` — `match`: `exact` — `site`/`drive` снимков этого сола совпали со стоянкой; `nearest_before` — стоянка того же `site` с ближайшим меньшим `drive`; `by_sol` — снимков с `site`/`drive` в этот сол нет, пар несколько или для единственной пары нет стоянки того же `site` с `drive` не больше; взята последняя стоянка с `sol` не больше сола образца (в этом соле — с наибольшими `site`, `drive`).

### `GET /api/v2/rovers/{rover}/samples`
Только `perseverance`. Параметров нет (неизвестные игнорируются). Все образцы без пагинации, сортировка `number ASC`.
```json
{"rover": "perseverance",
 "source_url": "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/",
 "source_updated_at": "2026-06-20T16:45:46Z", "updated_at": "2026-11-21T03:00:12Z",
 "items": [Sample, ...]}
```
- `source_url` — страница-источник (данные NASA; проект неофициальный). `source_updated_at` — дата правки страницы по её метаданным или `null`; `updated_at` — когда список последний раз изменился у нас; оба `null` и `items: []`, пока данных нет.
- Страница обновляется с отставанием от миссии (месяцами): последних образцов в ней может не быть. Данные проверяются не чаще раза в сутки.
- Другой существующий марсоход → `404 not_found`; неизвестный → `404 rover_not_found`. `Cache-Control: public, max-age=3600`.

### `GET /api/v2/rovers/{rover}/samples/{number}`
`number` — целое ≥ 1; нечисловое → `422`; нет такого образца (в т.ч. `0`, отрицательные и больше 32 767) → `404 sample_not_found`. `200 Sample` · `404 rover_not_found` / `not_found` (как у списка). `Cache-Control: public, max-age=3600`.

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

### `GET /api/v2/rovers/{rover}/feed.xml` (этап 16)
RSS 2.0-лента новых снимков марсохода — для RSS-читалок. Только марсоходы с регулярной синхронизацией: `curiosity`, `perseverance`.
Параметры (все необязательные):
- `camera` — код камеры без учёта регистра; неизвестный для марсохода → `422` (как у `/photos`);
- `sample_type` — `full`, `subframe`, `downsampled`; иное → `422`;
- `group` — `sol` (по умолчанию) или `photo`; иное → `422`;
- `limit` — число записей, от 1 до 100, по умолчанию 20 при `group=sol` и 50 при `group=photo`; вне границ → `422`.
Неизвестные параметры игнорируются.

**Что считается новым.** Снимок попадает в ленту, если его впервые сохранила регулярная синхронизация (по расписанию или `POST /admin/sync` без `from_sol`/`to_sol`; с диапазоном это загрузка истории); время появления — момент сохранения у нас (`pubDate`). Загрузка истории (`backfill`), импорт архивов и повторная загрузка уже известных снимков в ленту не попадают. В ленте только снимки, появившиеся за последние 30 дней. Снимок старого сола, дошедший с запозданием, — тоже новый.

**Ответ** `200 application/rss+xml; charset=utf-8`:
```xml
<?xml version='1.0' encoding='utf-8'?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
 <channel>
  <title>Perseverance — new raw images (NAVCAM_LEFT) — Sol Index</title>
  <link>https://example.ts.net/rovers/perseverance?camera=NAVCAM_LEFT</link>
  <atom:link rel="self" type="application/rss+xml" href="https://example.ts.net/api/v2/rovers/perseverance/feed.xml?camera=NAVCAM_LEFT"/>
  <description>Unofficial feed. Images: NASA/JPL-Caltech and partners.</description>
  <language>en</language><ttl>60</ttl>
  <lastBuildDate>Sat, 21 Nov 2026 06:00:12 GMT</lastBuildDate>
  <item>
   <title>Perseverance Sol 2032: 52 new images, 5 cameras</title>
   <link>https://example.ts.net/rovers/perseverance?sol=2032&amp;camera=NAVCAM_LEFT</link>
   <guid isPermaLink="false">sol-index:perseverance:sol-2032:1795240812000123:camera-NAVCAM_LEFT</guid>
   <pubDate>Sat, 21 Nov 2026 06:00:12 GMT</pubDate>
   <description>&lt;p&gt;…&lt;/p&gt;</description>
   <media:thumbnail url="https://mars.nasa.gov/…_320.jpg"/>
  </item>
 </channel>
</rss>
```
- Все ссылки абсолютные, хост — от текущего запроса (как `origin` у Photo). `link` канала — страница марсохода в интерфейсе с теми же фильтрами; `atom:link self` — адрес ленты с нормализованными параметрами (`camera` в верхнем регистре, параметры по умолчанию опущены).
- `group=sol`: запись — снимки одного сола, сохранённые одной загрузкой (сол может дать несколько записей, если снимки дошли в разные синхронизации). `title` — `{Rover} Sol {sol}: {n} new image(s), {k} camera(s)`; `link` — страница сола в интерфейсе; `description` (HTML) — камеры с числом снимков, до 6 миниатюр со ссылками на карточки `/photos/{id}`, ссылка на сол, строка `Credit:` со всеми авторствами пачки; `media:thumbnail` — первая миниатюра.
- `group=photo`: запись — один снимок. `title` — `title` снимка или `{Rover} Sol {sol}: {camera name}`; `link` — карточка `/photos/{id}` в интерфейсе; `guid` — тот же адрес (`isPermaLink="true"`); `description` — превью, `caption`, сол, камера, время съёмки UTC, ссылки «Full image (NASA)» (`images.full`) и «Raw image page» (`source.link`), `Credit: {credit}`; `media:content` (`images.full`, `medium="image"`, `width`/`height`, если известны), `media:thumbnail`, `media:credit`.
- Авторство (`credit`) есть в каждой записи; если источник его не дал — `NASA/JPL-Caltech`.
- Порядок — по времени появления (`pubDate`) от новых, внутри пачки — `sol DESC, taken_at DESC, id DESC`. `guid` записи постоянен; содержимое может обновиться, если источник исправит данные снимка (подпись, авторство, ссылки, сол).
- `pubDate` и `lastBuildDate` — RFC 822 в GMT; `lastBuildDate` — время последней записи (нет записей — элемента нет, канал пустой).
- `200` · `404 rover_not_found` · `404 not_found` — архивный марсоход (`spirit`, `opportunity`, `ingenuity`) · `422 validation_error`. Ошибки — JSON v2.

## Admin

Заголовок `X-Admin-Token`. Если `ADMIN_TOKEN` не задан в настройках, этих ручек нет (`404`).

### `POST /api/v2/admin/sync`
Тело: `{"rover": "perseverance", "from_sol": 2025, "to_sol": 2031}`. `from_sol` и `to_sol` необязательны: без них — инкремент.
`202 {"run_id": 17, "status": "running"}` · `401` · `404 rover_not_found` · `409 sync_in_progress` · `422`
`422 validation_error` также для `spirit` и `opportunity`: миссии завершены (Spirit — 2011, Opportunity — 2019), синхронизация не поддерживается, их снимки загружаются разовым импортом (этап 8). То же для `ingenuity` (этап 12): миссия завершена, снимки загружены разово.

### `GET /api/v2/admin/sync/runs?rover=&page=&per_page=`
`200` список:
```json
{"id": 17, "rover": "perseverance", "mode": "incremental", "from_sol": 2024, "to_sol": 2031,
 "status": "success", "started_at": "...", "finished_at": "...", "photos_upserted": 412, "failed_sols": [], "error": null}
```
Сортировка `started_at DESC`. `failed_sols` — солы, которые не удалось загрузить (по возрастанию). `401` · `404 rover_not_found` (неизвестный `rover`) · `422`.

**Учётные записи и ключи (этап 20)** — ручки есть только при `API_KEYS_ENABLED=true`:
- `GET /api/v2/admin/accounts?q=&status=&page=&per_page=` — `q` (до 254 символов): часть e-mail без учёта регистра или префикс ключа (`si_…`); `status` — `active|blocked`; `per_page` 1–100, по умолчанию 25; новые первыми. Элемент: `{"id", "email", "status", "created_at", "verified_at", "blocked_at", "block_reason", "active_keys", "requests_30d"}`.
- `GET /api/v2/admin/accounts/{id}` — элемент + `keys` (`{"id", "prefix", "created_at", "last_used_at", "revoked_at", "revoke_reason"}`) + `usage_daily`: `[{"day": "2026-12-03", "requests": 312, "rejected": 4}]` за 30 дней.
- `POST /api/v2/admin/accounts` `{"email", "note"}` → `201 {"key", "key_info", "account"}` — ключ вручную, без письма (запись сразу подтверждена); ключ показывается только в этом ответе. Адрес уже есть → выдаётся новый ключ существующей записи; `409 key_limit_reached`.
- `POST /api/v2/admin/accounts/{id}/block` `{"reason"}` (до 500 символов) → `200` элемент; `POST /api/v2/admin/accounts/{id}/unblock` → `200`.
- `POST /api/v2/admin/keys/{id}/revoke` → `200` ключ (`revoke_reason = "admin"`).
- `DELETE /api/v2/admin/accounts/{id}` → `204`: запись, её ключи и счётчики удаляются безвозвратно (по просьбе владельца адреса).
- Ошибки — как у остальных admin-ручек (`401`, `404 not_found`, `422`).

## Ограничение частоты (v1 и v2)

Решение владельца, этап 9.
- Действует на все запросы к `/api/v1/*`, `/mars-photos/api/v1/*` и `/api/v2/*`, включая `HEAD`, `304` и admin. Не действует на `/`, `/health`, `/health/sync`, `/docs`, `/openapi.json`.
- Ключ лимита без ключа API — IP клиента. За доверенным прокси (`FORWARDED_ALLOW_IPS`) — из `X-Forwarded-For`, иначе — адрес соединения. Адреса IPv6 считаются по сети `/64` (все адреса одной сети — один клиент; этап 18B). С действующим ключом API (этап 20) — **учётная запись** ключа: все её ключи делят один бюджет, IP не важен.
- **Уровни (этап 20).** Без ключа: `RATE_LIMIT_PER_MINUTE` (60), `RATE_LIMIT_PER_HOUR` (2000), `RATE_LIMIT_CONCURRENT` (4). С ключом: `RATE_LIMIT_KEY_PER_MINUTE` (120), `RATE_LIMIT_KEY_PER_HOUR` (10 000), `RATE_LIMIT_KEY_CONCURRENT` (8). `X-RateLimit-*` описывают окна того уровня, по которому посчитан запрос. Размер ответов v1 и тяжёлые поиски стоят одинаково на обоих уровнях; доплата списывается с того же счётчика, что и сам запрос; тяжёлый поиск — не больше одного одновременно на IP без ключа или на учётную запись с ключом.
- **Неверный ключ (этап 20).** После `API_KEY_AUTH_MAX_FAILURES` (20) ответов `invalid_api_key`/`api_key_revoked` с одного IP за `API_KEY_AUTH_WINDOW_MINUTES` (15) запросы с ключом с этого IP получают `429 rate_limited` с `Retry-After` до конца окна, кроме ключей, которые сервер недавно (в пределах `API_KEY_CACHE_SECONDS`) уже проверил как действующие: они обслуживаются как обычно, чтобы соседи за общим адресом (NAT) не теряли доступ. Остальные ключи с базой не сверяются и неудачей повторно не считаются. Неудачная попытка списывается и с лимита IP. На `429` блокировки `Retry-After` — до конца окна блокировки, а `X-RateLimit-*` описывают обычный лимит, поэтому значения могут не совпадать (как у блокировки admin).
- Два окна одновременно: минутное и часовое (размеры — по уровню, см. выше). Превышение любого → `429`. Отклонённые запросы в счётчики не идут.
- Ответ `429`: v2 — `{"error": {"code": "rate_limited", "message": "Rate limit exceeded", "details": null}}`, v1 — `{"errors": "Rate limit exceeded"}`; заголовок `Retry-After` — целые секунды до освобождения места в окне (не меньше 1); `Cache-Control: no-store`.
- **Заголовки лимита (этап 18D).** Все ответы на путях с лимитом — включая `HEAD`, `304`, ошибки и сам `429` — несут `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`:
  - окон два, заголовки описывают более тесное — то, где после текущего запроса осталось меньше; при равенстве — то, где место освободится позже (поэтому на `429` `X-RateLimit-Reset` совпадает с `Retry-After`);
  - `X-RateLimit-Limit` — размер этого окна (60 или 2000 при настройках по умолчанию);
  - `X-RateLimit-Remaining` — сколько запросов в нём осталось после текущего, не меньше 0;
  - `X-RateLimit-Reset` — целые секунды до освобождения места в этом окне (как `Retry-After`; не метка времени), не меньше 0;
  - CORS открывает эти заголовки браузеру (`Access-Control-Expose-Headers`);
  - на путях без лимита и при `RATE_LIMIT_ENABLED=false` заголовков нет; тело ответа и `ETag` от них не зависят;
  - у необработанных внутренних ошибок (`500`) заголовков нет — такой ответ формируется вне прослойки лимита;
  - при блокировке admin после неверных токенов `Retry-After` берётся из счётчика попыток, а `X-RateLimit-*` описывают общий лимит, поэтому значения могут не совпадать.
- **Одновременные запросы (этап 18B).** С одного IP одновременно обрабатывается не больше `RATE_LIMIT_CONCURRENT` (по умолчанию 4) запросов к путям с лимитом; лишний сразу получает тот же `429`, что и при превышении окна, с `Retry-After: 1`; в счётчики окон он не идёт. `X-RateLimit-*` у такого ответа — как обычно.
- **Размер ответов v1.** Ответ на путях v1 (`/api/v1/*`, `/mars-photos/api/v1/*`) стоит в обоих окнах большее из двух чисел, но не меньше одного запроса: сколько начатых `RATE_LIMIT_V1_BYTES_PER_REQUEST` (по умолчанию 262 144 байта, 256 КиБ) в теле в том виде, в каком оно ушло клиенту (после сжатия gzip), и сколько начатых `RATE_LIMIT_V1_RAW_BYTES_PER_REQUEST` (по умолчанию 1 048 576 байт, 1 МиБ) в несжатом теле, которое построил сервер, — в том числе для `HEAD` и `304`, где тело не отправляется. Ответ до 256 КиБ стоит, как и раньше, один запрос; список снимков всего сола Curiosity без `page` (8 МБ) — 31 запрос без сжатия и 8 — со сжатием (`Accept-Encoding: gzip`, ~140 КБ), а также для `HEAD` и `304`. Доплата списывается после отправки ответа: его собственные `X-RateLimit-*` её не учитывают, следующие — учитывают; если окно переполнено, следующие запросы получают `429` с `Retry-After`, пока списанное не выйдет из окна. Ответы v2 стоят по одному запросу независимо от размера.
- **Перегрузка (этап 18B).** Запрос API к базе ограничен по времени (`DB_STATEMENT_TIMEOUT_SECONDS`, по умолчанию 5), ожидание свободного соединения — `DB_POOL_TIMEOUT_SECONDS` (по умолчанию 5). Если не уложились — `503` с `Retry-After` (по умолчанию 5 секунд) и `Cache-Control: no-store`: v2 — `{"error": {"code": "service_busy", "message": "Server is busy, retry later", "details": null}}`, v1 — `{"errors": "Server is busy"}`. Синхронизация и загрузки данных этим ограничениям не подчиняются. Пул соединений API — `DB_POOL_SIZE` (по умолчанию 10) плюс `DB_MAX_OVERFLOW` (по умолчанию 5). **Тяжёлые поиски** — запросы списков снимков v2 (`/api/v2/photos`, `/api/v2/rovers/{rover}/photos`) без `sol` и `earth_date`, у которых `total` приходится считать по всем снимкам: с фильтрами по датам (`date_min`/`date_max`), местному времени и размеру кадра, с `sample_type`, отличным от `full` (без стоянок и `near`), а также стоянки (`site`, `site_min`/`site_max`, `drive_*`) и `near` в сочетании с `camera`, датами или этими фильтрами. Тяжёлым считается и любой поиск списка снимков v2 глубже 10 000-го снимка (`(page − 1) × per_page ≥ 10 000`), какими бы ни были фильтры. Тяжёлым считается и поиск без `sol` и `earth_date` с `camera` или `sample_type` и сортировкой по времени съёмки (`sort=taken_at` или `sort=-taken_at`). Одновременно на весь сервер выполняется не больше `HEAVY_QUERY_CONCURRENCY` (по умолчанию 2) тяжёлых поисков, и не больше одного одновременно с одного адреса (IPv6 — по сети `/64`); лишний сразу, не дожидаясь соединения, получает тот же `503 service_busy` с `Retry-After`. Остальные запросы v2 и все запросы v1 этим ограничением не затронуты. Чтобы поиск перестал быть тяжёлым, сузьте его: укажите `sol`, `earth_date` или диапазон солов без других фильтров.
- `RATE_LIMIT_ENABLED=false` выключает лимит (тесты, разработка).
- Admin, неверный токен: после `ADMIN_AUTH_MAX_FAILURES` (по умолчанию 10) ответов `401` с одного IP за `ADMIN_AUTH_WINDOW_MINUTES` (по умолчанию 15) все admin-запросы с этого IP получают `429 rate_limited` с `Retry-After` до конца окна — токен при этом не сверяется, даже верный. Успешная авторизация счётчик своего IP не сбрасывает.
- Счётчики в памяти процесса (приложение работает одним процессом), после перезапуска обнуляются.

## Кэширование (v1 и v2)

- `HEAD` поддерживается на всех `GET`-ручках v1 и v2: те же заголовки, без тела.
- Успешные `GET` (`200`) отдают `ETag`; повторный запрос с совпадающим `If-None-Match` получает `304` без тела.
- `Cache-Control`: списки, марсоходы, камеры, манифест, `/photos/stats` и `/photos/batch` (этап 18C) — `public, max-age=300`; карточка снимка (`/photos/{id}`) — `public, max-age=3600`; редирект на изображение — `public, max-age=86400`; `/rovers/{rover}/location`, `/rovers/{rover}/traverse`, `/rovers/{rover}/samples`, `/samples/{number}` и `/rovers/{rover}/radiation` (этап 21) — `public, max-age=3600` (данные меняются не чаще раза в сутки); `/rovers/{rover}/feed.xml` — `public, max-age=900`, `Last-Modified` — время последней записи ленты (нет записей — без заголовка), повтор с `If-Modified-Since` не раньше него → `304` (если в запросе есть `If-None-Match`, решает он); ошибки и admin-ручки — `no-store`.
- CORS — любые источники, только `GET`; заголовок `ETag` виден клиенту.
- Сжатие: при `Accept-Encoding: gzip` ответы от 1 КБ сжимаются; во всех ответах API — `Vary: Accept-Encoding`. `ETag` слабый и считается по несжатому телу, поэтому один и тот же для сжатого и несжатого ответа; `304` работает при любом кодировании.

## Черновики (уточнить перед своим этапом)

- **Погода** отложена: живых источников нет (`DATA-SOURCES.md`).
