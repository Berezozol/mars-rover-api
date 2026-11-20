# Этап 16: подписка на новые снимки (RSS) — черновик архитектора

Редакция 1 (2026-11-20, HEAD `1934a97`, миграции до 0010). Основа: `docs/PLAN.md` (этап 16), `docs/API.md` («Photo», «Кэширование», «Ограничение частоты»), dev-БД `mars` (статистика `photos`, `sync_runs`), код `app/` и `frontend/`. Сеть не использовалась. Раздел 3 — текст для `API.md`, переносит тимлид после «ок» владельца.

## 0. Цель, границы, критерий

**Входит:** RSS-лента новых снимков марсохода из нашей БД, фильтры `camera` и `sample_type`, два вида записей (сводка «сол — пачка» и «один снимок»); пометка «новый» при вставке регулярной синхронизацией (миграция 0011); `ETag`/`Last-Modified`, `304` по `If-None-Match` и `If-Modified-Since`; ссылки «Subscribe (RSS)» и `<link rel="alternate">` в интерфейсе.
**Не входит:** v1; Atom; ленты архивных марсоходов (Spirit, Opportunity, Ingenuity); ленты образцов, полётов, положения; вебхуки (вопрос В4); XSL-оформление ленты для браузера; запросы к NASA.
**Готово, когда:** после инкрементальной синхронизации (respx) в ленте появляются записи о вставленных снимках, а после `backfill`/импорта — нет; лента проходит разбор `xml.etree` и стороннего валидатора (W3C Feed Validator — вручную, один раз, на проде); повтор с `If-None-Match` или `If-Modified-Since` → `304`; `alembic downgrade 0010` работает; v1 и прежние тесты v2 без изменений.

## 1. Факты

| Что | Факт |
|---|---|
| `photos.created_at` | `server_default now()` — время начала транзакции вставки; upsert его не трогает (`app/repositories/photos.py`); `updated_at` меняется при каждом изменении данных |
| `photos.received_at` | `date_received` источника (когда JPL получил кадр), заполнен у всех 1,73 млн строк dev-БД, включая MER и Ingenuity |
| запоздание | `received_at − taken_at` (сол > 1000): Curiosity — медиана 1,5 сут., 90% — 13 сут., 99% — 214 сут.; Perseverance — 0,9 / 9,9 / 38 сут. Снимки старых солов приходят неделями |
| заливки | в dev-БД все 1,73 млн строк вставлены `backfill`/импортом за 2026-11-17…20 (до 385 тыс. в час); инкрементальных запусков в dev-БД нет. По `created_at` без фильтра лента после заливки = сотни тысяч «новых» |
| режимы | `resolve_mode`: без солов — `incremental` (планировщик, `POST /admin/sync`), с `--from-sol/--to-sol` — `backfill`; MER — `import-mer`; Ingenuity — `backfill`. Каждый сол коммитится отдельной транзакцией |
| объём | сол Curiosity — до ~350 снимков и 11 камер (5071–5073 в dev-БД); Perseverance — сотни снимков в сол; синхронизация раз в 6 ч (`SYNC_INTERVAL_MINUTES=360`) |
| индексы `photos` | `(rover_id, sol DESC, taken_at DESC, id DESC)`, `(rover_id, sample_type, sol DESC, taken_at DESC, id DESC)`, `(rover_id, camera_id, sol)`, `(rover_id, earth_date)`, частичные `full` и `site/drive`. По `created_at`/`received_at` индексов нет |
| HTTP-слой | `caching.py`: слабый `ETag` по телу, `304` по `If-None-Match`, `Cache-Control` по пути; `If-Modified-Since` не поддерживается; лимит частоты — на всё `/api/v2/*` (60/мин, 2000/ч на IP) |
| origin | `request_origin(request)` (`app/api/home.py`) — от `request.base_url`; им же строятся абсолютные ссылки MER в `photo_dict` |
| интерфейс | SPA: один `index.html` на все пути; маршруты `/rovers/{rover}` (фильтры `sol`, `camera`, `sample_type` в query), `/photos/{id}`; `useTitle` меняет `document.title` |

## 2. Решения

| № | Вопрос | Решение | Почему |
|---|---|---|---|
| D1 | формат | только RSS 2.0 + пространства `atom` (self) и `media` (картинки) | ADR-1 |
| D2 | генерация | `xml.etree.ElementTree` (stdlib) + вычистка запрещённых в XML 1.0 символов | ADR-2 |
| D3 | что «новое» | столбец `photos.feed_at` = время вставки, только при `incremental`; лента — по `feed_at` | ADR-3 |
| D4 | запись ленты | по умолчанию сводка «сол — пачка» (`group=sol`), по выбору — снимок (`group=photo`) | ADR-4 |
| D5 | путь | `GET /api/v2/rovers/{rover}/feed.xml`, v1 не трогаем | ADR-5 |
| D6 | кэш | `ETag` (есть), `Last-Modified` = последний `feed_at`, `If-Modified-Since` в `caching.py`, `max-age=900` | ADR-6 |
| D7 | архивные марсоходы | `404 not_found`: новых снимков не будет | ADR-7 |
| D8 | ошибки | JSON v2, как у всех ручек (читалки показывают только код) | ADR-5 |
| D9 | лимит частоты | действует, без исключений | ADR-6 |

## 3. Текст для `API.md`

**→ «Объекты», после Sample — нет новых JSON-объектов.**

**→ Новый эндпоинт (после `/samples/{number}`)**

### `GET /api/v2/rovers/{rover}/feed.xml` (этап 16)
RSS 2.0-лента новых снимков марсохода — для RSS-читалок. Только марсоходы с регулярной синхронизацией: `curiosity`, `perseverance`.
Параметры (все необязательные):
- `camera` — код камеры без учёта регистра; неизвестный для марсохода → `422` (как у `/photos`);
- `sample_type` — `full`, `subframe`, `downsampled`; иное → `422`;
- `group` — `sol` (по умолчанию) или `photo`; иное → `422`;
- `limit` — число записей, от 1 до 100, по умолчанию 20 при `group=sol` и 50 при `group=photo`; вне границ → `422`.
Неизвестные параметры игнорируются.

**Что считается новым.** Снимок попадает в ленту, если его впервые сохранила регулярная синхронизация (по расписанию или `POST /admin/sync`); время появления — момент сохранения у нас (`pubDate`). Загрузка истории (`backfill`), импорт архивов и повторная загрузка уже известных снимков в ленту не попадают. В ленте только снимки, появившиеся за последние 30 дней. Снимок старого сола, дошедший с запозданием, — тоже новый.

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
- Порядок — по времени появления (`pubDate`) от новых, внутри пачки — `sol DESC, taken_at DESC, id DESC`. Записи не меняются после публикации: `guid` и содержимое постоянны.
- `pubDate` и `lastBuildDate` — RFC 822 в GMT; `lastBuildDate` — время последней записи (нет записей — элемента нет, канал пустой).
- `200` · `404 rover_not_found` · `404 not_found` — архивный марсоход (`spirit`, `opportunity`, `ingenuity`) · `422 validation_error`. Ошибки — JSON v2.

**→ «Кэширование»:** `/rovers/{rover}/feed.xml` — `public, max-age=900`; `Last-Modified` — время последней записи ленты (нет записей — без заголовка); повтор с `If-Modified-Since` не раньше него → `304` (если в запросе есть `If-None-Match`, решает он).

**→ «Общие правила v2»:** ответы — JSON, кроме `feed.xml` (RSS, XML).

## 4. ADR

**ADR-1. Формат.** А — RSS 2.0: понимают все читалки и браузерные расширения, привычное «Subscribe (RSS)»; минусы — нестрогая спецификация (даты RFC 822, нет обязательного `updated`). Б — Atom 1.0: строгий (`id`, `updated`, тип содержимого), но выигрыш для подписчика нулевой. В — оба: два шаблона и двойной набор тестов ради одного выигрыша — «правильности». **Рекомендация: А**, с `atom:link rel="self"` (его просит W3C-валидатор) и Media RSS для картинок. Тип — `application/rss+xml; charset=utf-8` (браузер может скачать файл вместо показа — читалкам это неважно).

**ADR-2. Генерация XML.** А — `xml.etree.ElementTree` (stdlib): экранирует текст и атрибуты сам; пространства имён — через `{uri}tag`, без глобального `register_namespace` (вызвать один раз при импорте модуля — допустимо, если префиксы `atom`/`media`). `ET.tostring(root, encoding="utf-8", xml_declaration=True)`. Б — шаблон строкой с `html.escape`: легко забыть экранирование. В — `feedgen`/`lxml`: новая зависимость. **Рекомендация: А.** Правила: (1) любой текст из БД (`title`, `caption`, `credit`, имя камеры) проходит `xml_safe()` — удаление символов вне XML 1.0 (`\x00-\x08\x0b\x0c\x0e-\x1f`, `￾`, `￿`, одиночные суррогаты), иначе etree выдаст невалидный XML; (2) HTML для `description` собирается отдельно, каждое значение — `html.escape(..., quote=True)`, затем строка кладётся текстом элемента (etree экранирует второй раз — так и положено); без CDATA; (3) URL из БД в `href`/`src`/`url` — только `http`/`https`, иначе ссылка опускается; (4) origin — из `request_origin`, тоже через экранирование (Host приходит от клиента).

**ADR-3. Что считать новым.**
- А. `taken_at` — снимки старых солов, дошедшие поздно (90% — до 10–13 сут.), окажутся глубоко в ленте и будут пропущены. Отпадает.
- Б. `received_at` (JPL получил). Заливки не всплывают (у них старый `received_at`), но если JPL выложил кадр позже его `received_at` или наша синхронизация стояла, снимок вставится «ниже» уже показанных и выпадет за `limit`. Ключ меняется, если JPL переобработает кадр.
- В. `created_at` (сохранили мы). Всё новое — сверху, но любая заливка = сотни тысяч «новых» (факт dev-БД), отличить её нечем; индекс по `(rover_id, created_at)` на 1,7 млн строк ради ленты.
- Г. Новый столбец `feed_at timestamptz NULL` — ставится `now()` (то есть равен `created_at`) только при вставке в режиме `incremental`; upsert существующей строки его не меняет; `backfill`, `import-mer` — `NULL`. Частичный индекс только по таким строкам (~1–1,5 тыс. в сутки на марсоход).
- **Рекомендация: Г.** Это ровно смысл «новое для подписчика»: что пришло регулярной синхронизацией. Минусы: правка пути записи (`upsert_photos` получает флаг) и миграция. Переобработанный JPL кадр с новым `source_id` придёт как новый — это и есть новый продукт. Окно ленты — 30 дней (`FEED_WINDOW_DAYS`), оно же ограничивает просмотр индекса.

**ADR-4. Запись ленты.** Curiosity/Perseverance дают сотни снимков за синхронизацию: при «запись = снимок» и `limit ≤ 100` читалка, опрашивающая раз в несколько часов, теряет большую часть, а лента марсохода превращается в поток. А — снимок: удобно для редких камер (`SUPERCAM_RMI`, `CACHECAM`), картинка в каждой записи. Б — пачка «сол × загрузка»: 1–5 записей за синхронизацию, ничего не теряется. **Рекомендация: оба, по умолчанию Б (`group=sol`).** Пачка = строки с одинаковыми `(feed_at, sol)`: сол коммитится одной транзакцией, `now()` в ней одинаковый, — значит пачка неизменна после коммита; повторная доставка сола в той же синхронизации даёт новую пачку («ещё 3»), а не правку старой. `guid` пачки — `sol-index:{rover}:sol-{sol}:{feed_at в мкс}` + суффиксы фильтров (`:camera-X`, `:type-Y`), не зависит от хоста; у снимка — URL карточки.

**ADR-5. Путь и ошибки.** А — `/api/v2/rovers/{rover}/feed.xml?…`: работает имеющийся `caching.py` (ETag, HEAD, gzip, CORS), проверка `camera`/`sample_type` — те же функции, что у `/photos`. Б — отдельный `/feeds/...` вне API: свой кэш, свой обход интерфейса (`frontend.py` перехватывает неизвестные пути). **Рекомендация: А.** Суффикс `.xml` — подсказка читалкам и людям. Ошибки — обычный JSON v2 с `no-store`: читалки показывают только статус, а XML-ошибка была бы вторым форматом ради никого.

**ADR-6. Кэш и нагрузка.** Тело детерминировано (нет «сейчас»: `lastBuildDate` = последний `feed_at`), поэтому `ETag` по телу стабилен между синхронизациями. `If-Modified-Since` — в `caching.py` общим правилом: если ответ `200` несёт `Last-Modified`, в запросе нет `If-None-Match`, а `If-Modified-Since` разбирается и не раньше `Last-Modified` (с точностью до секунды) → `304`; тело всё равно строится (2 запроса по частичному индексу, единицы–десятки мс). `max-age=900`: синхронизация раз в 6 ч, но сол коммитится по ходу. Лимит частоты не снимаем: читалка опрашивает ленту раз в 15–60 мин, агрегаторы (Feedly и т. п.) — один раз на ленту для всех; 60/мин на IP хватает. Индекс: `ix_photos_feed (rover_id, feed_at DESC, id DESC) INCLUDE (camera_id, sample_type, sol, taken_at) WHERE feed_at IS NOT NULL` — фильтры камеры и типа проверяются по индексу; объём за окно 30 дней — ~30–45 тыс. строк на марсоход.

**ADR-7. Архивные марсоходы.** А — `200` с пустой лентой: подписка, которая никогда не обновится, вводит в заблуждение. Б — `404 not_found` (как `/flights` у не-Ingenuity). **Рекомендация: Б**; список — `SYNCABLE_ROVERS` из `app/sources/registry.py`. Кнопки в интерфейсе у архивных не показываются.

**ADR-8. Обнаружение ленты интерфейсом.** Читалки ищут `<link rel="alternate">` в HTML без JS, а `index.html` один на все пути. А — в статический `<head>` две ссылки: ленты Perseverance и Curiosity (относительные `href`), плюс SPA добавляет/меняет `<link>` текущей страницы (для расширений, читающих живой DOM). Б — сервер подставляет ссылку в `index.html` по пути: шаблонизация и разные `ETag` у одной страницы. **Рекомендация: А.**

## 5. Модель данных, миграция, поток

**0011 `20261123_0011_photos_feed_at.py`** (`down_revision "0010"`):
1. `ALTER TABLE photos ADD COLUMN feed_at timestamptz NULL` (без значения по умолчанию — без перезаписи таблицы).
2. Заполнение последних 30 дней: `UPDATE photos p SET feed_at = p.created_at WHERE p.created_at >= now() - interval '30 days' AND EXISTS (SELECT 1 FROM sync_runs r WHERE r.rover_id = p.rover_id AND r.mode = 'incremental' AND p.created_at BETWEEN r.started_at AND coalesce(r.finished_at, now()))` — блокировка марсохода исключает параллельный `backfill`, так что совпадение по времени однозначно. На проде — тысячи строк; в dev-БД — 0 (лента пуста до первой синхронизации).
3. Индекс `ix_photos_feed` (ADR-6) `CONCURRENTLY` в `autocommit_block`, как 0005/0009.
`downgrade`: `DROP INDEX CONCURRENTLY`, `DROP COLUMN feed_at`. Инвариант: `feed_at IS NULL OR feed_at = created_at`.

**Поток:** `SyncService` (`incremental`) → `upsert_photos(..., mark_new=True)` → `feed_at = now()` во вставляемых строках, вне `ON CONFLICT … SET` → `photos` → `app/repositories/feeds.py` → `app/services/feeds.py` (окно, группировка, выбор миниатюр) → `app/api/v2/feeds.py` (рендер RSS, `Last-Modified`) → `caching.py`. Запросов к источникам нет.

**Настройки:** `FEED_WINDOW_DAYS` (30).

## 6. Задания ролям и общие имена

**Общие имена** (`backend-dev` реализует, `qa-engineer` тестирует параллельно):
- `app/db/models.py`: `Photo.feed_at: Mapped[datetime | None]`. `app/repositories/photos.py`: `upsert_photos(session, rover_id, records, *, mark_new: bool = False)`; `feed_at` — в списке «никогда не перезаписывать».
- `app/repositories/feeds.py`: `latest_feed_at(session, rover_id, *, camera_id, sample_type, since)`, `feed_photos(session, rover_id, *, camera_id, sample_type, since, limit) -> list[Photo]`, `feed_batches(...) -> list[FeedBatchRow]` (`feed_at, sol, total, cameras: list[tuple[code, count]], credits: list[str]`), `batch_thumbnails(session, rover_id, keys, *, camera_id, sample_type, per_batch=6)`.
- `app/services/feeds.py`: `FeedQuery(rover, camera, sample_type, group, limit)`, `build_feed(session, query, now=None) -> Feed`, `Feed(items, last_modified)`, `FeedBatch`, `FEED_GROUPS = ("sol", "photo")`, `FEED_DEFAULT_LIMIT = {"sol": 20, "photo": 50}`, `FEED_MAX_LIMIT = 100`, `FEED_THUMBNAILS = 6`, `DEFAULT_CREDIT = "NASA/JPL-Caltech"`.
- `app/api/v2/feeds.py`: роут `feed.xml`, `render_rss(feed, origin, self_url) -> bytes`, `xml_safe(text) -> str`, `FEED_MEDIA_TYPE = "application/rss+xml; charset=utf-8"`. `app/api/caching.py`: `FEED_CACHE_CONTROL = "public, max-age=900"`, общее правило `If-Modified-Since`.
- Ошибки — существующие `RoverNotFound`, `NotFound`, `validation_error`.

**`backend-dev`:** всё выше + миграция 0011, настройка, `.env.example`; `SyncService` передаёт `mark_new=(mode == "incremental")`; `backfill`, `import-mer` — без флага.

**`qa-engineer`:**
- unit: `xml_safe` (`\x00`, `\x0b`, `￾`, суррогат — удалены; кириллица, эмодзи, `&<>"'` — сохраняются и экранируются); `render_rss` — парсится `ET.fromstring`, пространства имён, `guid` пачки без хоста, RFC 822 даты, `javascript:` в `img_src` → ссылки нет; caption `</description><script>` и `]]>` остаются текстом; Host `evil"><x` в ссылках экранирован.
- integration: инкремент (respx) вставляет снимки → `feed_at = created_at`; повторный инкремент тех же снимков → `feed_at` не меняется; `backfill` нового сола → `feed_at IS NULL`; два сола в одном запуске → две пачки; повторная доставка сола → третья пачка, первая не изменилась; окно 30 дней (сдвиг `feed_at` вручную); миграция 0011 вверх-вниз, заполнение по `sync_runs` (строка внутри и вне окна инкремента, `backfill`-строка).
- api: `curiosity`/`perseverance` 200 с `Content-Type` RSS; `PERSEVERANCE`; `spirit`/`opportunity`/`ingenuity` → `404 not_found` JSON; `xyz` → `rover_not_found`; `camera` нижним регистром, чужая камера → 422; `sample_type=mixed` → 422; `group=x`, `limit=0/101/abc` → 422; пустая лента — валидный канал без `item`, без `Last-Modified`; `group=photo` — `guid` = карточка, `credit` в каждом `item`, ссылки абсолютные от `Host` запроса; два запроса подряд — одинаковый `ETag`; `If-None-Match` → 304; `If-Modified-Since` равный/позже → 304, раньше → 200, мусор → 200, вместе с несовпадающим `If-None-Match` → 200; `HEAD`; gzip; `Cache-Control: public, max-age=900`; лимит частоты считает ленту; v1 и `/photos` без изменений.

**`data-engineer`:** на проде после выкатки — сколько строк дало заполнение 0011; через неделю — пачек и снимков в сутки на марсоход, доля «поздних» (`feed_at − received_at > 7 сут.`), время ответа ленты с редкой камерой; прогон W3C Feed Validator по двум лентам (вручную, с «ок» владельца на внешний сайт).

**`designer` (малый объём):** кнопка «Subscribe (RSS)» на странице снимков марсохода (учитывает текущие `camera`/`sample_type`, подпись вида «RSS: Perseverance · NAVCAM_LEFT»), переключатель «by sol / every photo» в маленьком поповере с подсказкой «Paste this link into your RSS reader» и кнопкой «Copy»; ссылка «Subscribe to this camera» в карточке снимка; у архивных марсоходов кнопок нет.
**`frontend-dev`** (после макета): две статические `<link rel="alternate" type="application/rss+xml">` в `frontend/index.html`; хук, ставящий `<link>` текущей ленты в `<head>` на страницах `/rovers/{rover}` и `/photos/{id}` и убирающий при уходе; кнопки по макету; показ — только при `data_source = "sync"`.
**`devops`:** после выкатки `alembic upgrade head` (индекс строится `CONCURRENTLY`, минуты на 1,7 млн строк).

## 7. Вопросы владельцу

**В1. Утвердить раздел 3** (`/rovers/{rover}/feed.xml`, параметры, правило «нового», `If-Modified-Since`, `max-age=900`)? Только добавление в v2, v1 не меняется. *Рекомендую: да.*

**В2. Новое = сохранённое регулярной синхронизацией** (миграция 0011 со столбцом `feed_at`); загрузки истории и архивы в ленту не попадают, окно — 30 дней. *Рекомендую: да.*

**В3. Только RSS 2.0, по умолчанию записи-пачки «сол», по выбору — запись на снимок.** *Рекомендую: да; Atom не делаем.*

**В4. Вебхуки** — после RSS отдельным этапом или не делать. Это хранение адресов клиентов, исходящие запросы с нашего ноутбука на чужие адреса (защита от SSRF, повторы, отписка) — заметно больше RSS. *Рекомендую: не делать, пока нет запроса от пользователей.*

**В5. Ленты архивных марсоходов** — `404`, кнопок нет. *Рекомендую: да.*
