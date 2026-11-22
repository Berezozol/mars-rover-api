# Этап 19: фронтенд — поиск, снимки рядом с точкой, статистика

Редакция 1 (2026-11-22, HEAD `8f99fb9`, миграции до `0015`). Основа: макеты `design/mockups/search.html`, `near.html`, `stats.html`, `map.html`; `design/NOTES.md`, раздел «Поиск, «рядом с точкой», статистика (этап 19)»; `docs/API.md`: `GET /api/v2/photos`, `/photos/stats`, раздел «Перегрузка»; код `frontend/src/`.

## 0. Цель, границы, критерий

**Входит:**
- экран **Search** (`/search`) на `GET /api/v2/photos`: фильтры, сетка, пагинация с «Go to page», состояния;
- режим **Photos near a point** на карте маршрута (`/rovers/{rover}/map?near=LAT,LON&radius_km=R`) для curiosity и perseverance;
- экран **Stats** (`/stats`) на `GET /api/v2/photos/stats`: сводка, вкладки по месяцам, годам и камерам, графики и таблица;
- пункты «Search» и «Stats» в шапке, ссылки из Stats и карточки стоянки в Search и в режим «рядом».

**Не входит:** изменения API и бэкенда (недостающие поля из NOTES, «Чего не хватает в API (этап 19)», — отдельное решение); разбивка по солам в Stats; мили; Spirit и Opportunity в «рядом с точкой».

**Готово, когда:** три сценария работают на реальном API, все AC ниже покрыты тестами, `npm run lint|typecheck|test|build` зелёные, `/ui-check` без ошибок консоли на 360 px и в тёмной теме.

## 1. Факты

- Решения владельца от 2026-11-22 (ответы на вопросы дизайнера): сортировка поиска по умолчанию `-taken_at`; при выбранной камере и без сола показывается подсказка выбрать сол. «Рядом с точкой» — режим карты. Ingenuity есть в поиске и статистике. В Stats нет разбивки по солам. Радиус задаётся только в км.
- Роутер: `frontend/src/router.tsx` (`matchRoute`), клиент API: `frontend/src/api/client.ts` (`ApiError.retryAfter` уже читается из `Retry-After`).
- `Pager` с «Go to page» уже есть: `frontend/src/components/Pager.tsx`.
- Тяжёлый поиск может ответить `503 service_busy` с `Retry-After` (API.md, «Перегрузка»).
- `page × per_page ≤ 10 000`.

## 2. Критерии приёмки

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | Открыть `/search` без параметров → запрос `GET /api/v2/photos?sort=-taken_at&per_page=…` без `rovers`, сетка снимков, `credit` на каждой плитке | frontend-qa | компонентный |
| AC-2 | `/search?rovers=curiosity&camera=MAST_LEFT&golden_hour=true` → фильтры в форме восстановлены из адреса, запрос содержит те же параметры | frontend-qa | компонентный |
| AC-3 | Изменить фильтры и нажать «Search» → один запрос и адрес обновлён; клики по чипам без «Search» запросов не шлют | frontend-qa | компонентный |
| AC-4 | Выбраны все марсоходы → `rovers` не отправляется; снять последний марсоход нельзя | frontend-qa | компонентный |
| AC-5 | Выбрать 10 камер → 11-я недоступна; снятие марсохода убирает его камеры из выбора | frontend-qa | компонентный |
| AC-6 | Время суток: «Golden hour» и «Custom window» взаимоисключающие (в запросе не бывает `golden_hour` вместе с `local_time_*`); окно `min = max` → ошибка у поля до запроса | frontend-qa | компонентный |
| AC-7 | Сол «от» = «до» → в запросе один `sol`; сол и земная дата одновременно задать нельзя | frontend-qa | компонентный |
| AC-8 | Камера выбрана, сол не задан → видна подсказка выбрать сол (`data-testid="search-sol-hint"`) | frontend-qa | компонентный |
| AC-9 | Ответ `503 service_busy` с `Retry-After: 5` → блок «busy» с обратным отсчётом, кнопка «Try again» неактивна до конца отсчёта, затем повторяет тот же запрос; есть советы сузить поиск | frontend-qa | компонентный |
| AC-10 | Ответ `422` → человеческий текст по `details` и сырой ответ под катом | frontend-qa | компонентный |
| AC-11 | Пустой ответ (`total: 0`) → состояние «ничего не найдено» с подсказкой по активным фильтрам | frontend-qa | компонентный |
| AC-12 | `total` больше 10 000 → число страниц в `Pager` ограничено `10000 / per_page`, над сеткой заметка о пределе | frontend-qa | компонентный |
| AC-13 | `/rovers/curiosity/map?near=-4.7331,137.3985&radius_km=2` → режим «Photos near a point», точка и круг на карте, запрос `GET /api/v2/photos?rovers=curiosity&near=-4.7331,137.3985&radius_km=2…`, сетка снимков под картой | frontend-qa | компонентный |
| AC-14 | Радиус вне 0,01–50 или не число → ошибка у поля, запроса нет; по умолчанию радиус 1 | frontend-qa | компонентный |
| AC-15 | Кнопка «Photos within 1 km» в панели выбранной стоянки → режим «рядом» с координатами стоянки и `radius_km=1` в адресе | frontend-qa | компонентный |
| AC-16 | Ввод широты/долготы в поля (без мыши) задаёт точку; широта вне ±90 или долгота вне ±180 → ошибка у поля | frontend-qa | компонентный |
| AC-17 | Карта Spirit, Opportunity или Ingenuity → переключателя режима «рядом» нет; ссылка `?near=` на них → запроса нет, пояснение почему | frontend-qa | компонентный |
| AC-18 | Режим «рядом» открыт → блок «What is covered» (`near-coverage`): у Curiosity около половины снимков без стоянки, у Perseverance нет снимков посадки | frontend-qa | компонентный |
| AC-19 | Открыть `/stats` → запрос `group_by=rover` для сводки и карточек марсоходов плюс запрос выбранной вкладки (по умолчанию `group_by=month`) | frontend-qa | компонентный |
| AC-20 | Вкладка «By month» или «By year» → у каждого марсохода свой график со своей шкалой; пропущенные периоды дополнены нулями | frontend-qa | компонентный + юнит |
| AC-21 | Переключатель «Chart / Table» → таблица с теми же числами; у графика `role="img"` и текстовый итог | frontend-qa | компонентный |
| AC-22 | Вкладка «By camera» → таблица камер по марсоходам, ссылка «Search» ведёт на `/search?rovers=…&camera=…` | frontend-qa | компонентный |
| AC-23 | Фильтры Stats: сол или дата (не оба), «Full frames only» → `sample_type=full`; пустая выборка (`groups: []`) → состояние «пусто»; 422 и 503 → состояния ошибки | frontend-qa | компонентный |
| AC-24 | Любая страница → в шапке «Search» и «Stats», ведут на `/search` и `/stats`; ширина 360 px → нет горизонтальной прокрутки страницы, фильтры поиска свёрнуты в кнопку «Filters» | frontend-qa | e2e или компонентный |
| AC-25 | Реальные данные: сквозной сценарий «Stats → By camera → Search по камере → открыть снимок» проходит на собранном фронтенде против локального сервера | frontend-qa | e2e |

## 3. Решения

1. Маршруты: `/search`, `/stats`; «рядом с точкой» — параметры `near`, `radius_km` на `/rovers/{rover}/map` и переключатель режима «Browse stops | Photos near a point».
2. Фильтры поиска применяются кнопкой «Search», а не на каждый клик, потому что запросы тяжёлые.
3. «May be slow» — пометка по упрощённым правилам из API.md, раздел «Перегрузка». Признака тяжести в API нет, это хрупко (NOTES, п. 4), но приемлемо.
4. Нули в пропущенных периодах Stats добавляет клиент.
5. Макеты — образец, а не исходник: переключатели «Mockup» в продукт не входят.

## 7. Задания ролям и общие имена

**Общие имена:**
- **Страницы:** `frontend/src/pages/Search.tsx`, `frontend/src/pages/Stats.tsx`, режим «рядом» в `frontend/src/pages/RouteMap.tsx`.
- **Логика без DOM:** `frontend/src/lib/search.ts` (адрес ⇄ фильтры ⇄ параметры запроса, правила «May be slow»), `frontend/src/lib/stats.ts` (`fillPeriods`).
- **Клиент:** `searchPhotos(query)`, `photoStats(query)` в `api/client.ts`.
- **Маршруты:** `{ name: "search" }`, `{ name: "stats" }` в `matchRoute`.
- **`data-testid`:** `search-form`, `search-submit`, `search-results`, `search-sol-hint`, `search-busy`, `search-retry`, `search-empty`, `search-limit-note`, `near-mode`, `near-lat`, `near-lon`, `near-radius`, `near-radius-error`, `near-coverage`, `near-results`, `stop-near-button`, `stats-summary`, `stats-tab-month`, `stats-tab-year`, `stats-tab-camera`, `stats-chart`, `stats-table-toggle`, `stats-table`, `stats-empty`. Состояния загрузки и ошибок — существующие компоненты `States.tsx`.

**Задания:**
- `frontend-dev`: реализация по макетам и этому документу. Файлы тестов не трогает.
- `frontend-qa`: тесты `frontend/tests/search.test.tsx`, `near.test.tsx`, `stats.test.tsx`, `stats-lib.test.ts` (и e2e AC-25 в `frontend/e2e/`) по AC, параллельно, по контракту и макетам; таблица «AC → тест».

## 8. Проверка и бюджет

- **Команды** в `frontend/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.
- **Бэкенд** не меняется; `uv run pytest` не обязателен.
- **Живая проверка:** `/ui-check`: `/search` (данные, busy, пусто), `/rovers/curiosity/map?near=…`, `/stats` (все вкладки, таблица), 360 px, тёмная тема.
- **Ревью:** `reviewer`. Публичная поверхность API не меняется, поэтому `security-reviewer` не нужен.
- **Расход:** frontend-dev и frontend-qa — крупные запуски на Sonnet; architect и data-engineer не участвуют.

## 9. Внешние запросы (для «ок» владельца)

Нет.

## 10. Вопросы владельцу

Решены 2026-11-22 (см. раздел 1).
