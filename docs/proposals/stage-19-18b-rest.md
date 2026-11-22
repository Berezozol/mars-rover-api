# Этап 19 (остаток 18B): углы мачты и `fields`/`include` — черновик архитектора

Редакция 1 (2026-11-22, HEAD `725d4f4`, миграции до `0015`). Основа: `docs/PLAN.md` (18B, этап 19), `docs/API.md` («Общие правила v2», Photo, PhotoDetail, `/photos`, «Перегрузка», «Кэширование»), `docs/DATA-SOURCES.md` (поля MSL и M2020, «Покрытие полей для фильтров этапа 18B»), `docs/reports/stage-18b-fields.md` (§3, §6), `docs/proposals/stage-18b-filters.md` (ADR-5, ADR-6, D11, D13), код `app/sources/photo_meta.py`, `app/services/v2.py` (`photo_meta`), `app/api/v2/schemas.py` (`photo_dict`, `photo_base_dict`, `*_body`), `app/api/v2/photos.py`, `app/repositories/photos.py`; три read-only запроса к dev-базе `mars` (2026-11-22, 1,37 млн снимков Curiosity + Perseverance).

## 0. Цель, границы, критерий

**Входит:**
- новый ключ `mast` (азимут и возвышение камеры по данным источника) у Photo в списках и у PhotoDetail; разбор — в `app/sources/photo_meta.py`, из `extra`, без новых колонок (D13);
- параметр `fields` (выборка ключей Photo) у четырёх ручек-списков v2: `/api/v2/photos`, `/api/v2/rovers/{rover}/photos`, `/api/v2/rovers/{rover}/photos/latest`, `/api/v2/photos/batch`;
- текст правки `API.md`, `openapi.json`, `examples/types.ts`.

**Не входит:** фильтр по углам мачты (ADR-2, вопрос В2); `include` (ADR-4, вопрос В3); `fields` у карточки `/photos/{id}` и у прочих ручек; вложенные пути в `fields` (`images.small`); v1 (не меняется ни в одном байте); миграции и перезагрузка данных; фронтенд (по желанию — позже, §7).

**Готово, когда:** у снимков Curiosity и Perseverance с углами в источнике в ответах v2 есть `mast` с нормализованным азимутом, у остальных — `null`; `fields` урезает элементы списков ровно по правилам раздела 4; v1 и тела без `fields` (кроме добавленного `mast`) не изменились; все AC закрыты тестами.

## 1. Факты

Покрытие и значения (dev-база, запросы 2026-11-22; совпадает с `DATA-SOURCES.md`, «Покрытие полей для фильтров этапа 18B»):

| Марсоход | Поля в `extra` | Есть оба угла | Только один | Азимут, диапазон | Возвышение, диапазон |
|---|---|---|---|---|---|
| Curiosity | `extended.mast_az`, `extended.mast_el` — строки-числа (`"328.438"`) | 386 882 из 791 788 (49%) | 0 | 0…360 (31 значение ровно `360`) | −86,5…89,9 |
| Perseverance | `extended.mastAz`, `extended.mastEl` — строки, бывают в экспоненте (`"-2.93157e-05"`) | 545 390 из 579 518 (94%) | 0 | **−180…360** (194 537 вне `[0, 360)`) | −89,8…90,0 |
| Ingenuity | `extended.mastAz` = `"UNK"` (`DATA-SOURCES.md`, раздел Ingenuity) → в `extra` не сохраняется | 0 | — | — | — |
| Spirit, Opportunity | нет | 0 | — | — | — |

- Все непустые значения обоих марсоходов — числа (проверено регэкспом); `"UNK"` адаптер уже выбрасывает (`DATA-SOURCES.md`: «`"UNK"`, пустые строки и `null` → `None`»). Пар «0 и 0» нет.
- **У Perseverance две записи азимута.** По солам: 0–499 — почти всё в `[0, 360]` (отрицательных 46 из 126 351), 750 и позже — `[−180, 180]` (отрицательных ~50%, больше 180 — единицы). Переход — в солах 500–749. Это одно и то же направление в двух записях (−50 ≡ 310), поэтому в ответе азимут приводится к `[0, 360)`. Причину смены у NASA не знаем `[не проверено]`.
- **Система отсчёта.** Источник её не называет. По данным — относительно корпуса марсохода, а не севера: у неподвижных камер угол постоянен на всех солах (Curiosity `FHAZ_LEFT_B` az 1,8 / el −44,4, `RHAZ_LEFT_B` 180,6 / −44,6; Perseverance `REAR_HAZCAM_LEFT` 179,3 / −44,9), хотя марсоход поворачивался. Значит, 0° ≈ вперёд по корпусу, 180° ≈ назад, возвышение отрицательное — вниз. Направление отсчёта азимута (по часовой или против, если смотреть сверху) `[не проверено]` — по данным не различить без боковых камер. Компасный азимут (от севера) из этого не получить без курса марсохода на момент съёмки (`attitude` — кватернион строкой; его разбор — не этот этап).
- **Не только мачта.** Имя поля у NASA — «mast», но значения есть и у камер на корпусе (Hazcam): это заявленное направление камеры. У Perseverance `FRONT_HAZCAM_LEFT_A` встречаются ровно две пары: (−11, −26) у 12 713 снимков и (300, 20) у 3 642 — вторая похожа на положение мачты, а не камеры `[не проверено]`. У Curiosity `MAHLI`, `MARDI`, `MAST_*` (Mastcam) углов в dev-базе нет — их снимков с `mast_az` 0 (видно по разбивке по камерам).
- Списки уже читают `extra` целиком (`app/repositories/photos.py:76`, колонки списка) и разбирают его в `v2.photo_meta` (`app/services/v2.py:173`) — `mast` в списках не добавляет запросов к базе.
- Списки v2 отдаются через `FastJSONResponse` словарём (`app/api/v2/photos.py:125`, `schemas.photo_dict`), `response_model` служит только для OpenAPI — урезание ключей не упирается в проверку pydantic.
- `ETag` — хэш несжатого тела (`app/api/caching.py`), поэтому разные `fields` сами получают разные `ETag`.

## 2. Критерии приёмки

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | `photo_meta.mast_angles("curiosity", {"extended": {"mast_az": "328.438", "mast_el": "-44.4084"}})` → `MastAngles(azimuth=328.44, elevation=-44.41)` | qa-engineer | unit |
| AC-2 | Perseverance `mastAz`/`mastEl`: `"-50.1149"`/`"16.9037"` → `309.89`/`16.9`; `"360"` → азимут `0.0`; `"-2.93157e-05"` → `0.0` (не `360.0` и не `-0.0`); `"180.2"` → `180.2` | qa-engineer | unit |
| AC-3 | `mast_angles` при отсутствии любого из двух полей, значении не строка-число и не число (`"UNK"`, `""`, `true`, `"abc"`), `nan`/`inf`, возвышении вне `[-90, 90]`, азимуте вне `[-360, 360]`, марсоходе `spirit`, `opportunity`, `ingenuity` (даже с такими ключами в `extra`) → `None` | qa-engineer | unit |
| AC-4 | Снимок Curiosity из AC-1 в `GET /api/v2/rovers/curiosity/photos?sol=N` → у элемента `"mast": {"azimuth": 328.44, "elevation": -44.41}`, ключ сразу после `local_time` (при `near` — перед `near_stop`) | qa-engineer | api |
| AC-5 | Тот же снимок в `GET /api/v2/photos`, `/rovers/{rover}/photos/latest`, `/photos/batch?ids=…` → тот же `mast`; в `GET /api/v2/photos/{id}` → тот же `mast` после `local_time`, перед `in_sol`; `extra` карточки не изменился (сырые строки источника) | qa-engineer | api |
| AC-6 | Снимок без углов (Curiosity без `mast_az`, Spirit, Ingenuity) → `"mast": null` в списке и в карточке | qa-engineer | api |
| AC-7 | Ответы v1 (`/api/v1/...`, `/mars-photos/api/v1/...`) на тех же данных → без ключа `mast`, побайтно как до этапа (существующие векторы `LEGACY-API.md` зелёные) | qa-engineer | api |
| AC-8 | `GET /api/v2/photos?fields=sol,img_src` → у каждого элемента ровно ключи `id`, `sol`, `img_src`, `credit` в этом порядке (порядок Photo, не порядок запроса); `page`, `per_page`, `total` — как без `fields` | qa-engineer | api |
| AC-9 | `fields` у `/rovers/{rover}/photos` → `nearest_sols` на месте; у `/latest` → `sol` на месте; у `/batch` → `missing` на месте; урезаны только элементы `items` | qa-engineer | api |
| AC-10 | `fields=img_src,sol,img_src` → как `fields=sol,img_src`; `fields=id` → только `id`, `credit`; `fields=credit` → только `id`, `credit` | qa-engineer | api |
| AC-11 | `fields=` (пусто), `fields=sol,`, `fields=SOL`, `fields=images.small`, `fields=foo`, строка длиннее 300 символов, параметр повторён (`fields=sol&fields=id`) → `422 validation_error`, в `details` — `{"field": "fields", "message": …}`, в сообщении для неизвестного ключа — список допустимых | qa-engineer | api |
| AC-12 | Тяжёлый поиск (например, `/api/v2/photos?date_min=…&fields=foo`) с занятыми слотами → `422`, а не `503`: проверка `fields` идёт до слота и до базы | qa-engineer | api |
| AC-13 | `/api/v2/photos?near=…&fields=sol` → без `near_stop`; `…&fields=sol,near_stop` → с `near_stop`; `fields=near_stop` без `near` → `200`, у элементов только `id`, `credit` | qa-engineer | api |
| AC-14 | Один и тот же запрос с `fields` и без → разные `ETag`; повтор с `fields` и его `If-None-Match` → `304`; `Cache-Control: public, max-age=300` у обоих | qa-engineer | api |
| AC-15 | `GET /api/v2/photos/{id}?fields=sol` → `200`, полная PhotoDetail (параметр неизвестен ручке и игнорируется, как другие неизвестные) | qa-engineer | api |
| AC-16 | Регенерация `docs/openapi.json` → у Photo и PhotoDetail свойство `mast` (`MastOut` или `null`, обязательное); у четырёх ручек параметр `fields` (строка, `maxLength` 300) с описанием из раздела 4; схема Photo осталась с прежним списком обязательных ключей | qa-engineer | unit |

## 3. Решения

1. **`mast` — в Photo и PhotoDetail, без колонок** (ADR-1, ADR-3). Значение считается из `extra` в `app/sources/photo_meta.py`, как `local_time` и размер кадра (D13 18B: тело ответа не зависит от колонок-помощников).
2. **Азимут приводится к `[0, 360)`, оба угла округляются до 0,01°** (ADR-1). Сырые строки по-прежнему видны в `extra` карточки.
3. **Фильтра по углам нет** (ADR-2). Колонок, индексов, миграции нет.
4. **`fields` — только верхние ключи Photo, только у четырёх списков, `id` и `credit` всегда в ответе** (ADR-4). Запросы к базе не меняются, экономия — только в байтах.
5. **`include` не делаем** (ADR-4): в списках нет тяжёлых необязательных частей, которые стоило бы включать по запросу.

## 4. Текст для `API.md` (если меняется контракт)

Изменения аддитивные: новый ключ `mast` в Photo и PhotoDetail и новый необязательный параметр. Клиентов не ломает, кроме тех, кто строго проверяет набор ключей Photo (так же было с `local_time`, `width`/`height`). v1 не меняется.

**4.1. «Общие правила v2» — новый пункт после «Границы чисел»:**

> - `fields` (этап 19) — выборка ключей снимка в списках: `GET /api/v2/photos`, `/api/v2/rovers/{rover}/photos`, `/api/v2/rovers/{rover}/photos/latest`, `/api/v2/photos/batch`. Ключи Photo через запятую без пробелов, в нижнем регистре: `id`, `rover`, `sol`, `earth_date`, `taken_at`, `camera`, `sample_type`, `title`, `img_src`, `images`, `credit`, `source`, `width`, `height`, `local_time`, `mast`, `near_stop`. `id` и `credit` есть всегда, даже если не названы. Ключи идут в обычном порядке Photo, а не в порядке запроса; повторы схлопываются. Выбирается ключ целиком: вложенных путей (`images.small`) нет. Обёртка ответа (`page`, `per_page`, `total`, `nearest_sols`, `sol` у `/latest`, `missing` у `/batch`) не меняется. `near_stop` выводится, только если задан `near` и ключ выбран. Пусто, пустой элемент, неизвестный ключ, другой регистр, строка длиннее 300 символов, параметр повторён → `422`. Без `fields` — полный Photo. Другие ручки параметр не знают и игнорируют. `fields` не делает поиск быстрее и не влияет на тяжёлые поиски и лимиты: экономится только размер ответа.
>   ```
>   GET /api/v2/photos?rovers=perseverance&sol=2031&fields=sol,img_src,camera
>   {"items": [{"id": 81234, "sol": 2031,
>               "camera": {"code": "SUPERCAM_RMI", "name": "SuperCam Remote Micro Imager"},
>               "img_src": "https://mars.nasa.gov/.../LRF_2031_..._0100I4J01.png",
>               "credit": "NASA/JPL-Caltech/LANL/CNES/IRAP"}],
>    "page": 1, "per_page": 25, "total": 52}
>   ```

**4.2. «Объекты», Photo — в JSON-пример после `local_time`:**

```json
 "local_time": {"time": "17:40:12", "kind": "lmst", "planet": "mars"},
 "mast": {"azimuth": 309.89, "elevation": 16.9}}
```

и абзац после абзаца про `local_time`:

> `mast` (этап 19) — направление камеры в момент съёмки по данным источника, градусы: `azimuth` — от 0 (включительно) до 360 (не включительно), `elevation` — от −90 до 90 (отрицательное — вниз), оба округлены до 0,01. Отсчёт — **от корпуса марсохода, а не от севера**: 0 — примерно вперёд по ходу, 180 — назад; сторону света по этому числу не определить. Источник называет поля «mast» (мачта), но даёт их и для неподвижных камер на корпусе (Hazcam) — у них угол постоянный. Perseverance в разные периоды пишет азимут то от 0 до 360, то от −180 до 180; здесь он всегда приведён к 0–360 (исходная строка — в `extra` карточки). `null`, если источник углов не дал или они неразборчивы: у Curiosity — примерно у половины снимков, у Perseverance — у ~6%, у Spirit, Opportunity и Ingenuity — всегда. По отдельности `azimuth` и `elevation` `null` не бывают. Ключ есть у Photo в списках и у PhotoDetail (после `local_time`).

**4.3. PhotoDetail — в первой строке перечня:**

> **PhotoDetail** = Photo (включая `local_time` и `mast`, см. выше) + `caption`, …

и в пункт `local_time` списка: «`local_time` — как у Photo; в PhotoDetail ключ стоит на прежнем месте (после `location`), за ним — `mast` (этап 19), затем `in_sol`.»

**4.4. Ручки** — в перечни параметров `GET /api/v2/photos` (перед `page`, `per_page`), `GET /api/v2/rovers/{rover}/photos` (перед `page`, `per_page`), `GET /api/v2/rovers/{rover}/photos/latest` (в список параметров), `GET /api/v2/photos/batch` (новым пунктом):

> - `fields` (этап 19) — выборка ключей снимка, см. «Общие правила v2».

**4.5. «Кэширование»** — без правки: `ETag` считается по телу, поэтому у запросов с разными `fields` он разный; `Cache-Control` тот же.

## 5. ADR

**ADR-1. Форма и место `mast`.**
- (а) Только в PhotoDetail: минимум изменений, но списки (галерея, поиск) углов не видят, а клиенту пришлось бы ходить в карточку за каждым снимком.
- (б) В Photo и PhotoDetail: +~45 байт на элемент (до gzip), запросов к базе не добавляет (`extra` уже читается), по образцу `local_time`.
- Имена: (1) `mast: {azimuth, elevation}` — узнаваемо по источнику и `PLAN.md`; (2) `pointing` / `camera_direction` — точнее для Hazcam, но расходится с языком источника и плана; (3) плоские `mast_az`, `mast_el` — как в фиде Curiosity, но два ключа вместо одного «оба или ничего».
- Значения: как в источнике (строки, две записи азимута у Perseverance) — честно, но клиенту приходится нормализовать; числа с нормализацией азимута к `[0, 360)` и округлением 0,01° (точность источника 4-6 знаков избыточна для «куда смотрела камера») — удобно, исходник остаётся в `extra` карточки.
- Пометка системы отсчёта (`"frame": "rover"`): лишний ключ с одним значением; оговорка в контракте достаточна.
- **Рекомендация:** (б), имя (1), числа с нормализацией, без `frame`, оговорка «от корпуса, не от севера» в контракте (В1).

**ADR-2. Фильтр по углам (`mast_az_min/max`, `mast_el_min/max`).**
- За: «снимки неба» (`elevation ≥ 30`), «снимки под колёсами» (`elevation ≤ −60`), отбор панорамных серий по возвышению.
- Против: азимут от корпуса без курса марсохода почти бесполезен для поиска («смотрит на север» не выразить), а с переходом через 0 добавляет правило как у `local_time_*`; покрытие Curiosity 49%; нужны колонки `mast_az`/`mast_el` (`real`), заполнение 1,37 млн строк (`backfill-meta`), частичные индексы `CONCURRENTLY` (~3-33 МБ каждый, `docs/reports/stage-18b-fields.md` §3), новая строка в правилах тяжёлых поисков, новый `/sync-check` и `/migration-check`. Спроса не видно: ни владелец, ни фронтенд Sol Index его не просили.
- Варианты: (а) не делать; (б) только `elevation_min/max` — одна колонка `mast_el real`, индекс `(rover_id, mast_el) WHERE mast_el IS NOT NULL`, без переходов через 0, тяжёлый поиск без `sol`/`earth_date`; (в) оба угла с переходом через 0 для азимута.
- **Рекомендация:** (а) сейчас. Если владелец захочет — (б) отдельной доработкой: миграция `0016` (колонка + `CREATE INDEX CONCURRENTLY` в `autocommit_block`, `downgrade` — `DROP INDEX CONCURRENTLY` + `DROP COLUMN`), `stored_meta` получает поле `mast_el`, `STORED_META_PATHS` — два пути, заливка существующим `backfill-meta`.

**ADR-3. Колонки для ответа.** Читать `mast` из будущей колонки или из `extra`. Колонки нет (ADR-2), а D13 18B уже требует ответов из `extra`, чтобы окно между миграцией и заливкой не давало `null`. **Рекомендация:** из `extra`.

**ADR-4. `fields` и `include`.**
- `fields`. Плюс: клиенты галерей берут 3-4 ключа из 17, тело списка уменьшается в 3-5 раз (gzip съедает часть выигрыша). Минусы прошлой оценки (18B, ADR-5): «все поля в OpenAPI станут необязательными» — снимаем тем, что схему Photo не трогаем, а урезание описываем в параметре (клиент, задавший `fields`, сам знает, какие ключи придут); «ещё одна ось кэша» — `ETag` по телу справляется сам, CDN нет. Цена — функция-фильтр словаря и одна проверка параметра. Решения внутри:
  - только верхние ключи — без разбора путей и без вопросов «что такое `images.small` без `images`»;
  - `id` и `credit` всегда: `id` — чтобы элемент можно было сослаться и дособрать (`/photos/{id}`, `/batch`); `credit` — правило проекта «авторство снимков всегда сохраняется» (`CLAUDE.md`);
  - строгий регистр и `422` на неизвестное — как `sort` и `sample_type`: опечатка не должна молча вернуть урезанный ответ;
  - только списки снимков: у карточки один объект (экономить нечего), у остальных ручек нет Photo;
  - `fields` не влияет на SQL: `extra` всё равно нужен для `local_time`/`width`/`mast`; урезать `SELECT` по `fields` — сложность ради миллисекунд.
- `include`. Кандидаты: `extra` в списках (~1 КБ сырого формата NASA на снимок — противоречит смыслу v2 как своего формата; есть в карточке), `location` в списках (отдельный запрос со стоянками на страницу; для поиска по месту уже есть `near_stop`), `samples`/`flight` (по солу, есть в карточке). Ни один не просили; каждый — новая ветка запроса и правила тяжёлых поисков.
- **Рекомендация:** `fields` — делаем по разделу 4; `include` — не делаем, имя параметра не резервируем в контракте (В3).

## 6. Модель данных, миграция, поток

- Таблицы, колонки, индексы — без изменений; миграции нет.
- Поток `mast`: фид (MSL `extended.mast_az/mast_el`, M2020 `extended.mastAz/mastEl`) → адаптеры (без изменений, строки уже в `extra`) → `photos.extra` → `photo_meta.mast_angles` (`app/sources/`) → `v2.photo_meta` (`PhotoMeta.mast`) → `schemas.photo_dict` / тело карточки → роутер. Синхронизация не меняется, `/sync-check` не нужен.
- Поток `fields`: роутер (параметр) → проверка `v2.parse_fields` до слота тяжёлого поиска и до базы → сервис как сейчас → `schemas.select_fields(item, fields)` над готовыми словарями элементов → `FastJSONResponse`.
- Нагрузка: разбор двух строк на элемент (≤ 100 на страницу) — пренебрежимо.

## 7. Задания ролям и общие имена

**Общие имена:**
- `app/sources/photo_meta.py`: `@dataclass(frozen=True) class MastAngles: azimuth: float; elevation: float`; `def mast_angles(rover_id: str, extra: Mapping[str, Any]) -> MastAngles | None` — Curiosity: `extra["extended"]["mast_az"|"mast_el"]`; Perseverance: `extra["extended"]["mastAz"|"mastEl"]`; остальные → `None`. Принимает строку-число (в т. ч. экспонента) или `int`/`float` (не `bool`); конечное; `elevation` в `[-90, 90]`, `azimuth` в `[-360, 360]`; `azimuth = round(az % 360, 2)`, `360.0` → `0.0`; `-0.0` → `0.0` у обоих.
- `app/services/v2.py`: `PhotoMeta.mast: MastAngles | None`.
- `app/api/v2/schemas.py`: `class MastOut(BaseModel): azimuth: float; elevation: float`; `PhotoOut.mast: MastOut | None` (после `local_time`), `PhotoDetailOut.mast: MastOut | None` (после `local_time`); `PHOTO_FIELDS: tuple[str, ...]` — 17 ключей из 4.1 в порядке Photo; `ALWAYS_FIELDS = frozenset({"id", "credit"})`; `def select_fields(item: Json, fields: frozenset[str] | None) -> Json`.
- `app/services/v2.py` (или `app/api/v2/params.py` по месту существующих проверок): `def parse_fields(raw: str | None) -> frozenset[str] | None` — `ValidationFailed` с `field="fields"`; константа `FIELDS_MAX_LENGTH = 300`.
- Параметр запроса — `fields`; тела `photos_body`, `photo_list_body`, `latest_body`, `photo_batch_body` получают `fields: frozenset[str] | None = None`.

- `backend-dev`: `mast_angles` и `MastAngles`; `mast` в `photo_dict` и в теле карточки (порядок ключей по AC-4/AC-5); `MastOut` в схемах; `fields` в четырёх ручках `app/api/v2/photos.py`, `app/api/v2/rovers.py` (проверка повтора параметра — как у `ids` в `/batch`; проверка до `HeavyQueryGate`); описание параметра в OpenAPI — текст 4.1 сокращённо на английском; регенерация `docs/openapi.json` (`uv run python -m app.cli openapi --output docs/openapi.json`; файл в `docs/` — передать тимлиду, если зона не пускает).
- `qa-engineer`: AC-1–AC-3 — unit по `photo_meta` (в т. ч. экспонента, `"360"`, `-0.0`, границы ±90 и ±360 включительно, `bool`); AC-4–AC-7 — api на фикстурных строках `extra` из §1 (Curiosity, Perseverance до и после сола 750, Spirit, Ingenuity); AC-8–AC-15 — api; AC-16 — unit по `openapi.json`. Таблица «AC → тест».
- `data-engineer`: не нужен. Необязательно, позже: по `attitude` проверить, можно ли получить компасный азимут, и направление отсчёта азимута (по часовой ли) — отчёт без внешних запросов.
- `frontend-dev` / `frontend-qa` / `designer`: в этапе не участвуют. Возможное использование позже — строка «Camera pointing: az 309.9°, el 16.9° (rover frame)» в карточке и `fields` для сетки Search (`id,sol,camera,images,taken_at`).
- `tech-writer`: `examples/types.ts` (`mast`, `MastOut`), абзац про `fields` для `/developers` и `llms.txt` (текст в отчёте, вставляет `backend-dev`).
- `devops`: ничего (миграций нет).

## 8. Проверка и бюджет

- Команды: `uv run ruff check .`, `uv run ruff format .`, `uv run pytest -q -n 12`, `uv run python -m app.cli openapi --output docs/openapi.json`, `uv run --no-project python .claude/tools/ac_trace.py` (покрытие AC).
- Живая проверка: на dev-базе `GET /api/v2/rovers/perseverance/photos?sol=600` (сол со смешанной записью азимута) — все `azimuth` в `[0, 360)`; `GET /api/v2/photos?rovers=curiosity&sol=5077&fields=id,img_src` — элементы только с `id`, `img_src`, `credit`. `/sync-check` не нужен.
- Ревью: `reviewer`; `security-reviewer` — коротко (новый параметр публичной ручки: длина, повтор, отсутствие влияния на SQL).
- Ожидаемый расход: architect — этот запуск ~1%; data-engineer — 0; backend-dev + qa-engineer — небольшой этап (порядка одной доработки 18B-2 без миграции).

## 9. Внешние запросы (для «ок» владельца)

Нет.

## 10. Вопросы владельцу

- **В1. `mast` в списках и в карточке, имя `mast` с `azimuth`/`elevation`, азимут приведён к 0–360 и округлён до 0,01°, с оговоркой «от корпуса марсохода, не от севера»?** *Рекомендация:* да (ADR-1). Альтернатива — только в карточке или имя `pointing`.
- **В2. Фильтр по углам — не делать?** *Рекомендация:* не делать сейчас (ADR-2). Если нужен — только возвышение (`elevation_min/max`), отдельной доработкой с миграцией.
- **В3. `fields` — делать по разделу 4 (только списки, только верхние ключи, `id` и `credit` всегда, неизвестный ключ → `422`); `include` — не делать?** *Рекомендация:* да.
- **В4. Правка `API.md` по разделу 4** (аддитивная, v1 не меняется) — «ок» на `/contract-change`? *Рекомендация:* да.
