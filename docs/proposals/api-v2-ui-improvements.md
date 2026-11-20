# API v2: доработки для интерфейса Sol Index — черновик архитектора

Редакция 1 (2026-11-20, HEAD `a7c4e88`, миграции до 0006). Основа — раздел «Чего не хватает в API v2» в `design/NOTES.md` (пункты 1–11), код `frontend/src`, `app/api/v2`, `app/services/v2.py`, `app/repositories`, факты `data-engineer` (`docs/reports/stage-12-ingenuity-and-api-data.md`) и замеры по локальной базе (полный объём, 2026-11-20). Раздел 3 — текст для `API.md`, переносит тимлид после «ок» владельца.

## 0. Цель, границы, критерий

**Входит (предлагаю взять сейчас):** соседи снимка в соле (п. 1), счётчики камер за сол (п. 2), `width`/`height` (п. 3), местное солнечное время из уже сохранённых данных (п. 5), `sample_type` у `/photos/latest` (п. 6), ближайшие солы при пустом результате (п. 7), `end_date` (п. 9), `data_source` (п. 10).
**Позже:** миниатюры Curiosity (п. 4), лёгкий манифест (п. 8), перезагрузка истории Perseverance ради местного времени (часть п. 5).
**Не нужно:** CORS (п. 11) — уже в контракте и в коде.

**Ограничения:** v1 — байт-в-байт как раньше; в v2 только добавления: новые ключи и необязательные параметры, существующие поля не меняют ни тип, ни смысл. Клиенты, игнорирующие незнакомые ключи, не ломаются.

**Готово, когда:** новые поля и параметры отвечают по разделу 3 на всех четырёх марсоходах; там, где данных нет, — `null`; тесты v1 проходят без изменений; `alembic downgrade 0006` работает.

## 1. Факты, на которые опирается план

| Что | Факт (полный объём локальной базы) |
|---|---|
| размеры кадра | Perseverance — 100% в `extra.extended.dimension` строкой `"(W,H)"` (`"(1648,1200)"` у MCZ, `"(1280,960)"` у NAVCAM при `scaleFactor` 4 — это размер файла `full_res`, а не матрицы). Spirit/Opportunity — 100%: `extra.line_samples` (ширина) и `extra.lines` (высота) исходного продукта PDS. Curiosity — нет нигде (`subframe_rect`, `scale_factor` фида не сохраняем, в фикстурах `null`). |
| местное время | Curiosity — `extra.extended.lmst` у 388 029 из 791 788 снимков (49%), формат `"Sol-05073M11:25:43.222"`, LMST. Spirit/Opportunity — `extra.local_true_solar_time` у 100%, `"12:45:26"`, это **LTST** (истинное), не LMST. Perseverance — `date_taken_mars` (`"Sol-02024M13:07:38.713"`) есть в фиде, адаптер его не сохраняет. |
| миниатюры Curiosity | фид присылает их отдельными записями (`is_thumbnail`), адаптер отбрасывает; соответствие миниатюра → кадр не подтверждено. |
| объёмы | снимков в соле — до 4380 (Curiosity); `sol_summaries`: Curiosity 4690 солов, Opportunity 4842; `camera_sol_counts` — 69 тыс. строк на всё. |
| скорость | счётчики камер за сол из `camera_sol_counts` — 0,8 мс; соседи по `ix_photos_rover_id_sol_taken_at` — 0,04–0,14 мс, позиция — 0,33 мс на самом большом соле (вариант с камерой не замерялся — задача `data-engineer` в разделе 6). |
| даты окончания MER | в источниках нет; это справочные факты (как `launch_date`/`landing_date`, которые заданы в миграции 0002). |
| CORS | `API.md`, «Кэширование»: «любые источники, только `GET`, `ETag` виден»; в коде `CORSMiddleware`, `CORS_ORIGINS` по умолчанию `["*"]`. |

## 2. Решения по пунктам

| № | Пункт | Решение | Почему |
|---|---|---|---|
| 1 | соседи в соле | **сейчас** | сейчас карточка снимка догружает 1–3 страницы списка (`Photo.tsx`, `neighbours`); на сервере — три запроса по готовому индексу, < 1 мс |
| 2 | камеры за сол | **сейчас**, параметр `sol` у `/cameras` | таблица `camera_sol_counts` уже есть; менять `manifest.sols[].cameras` на объекты — ломающее изменение, а дублирующее поле утяжелит и без того большой манифест |
| 3 | `width`/`height` | **сейчас**, `null` у Curiosity | у M20 и MER данные есть на 100%, считаются из `extra` при чтении, без миграции |
| 4 | миниатюры Curiosity | **позже** | см. ADR-4: свой ресайз нагружает ноутбук (канал отдачи, CPU, очередь конвертера), а путь через миниатюры фида ещё не подтверждён |
| 5 | местное время | **сейчас для Curiosity и MER**, Perseverance — только новые снимки; перезагрузка истории — **позже**, решает владелец | см. ADR-2 |
| 6 | `sample_type` у `/latest` | **сейчас** | одна строка в сервисе, индекс `ix_photos_rover_id_sample_type_sol` есть |
| 7 | ближайшие солы | **сейчас**, ключ `nearest_sols` в списке | для `earth_date` у интерфейса сейчас нет «соседнего сола» вообще; цена — два запроса к `sol_summaries` и только при пустом ответе |
| 8 | лёгкий манифест | **позже**, по замеру | ответ кэшируется (ETag, `max-age=300`, gzip); решать по цифрам (ADR-5) |
| 9 | `end_date` | **сейчас** | один столбец, две строки данных |
| 10 | `data_source` | **сейчас** | выводится из `SYNCABLE_ROVERS`, без миграции |
| 11 | CORS | **не нужно** | уже описан и работает; фронтенд и так на том же хосте. Пункт в `NOTES.md` устарел |

## 3. Текст для `API.md`

Вставлять в соответствующие места; добавления помечены разделом-якорем.

**→ «Объекты», Rover** — пример и абзац под ним заменить на:
```json
{"id": "perseverance", "name": "Perseverance", "mission": "mars2020",
 "launch_date": "2020-07-30", "landing_date": "2021-02-18", "end_date": null, "status": "active",
 "max_sol": 2031, "max_date": "2026-11-06", "total_photos": 579518,
 "cameras_count": 23, "last_synced_at": "2026-11-17T06:00:12Z", "data_source": "sync"}
```
`max_sol`, `max_date` и `last_synced_at` равны `null`, пока данных нет.
- `end_date` — дата официального завершения миссии; `null` у действующих. Spirit — `2011-05-25`, Opportunity — `2019-02-13` (справочные даты, не из источника). Последний снимок — по-прежнему `max_date`.
- `data_source` — откуда снимки: `"sync"` — регулярная синхронизация с фидом (Curiosity, Perseverance), `"archive"` — разовый импорт архива (Spirit, Opportunity); у `"archive"` `last_synced_at` — время импорта.

**→ «Объекты», Camera** — после примера:
В `/cameras?sol=N` `total_photos` — число снимков камеры за этот сол (см. эндпоинт).

**→ «Объекты», Photo** — в пример после `"source": {...}` добавить `"width": 1648, "height": 1200`, под примером абзац:
`width`, `height` — размер полного кадра в пикселях по данным источника (у Spirit и Opportunity — исходного продукта PDS); по ним можно заранее задать пропорции любого размера из `images`. Оба `null`, если источник размер не даёт (у Curiosity — всегда) или он неразборчив; по отдельности `null` не бывают.

**→ «Объекты», PhotoDetail** — заменить строку на:
**PhotoDetail** = Photo + `caption`, `received_at`, `site`, `drive`, `extra` (объект с техническими полями источника), `location` (этап 11), `local_time`, `in_sol`.
```json
"local_time": {"time": "11:25:43", "kind": "lmst"},
"in_sol": {"position": 17, "total": 52, "newer_id": 81233, "older_id": 81240}
```
- `local_time` — местное солнечное время съёмки на Марсе, `HH:MM:SS` (24 марсианских часа, без долей секунды). `kind`: `"lmst"` — среднее (Curiosity, Perseverance), `"ltst"` — истинное (Spirit, Opportunity; отличается от среднего до ~50 минут). `null`, если источник время не дал: у Curiosity — примерно у половины снимков, у Perseverance — у снимков, загруженных до этой доработки.
- `in_sol` — место снимка в его соле в порядке списка `/photos` (`taken_at DESC, id DESC`): `position` — номер с 1, `total` — снимков в соле, `newer_id` — предыдущий в списке (снят позже), `older_id` — следующий (снят раньше); `null` у краёв. Учитывает параметры `camera` и `sample_type` запроса `/photos/{id}` (см. эндпоинт). Карточка кэшируется на час — у последних солов, куда снимки ещё доходят, `in_sol` может отставать на это время.

**→ `GET /api/v2/rovers/{rover}/cameras`** — заменить на:
Параметр (необязательный): `sol` — целое ≥ 0, границы общие.
`200 {"items": [Camera, ...]}`, сортировка по `code`. Без `sol` `total_photos` — по всей миссии; с `sol` — за этот сол, в списке все камеры марсохода, у не снимавших `total_photos: 0` (сол без снимков — все нули). · `404 rover_not_found` · `422 validation_error`

**→ `GET /api/v2/rovers/{rover}/photos`** — после строки «Сортировка всегда…»:
Ответ — список Photo с дополнительным ключом `nearest_sols`:
```json
{"items": [], "page": 1, "per_page": 25, "total": 0, "nearest_sols": {"before": 2028, "after": 2030}}
```
- `nearest_sols` — не `null` только если задан `sol` или `earth_date` и `total = 0`. `before` — наибольший сол со снимками меньше запрошенного, `after` — наименьший больше; `null`, если такого нет. Для `earth_date` сравнивается самая ранняя дата съёмки сола (`manifest.sols[].earth_date`). Фильтры `camera` и `sample_type` не учитываются: это ближайшие солы, где есть хоть какие-то снимки.
- В остальных случаях `"nearest_sols": null`.

**→ `GET /api/v2/rovers/{rover}/photos/latest`** — заменить строку параметров:
Фото за `max_sol` марсохода (по всем типам). Параметры: `camera`, `sample_type` (как у `/photos`), `page`, `per_page`. `sol` в ответе — всегда `max_sol`, даже если с фильтрами снимков нет (`total: 0`).

**→ `GET /api/v2/photos/{id}`** — заменить на:
Параметры (необязательные, влияют только на `in_sol`): `camera`, `sample_type` — как у `/photos`; неизвестная для марсохода снимка камера или неверный `sample_type` → `422`. С фильтрами `in_sol` считается по снимкам сола, подходящим под них; если сам снимок не подходит — `position: null`, а `newer_id`/`older_id` — ближайшие подходящие по порядку списка.
`200 PhotoDetail` · `404 photo_not_found` · `422 validation_error`

**→ `GET /api/v2/rovers/{rover}/manifest`** — после «Сортировка `sol DESC`»: солы без снимков в `sols` отсутствуют (уточнение, поведение не меняется).

## 4. ADR

**ADR-1. Где считать `width`/`height`/`local_time`.**
- А. При чтении из `extra` чистыми функциями в `app/sources/photo_meta.py` (формат `extra` задают адаптеры — значит, и разбирают в `sources/`); сервис v2 отдаёт результат схемам. Плюсы: без миграции и перезаписи 1,7 млн строк, сразу на всю историю, `extra` в списках и так читается. Минусы: разбор строки на каждый снимок списка (≤ 100 на страницу — микросекунды); фильтровать по размеру нельзя.
- Б. Столбцы `width`, `height`, `local_time` в `photos`, заполняет адаптер. Плюсы: нормализовано, можно индексировать. Минусы: миграция + пакетное заполнение истории (UPDATE всех строк — двойной объём таблицы до VACUUM) или перезагрузка из сети.
- **Рекомендация: А.** Фильтров по размеру и времени никто не просил; если понадобятся — Б отдельным этапом, функции из А переедут в адаптеры.

**ADR-2. Местное время Perseverance.**
- А. Адаптер M20 начинает класть `date_taken_mars` в `extra`; новые снимки и перекрытие инкремента получают время, история — `null`. Бесплатно.
- Б. А + разовая перезагрузка истории (`backfill --rover perseverance --no-resume`): ~5,8 тыс. запросов к JPL (по 100 снимков), часы при ≤2 rps; upsert перепишет `extra` у 579 тыс. строк.
- В. Вычислять LMST из `taken_at` и долготы места посадки (алгоритм Mars24): 100% без сети, но это наше вычисление, а не данные миссии (расхождение — секунды–минуты).
- **Рекомендация: А сейчас, Б — по решению владельца** (вопрос В2). В отклоняю: выдавать вычисленное за данные источника не хочу, а помечать отдельным `kind` — лишняя сложность ради одного поля.

**ADR-3. LTST у Spirit/Opportunity.** Отдавать с `kind: "ltst"` (рекомендую) или только LMST (тогда MER — всегда `null`). Смешивать под одним именем без `kind` нельзя: разница до ~50 минут.

**ADR-4. Миниатюры Curiosity.**
- А. Миниатюры фида: если `data-engineer` подтвердит однозначное соответствие миниатюра → кадр, адаптер пишет её URL в `img_small`; нужна перезагрузка истории Curiosity без фильтра `thumbnail::sample_type::noteq` (~4,7 тыс. солов, страниц примерно вдвое больше нынешнего) — внешние запросы, решает владелец.
- Б. Свой ресайз, как 9a: `size=small` у Curiosity → скачать полный кадр, уменьшить Pillow (зависимость уже есть), дисковый кэш. Цена на ноутбуке за Tailscale Funnel: холодная страница сетки 24–96 плиток = столько же скачиваний полных кадров и ресайзов; очередь конвертера (2 параллельно, 8 в очереди) даст `503 converter_busy` — нужен отдельный пул и лимиты; картинки пойдут через канал отдачи ноутбука (сейчас браузер берёт их у NASA напрямую); полный прогрев — ~10 ГБ кэша.
- В. Ничего на сервере: интерфейс грузит `full` лениво (`loading="lazy"`, `decoding="async"`), как сейчас.
- **Рекомендация: позже.** Сейчас В; следующий шаг — проверка А у `data-engineer` (без загрузки истории). Б — только если А не сработает и будут жалобы на скорость сетки.

**ADR-5. Лёгкий манифест.** Ответ Curiosity — ~4,7 тыс. солов, массивы камер ≈ 416 КБ текста, итого порядка 0,7 МБ JSON, после gzip — порядка 70–100 КБ (оценка), кэшируется браузером и интерфейсом. **Рекомендация: позже**; `data-engineer` замеряет размер gzip и p95. Порог для доработки — gzip > 150 КБ или p95 > 300 мс; тогда — параметр `cameras=false` (ключ `cameras` отсутствует), а не общий `fields`.

**ADR-6. Охват `in_sol`.** Только сол снимка, с `camera`/`sample_type` (рекомендую) — совпадает со списком сола, все запросы ограничены одним солом (≤ 4,4 тыс. строк). Охват «по `earth_date`» — дата может захватывать два сола, отдельная логика; списки по дате интерфейс оставляет на нынешней догрузке страниц.

## 5. Модель данных и стоимость запросов

**Миграция `20261120_0007_rover_end_date.py`** (`revision "0007"`, `down_revision "0006"`): `rovers.end_date date NULL`; `UPDATE` Spirit → `2011-05-25`, Opportunity → `2019-02-13`. `downgrade` — `drop_column`. Модель: `Rover.end_date: Mapped[date | None]`. Других изменений схемы нет, новых индексов нет.

| Запрос | Как | Индекс | Цена |
|---|---|---|---|
| `in_sol` без фильтров | `newer`: `(taken_at, id) > (t, id)` по возрастанию, `LIMIT 1`; `older` — наоборот; `position` — `count(*)` с `(taken_at, id) > …` + 1; `total` — `sol_summaries` | `ix_photos_rover_id_sol_taken_at` | ≤ 0,5 мс (замер) |
| `in_sol` с `sample_type` | то же с условием | `ix_photos_rover_id_sample_type_sol` | того же порядка |
| `in_sol` с `camera` | то же; `total` — `count(*)` | `ix_photos_rover_id_camera_id_sol` + фильтр | ≤ 4,4 тыс. строк, не замерялось |
| `/cameras?sol` | `cameras LEFT JOIN camera_sol_counts ON (rover_id, sol, camera_id)` | PK `camera_sol_counts` | 0,8 мс |
| `nearest_sols` | `max(sol) WHERE sol < X`, `min(sol) WHERE sol > X`; по дате — `earth_date < D` / `> D` | PK `sol_summaries`; по дате — просмотр ≤ 4,9 тыс. строк марсохода | только при `total = 0` |
| `/latest?sample_type` | как `/photos?sol=max_sol&sample_type` | `ix_photos_rover_id_sample_type_sol` | как сейчас |
| `width`/`height`/`local_time`, `data_source` | вычисление в памяти | — | 0 запросов |

## 6. Задания ролям и общие имена

**Общие имена** (`backend-dev` реализует, `qa-engineer` пишет тесты по ним параллельно):
- `app/sources/photo_meta.py`: `frame_size(rover_id: str, extra: Mapping[str, Any]) -> tuple[int, int] | None`; `local_time(rover_id: str, extra: Mapping[str, Any]) -> LocalTime | None`; `@dataclass(frozen=True) LocalTime(time: str, kind: Literal["lmst", "ltst"])`. Разбор: M20 — `extra.extended.dimension` по `^\((\d{1,6}),(\d{1,6})\)$`, `extra.date_taken_mars`; MSL — `extra.extended.lmst`; MER — `extra.line_samples`, `extra.lines`, `extra.local_true_solar_time`. Время — `^(?:Sol-\d+M)?(\d{2}):(\d{2}):(\d{2})`, часы 0–23, минуты и секунды 0–59, иначе `None`; размер — целые > 0, иначе `None`.
- `app/sources/m2020.py`: в `_extra` добавить ключ `date_taken_mars` (через `clean`).
- `app/services/v2.py`: `PhotoMeta(width, height, local_time)` и `photo_meta(photo: Photo) -> PhotoMeta`; `data_source(rover_id: str) -> Literal["sync", "archive"]` (по `SYNCABLE_ROVERS`); `SolPosition(position: int | None, total: int, newer_id: int | None, older_id: int | None)`; `PhotoDetail.in_sol: SolPosition`; `get_photo_detail(session, photo_id, camera: str | None = None, sample_type: str | None = None)`; `list_cameras(session, slug, sol: int | None = None)`; `latest_photos(session, slug, camera, page, sample_type: str | None = None)`; `NearestSols(before: int | None, after: int | None)`, `PhotoPage.nearest_sols: NearestSols | None = None`.
- `app/repositories/photos.py`: `sol_position(session, flt: PhotoFilter, taken_at: datetime, photo_id: int) -> tuple[int | None, int, int | None, int | None]` (`flt.sol` обязателен); `nearest_sols(session, rover_id, *, sol: int | None = None, earth_date: date | None = None) -> tuple[int | None, int | None]`.
- `app/repositories/rovers.py`: `list_cameras_with_sol_counts(session, rover_id, sol) -> list[tuple[Camera, int]]`.
- `app/api/v2/schemas.py`: `RoverOut.end_date`, `RoverOut.data_source` (порядок ключей — как в разделе 3); `PhotoOut.width`, `PhotoOut.height` (после `source`); `LocalTimeOut(time, kind)`, `InSolOut(position, total, newer_id, older_id)`, `PhotoDetailOut.local_time`, `PhotoDetailOut.in_sol` (после `location`); `NearestSolsOut(before, after)`, `PhotosResponse.nearest_sols`.
- Сообщения 422 — те же, что у `/photos` (`_camera_codes`, `_sample_type`).

**`backend-dev`:** всё выше + миграция 0007; v1 (`app/api/legacy`, `app/services/legacy.py`) не трогать; `extra` в ответе не меняется, кроме нового ключа `date_taken_mars` у новых снимков Perseverance.

**`qa-engineer`** (`tests/`):
- unit `photo_meta`: все три формата; `"UNK"`/отсутствие/мусор (`"(0,5)"`, `"(a,b)"`, `"25:00:00"`, `"Sol-1M9:5:1"`) → `None`; Curiosity размер всегда `None`; MER `kind == "ltst"`; разбор `date_taken_mars` из фикстуры `m2020/sol_2029_page1.json`.
- api `/photos/{id}`: `in_sol` на первом, среднем, последнем снимке сола; одинаковый `taken_at` у двух снимков (порядок по `id DESC`); сол из одного снимка (`1/1`, оба `null`); `camera` и `sample_type` (снимок подходит / не подходит → `position: null`); неизвестная камера и `sample_type=thumbnail` → `422`; соответствие `position` и порядка `/photos?sol=` на нескольких страницах; разные `ETag` для разных параметров.
- api `/cameras?sol=`: все камеры, нули, сол без снимков, `sol=-1`/`2147483648`/`abc` → `422`; без `sol` — как раньше.
- api `/photos`: `nearest_sols` — пусто по `sol` между солами, ниже первого (`before: null`), выше последнего; пусто по `earth_date`; пусто из-за `camera` при непустом соле; непустой ответ и запрос без `sol`/`earth_date` → `null`.
- api `/latest?sample_type=`: фильтр работает, `sol == max_sol` при `total: 0`, неверное значение → `422`.
- Rover: `end_date`, `data_source` у всех четырёх; миграция 0007 вверх/вниз.
- Регрессия: все тесты v1 без изменений.

**`data-engineer`:** (1) EXPLAIN `in_sol` с `camera` на самом большом соле Curiosity; (2) размер gzip и p95 `/manifest` Curiosity и Opportunity (ADR-5); (3) соответствие миниатюр MSL кадрам (ADR-4А), без загрузки истории; (4) совпадает ли размер browse JPEG MER с `lines`/`line_samples`.

**`frontend-dev`** (после выкатки):
- `Photo.tsx`: «newer/older» и «N of M» — из `in_sol` (с `camera`/`sample_type` из контекста списка, если список по солу); догрузку страниц оставить только для списков по `earth_date`; `parseDimension(extra)` заменить на `width`/`height`; строка «Local time … LMST/LTST».
- `Photos.tsx`: числа на чипах — `/cameras?sol=` (вместо `total_photos` «по всем солам»); пустое состояние — кнопки из `nearest_sols`, в том числе для поиска по дате; `ranged manifest` для «соседних солов» больше не нужен.
- `Thumb.tsx`/`PhotoTile.tsx`: `aspect-ratio` заглушки из `width`/`height`.
- `Home.tsx`: полоса последних снимков — `latest?sample_type=full`; карточка завершённой миссии — `end_date`, подпись «Imported» при `data_source: "archive"`.
- `types.ts`: новые поля как необязательные/`null`.

**`devops`:** ничего; после выкатки — `alembic upgrade head` (миграция мгновенная).

## 7. Вопросы владельцу

**В1. Утвердить текст раздела 3 (пункты 1, 2, 3, 5, 6, 7, 9, 10)?** Все изменения — только добавления в v2, v1 не меняется. *Рекомендую: да.*

**В2. Местное время Perseverance для истории:** разовая перезагрузка 579 тыс. снимков (~5,8 тыс. запросов к JPL, несколько часов) или только новые снимки? *Рекомендую: пока только новые; перезагрузку — позже, ночью, если поле окажется востребованным.*

**В3. Spirit/Opportunity: отдавать истинное время (LTST) с пометкой `kind: "ltst"`?** *Рекомендую: да* (у них 100% данных; без этого у MER поле всегда пустое).

**В4. Даты окончания миссий** берём справочные: Spirit — 2011-05-25, Opportunity — 2019-02-13 (дата объявления о завершении; последняя связь — 2010-03-22 и 2018-06-10). *Рекомендую: да, именно даты объявления.*

**В5. Миниатюры Curiosity — отложить** до проверки миниатюр фида; свой ресайз на ноутбуке не делать. *Рекомендую: да, отложить.*

Попутно по открытым вопросам дизайнера (`NOTES.md`): (1) список допустимых камер — внутри `message` (`"...; allowed: A, B"`), структуру не меняем, интерфейс берёт список из `/cameras`; (2) солы без снимков в манифесте отсутствуют — фиксируем в `API.md` (раздел 3); (3) сетка — напрямую `images.*`, «Open image»/скачивание — через `/photos/{id}/image`; изменений контракта не нужно.
