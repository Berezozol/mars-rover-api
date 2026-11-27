# Фронтенд: оболочка и навигация (пакет 4 ревью 2026-11-26)

Редакция 1 (2026-11-27, HEAD `5637b6f`, миграции не затрагиваются). Основа: сводка `docs/reviews/2026-11-26-frontend-summary.md` (пакет 4, вопрос 1) и отчёт `2026-11-26-shell-status-notfound.md` (находки 1–4, 6, 14, 15); макеты `design/mockups/index.html` (шапка), `map.html` (вкладки вида); код `frontend/src/` на HEAD.

## 0. Цель, границы, критерий

**Цель:** шапка — только про сайт целиком; всё про один марсоход — в одном ряду вкладок вида, одинаковом на всех его страницах. Навигация помещается на телефоне, заголовки страниц и тема ведут себя предсказуемо.

**Решение владельца 2026-11-27 (вопрос 1 сводки, «Как предлагает ревью»):** в шапке только глобальные пункты; на всех страницах марсохода один и тот же ряд вкладок вида; вкладка, которой у марсохода нет, скрыта, а не ведёт на другой марсоход. Точный набор и порядок — по коду и макетам (набросок владельца был примерным).

**Входит:**
- новая шапка и общий ряд вкладок вида (AC-1);
- навигация на 375 px и подвал (AC-2);
- один `h1` на страницах-заглушках, `/photos/abc` → NotFound, фокус на `h1` после перехода (AC-3);
- переключатель темы Light / Dark / Auto (AC-4).

**Не входит:**
- HTTP 404 с сервера на неизвестные адреса (вопрос 6 сводки не решён) — только фронтенд;
- контракт API, бэкенд, макеты в `design/`;
- остальные находки отчёта оболочки (P2–P3: границы полей 3:1, `<noscript>`, `theme-color`, регистр в `document.title`, шкала кегля).

**Готово, когда:**
- AC-1…AC-4 выполнены, vitest и e2e (с axe) зелёные, прежние тесты не ослаблены;
- `reviewer` без открытых замечаний;
- живая проверка на `localhost:8000` (1280 и 375 px, обе темы) и `/design-review` шапки и одной страницы марсохода (шаг 4г: навигация меняется заметно); P0–P1 исправлены в этом же цикле.

## 1. Факты

Пути — относительно `frontend/src/`.

- **Шапка** (`components/Layout.tsx`, `nav.nav`): Rovers, Photos, Search, Timeline, Route map, Flights (если API знает Ingenuity), меню Data, Stats, API docs (`/docs`, не SPA). Photos/Timeline/Route map ведут на марсоход текущей страницы, иначе на первый из списка (`own ?? list[0]`) — после `/data/weather` «Photos» молча ведёт на Perseverance. Макет `design/mockups/index.html:44-54` — тот же набор.
- **Вкладки вида** есть только на карте (`pages/RouteMap.tsx:128-148`, `ViewTabs`), Samples (`pages/Samples.tsx:119`) и Flights (`pages/Flights.tsx:131`), каждая своя копия; на Photos и Timeline их нет. «Flights» в «Views of Perseverance» уводит на Ingenuity. Макет `map.html:170-176`: Photos, Timeline, Route map, Samples, Flights.
- **Переключатель марсохода** `components/RoverTabs.tsx` — на Photos, Timeline, карте.
- **Что есть у марсохода:** позиции (карта) — только Curiosity и Perseverance (`RouteMap.tsx:310-316`, у остальных «No location data»); образцы — `SAMPLES_ROVER = "perseverance"` (`lib/samples.ts:5`); полёты — Ingenuity (`lib/flights.ts`).
- **Подвал:** For developers, API docs, Status; API keys: Get an API key, My keys, Delete my data. Ссылки 21 px высотой (отчёт, находка 3).
- **375 px:** `.nav` прокручивается вбок (`scrollWidth 675 / clientWidth 343`), без полосы и маски; активный пункт может быть за краем; `overflow-y:hidden` при высоте 37 px режет контур фокуса (`styles-base.css:669-684`).
- **Заголовки:** `EmptyState` (`components/States.tsx:91-108`) всегда даёт `h3`; на `NotFound` и «Rover not found» (Photos, Timeline, карта, Samples, Flights) `h1` нет.
- **`/photos/abc`:** роутер принимает любой id (`router.tsx`, ветка `photo`), API отвечает 422, страница показывает технический текст. Сол роутер проверяет.
- **Фокус после перехода:** на весь `<main>` (`Layout.tsx`, эффект по `pathname`; после пакета 3 — только при переходе по ссылке).
- **Тема** (`lib/theme.ts`): кнопка переключает light/dark и пишет `localStorage['sol-index-theme']`; вернуться к «как в системе» нельзя; подпись называет целевое состояние («Dark» в светлой теме); нет слушателя `matchMedia change` и `storage`. `index.html:28-34` ставит тему до отрисовки.

## 2. Критерии приёмки

Проверяют `frontend-qa` (vitest — компоненты; e2e Playwright с axe — браузер, 1280 и 375 px) и тимлид (живая проверка).

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | Любая страница → в шапке ровно пункты Rovers, Search, Data (меню), Stats, Status, API docs в этом порядке; нет Photos, Timeline, Route map, Flights. `/rovers/curiosity`, `/rovers/curiosity/timeline`, `/rovers/curiosity/map` → один и тот же ряд вкладок `nav[data-testid=view-tabs]` «Photos, Timeline, Route map», текущая с `aria-current="page"`, все ведут на Curiosity. Perseverance → «Photos, Timeline, Route map, Samples». Ingenuity → «Photos, Timeline, Flights» (нет Route map). Spirit/Opportunity → «Photos, Timeline». `/rovers/perseverance/samples` и `/rovers/ingenuity/flights` → тот же ряд своего марсохода. Нигде вкладка не ведёт на другой марсоход. | frontend-qa, тимлид | компонент + e2e |
| AC-2 | 375×812, любая страница из шапки (в том числе `/data/weather`, `/status`) → пункт с `aria-current` целиком в видимой части полосы без ручной прокрутки; если пункты не помещаются, у края, за которым есть ещё пункты, виден признак продолжения (`data-overflow="end"`/`"start"` на полосе и градиент); фокус-контур пункта (Tab) не обрезан — `getBoundingClientRect` контура внутри полосы. То же для ряда вкладок вида. Подвал на 375 → каждая ссылка ≥ 44 px по высоте, группы подписаны («Project», «API keys»); на `/status` ссылка Status в шапке с `aria-current="page"`. | frontend-qa, тимлид | e2e |
| AC-3 | `/no-such-page`, `/rovers/nosuch`, `/rovers/nosuch/timeline`, `/rovers/nosuch/map`, `/rovers/nosuch/samples`, `/rovers/nosuch/flights`, `/photos/abc`, `/photos/-1` → ровно один `h1` на странице («Page not found» / «Rover not found»); `/photos/abc` показывает тот же NotFound, что неизвестный адрес, без запроса к API. Переход по внутренней ссылке → фокус на `h1` новой страницы (`document.activeElement` — `h1`, `tabindex="-1"`); если `h1` ещё нет (загрузка), фокус на `<main>`, а когда `h1` появится в течение 2 с — на нём, если фокус к тому времени не ушёл в другое место. «Назад» фокус не переносит (как в пакете 3). | frontend-qa, тимлид | компонент + e2e |
| AC-4 | Переключатель темы — `select#theme-select` с подписью «Theme» и вариантами Auto, Light, Dark; виден текущий выбор. Light/Dark → `html[data-theme]` и `localStorage['sol-index-theme']` = выбор; Auto → атрибут и ключ удалены, тема по системе. При Auto смена системной темы (`matchMedia change`) сразу меняет тему страницы, выбор остаётся Auto. Выбор в другой вкладке (`storage`) применяется в этой. Перезагрузка → тот же выбор, без вспышки. | frontend-qa, тимлид | компонент + e2e |

## 3. Решения

1. **Шапка:** Rovers (`/`), Search, Data (существующее меню), Stats, Status (`/status`), API docs (`/docs`, обычная ссылка). Status переезжает из подвала в шапку (служебная страница, которую ищут при сбое); For developers остаётся в подвале. Вычисление `own ?? list[0]` и пункт Flights из шапки уходят.
2. **Ряд вкладок вида** — один компонент `components/ViewTabs.tsx`: `ViewTabs({ rover: string, current: View })`, `View = "photos" | "timeline" | "map" | "samples" | "flights"`. Набор — из `lib/views.ts`, `roverViews(roverId): View[]`: всем — photos, timeline; map — Curiosity и Perseverance; samples — `SAMPLES_ROVER`; flights — Ingenuity. Порядок — как в макете: Photos, Timeline, Route map, Samples, Flights. Ряд стоит на Photos, Timeline, карте, Samples, Flights сразу под `RoverTabs` (где он есть), над `h1`; копии в `RouteMap.tsx`, `Samples.tsx`, `Flights.tsx` удаляются. `aria-label` — «Views of <Name>».
3. **Адрес вида, которого нет у марсохода** (`/rovers/ingenuity/map`, `/rovers/curiosity/samples`): поведение страниц не меняется (их пустые состояния уже объясняют), но вкладки на них нет.
4. **375 px:** полоса шапки остаётся одной строкой с горизонтальной прокруткой; при монтировании и смене маршрута активный пункт прокручивается в видимую часть (`scrollIntoView({inline: "nearest", block: "nearest"})` у полосы, без прокрутки страницы); признак продолжения — маска-градиент у края, где есть скрытые пункты, через атрибут `data-overflow` (`start`, `end`, `both`), обновляемый по `scroll` и `resize`; вертикальные отступы полосы вмещают контур фокуса 3 px + offset 2 px. То же — для ряда вкладок вида.
5. **Подвал:** две группы с подписями («Project»: For developers, API docs; «API keys»: Get an API key, My keys, Delete my data), ссылки `min-height: 44px` и `inline-flex` по центру на ширине ≤ 640 px.
6. **Заголовки:** `EmptyState` получает проп `level?: 1 | 2 | 3` (по умолчанию 3). Страничные заглушки — NotFound, «Rover not found» на всех страницах марсохода, 404 отчёта по солу — передают `level={1}`. Роутер принимает `/photos/<id>` только для целого положительного id (`^[1-9]\d{0,9}$`), иначе `notfound`.
7. **Фокус:** Layout после перехода вперёд ищет `main h1`; если есть — `tabindex="-1"` и `focus({preventScroll: true})`; иначе фокус на `<main>` и наблюдение (`MutationObserver` на `main`, до 2 с): появился `h1` и `document.activeElement` всё ещё `<main>` или `body` → фокус на `h1`. Наблюдатель снимается при следующем переходе.
8. **Тема:** `lib/theme.ts` — `ThemeChoice = "auto" | "light" | "dark"`; `getThemeChoice()`, `setThemeChoice(c)` (auto удаляет ключ и `data-theme`), `onSystemThemeChange(cb)`; `storage`-событие синхронизирует вкладки. `index.html` не меняется (он уже ставит только сохранённый light/dark). В шапке — `<label for="theme-select">Theme</label>` (на 375 — визуально скрытая подпись) и `<select id="theme-select">` с Auto/Light/Dark.
9. **Тексты** (английский интерфейс): пункты и вкладки как выше; подписи групп подвала «Project», «API keys»; «Page not found», «Rover not found» — без изменений.

## 7. Задания ролям и общие имена

**Общие имена:**
- `components/ViewTabs.tsx`: `ViewTabs`, `nav[data-testid="view-tabs"]`; `lib/views.ts`: `type View`, `roverViews(roverId)`;
- шапка `nav.nav[aria-label="Main"]`, атрибут `data-overflow` у `nav.nav` и `nav[data-testid=view-tabs]`;
- `EmptyState` проп `level`;
- `lib/theme.ts`: `ThemeChoice`, `getThemeChoice`, `setThemeChoice`, `onSystemThemeChange`; `select#theme-select`;
- подвал: `footer [data-testid=footer-group-project]`, `[data-testid=footer-group-keys]`; `data-testid="footer-status-link"` уходит вместе со ссылкой Status из подвала (ссылка в шапке).

Исполнители:
- `frontend-dev` (`frontend/src/`, кроме тестов): решения 1–9. Файлы: `components/Layout.tsx`, `components/ViewTabs.tsx` (новый), `lib/views.ts` (новый), `components/States.tsx`, `components/RoverTabs.tsx` (если нужно), `pages/Photos.tsx`, `pages/Timeline.tsx`, `pages/RouteMap.tsx`, `pages/Samples.tsx`, `pages/Flights.tsx`, `pages/NotFound.tsx`, `pages/SolReport.tsx`, `router.tsx`, `lib/theme.ts`, `styles-base.css`, `app.css`. Тесты не пишет; `git stash`, `git reset`, `git checkout --`, `git switch` не выполняет.
- `frontend-qa` (`frontend/tests/`, `frontend/e2e/`): тесты по AC-1…AC-4 с префиксом `shell AC-N`, таблица «AC → тест»; e2e — новый `e2e/shell-nav.spec.ts`, добавить в `testMatch` `e2e/preview.config.ts`. Старые тесты, которые ждут пункты Photos/Timeline/Route map/Flights в шапке, кнопку темы или `footer-status-link`, обновить так, чтобы они охраняли то же поведение на новом месте (не удалять молча); перечислить их в отчёте.
- `reviewer`: соответствие AC и решениям; ни одна вкладка не ведёт на другой марсоход; фокус не прыгает при «Назад» и при вводе; тема без вспышки.
- `designer` и `/design-review` — после разработки (шапка + `/rovers/perseverance/map`), отчёт в `docs/reviews/`.

## 8. Проверка и бюджет

- **Команды** в `frontend/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npx playwright test -c e2e/preview.config.ts`.
- **Живая проверка:** `localhost:8000`, 1280 и 375 px, обе темы: шапка, вкладки на всех страницах марсохода (Curiosity, Perseverance, Ingenuity), `/no-such-page`, `/photos/abc`, тема Auto/Light/Dark.
- **Design review:** `/design-review` шапки и `/rovers/perseverance/map` (два субагента).
- **Ожидаемый расход:** `architect` и `data-engineer` не запускаются. `frontend-dev` и `frontend-qa` — по одному запуску, сопоставимо с пакетом 3; `reviewer` — один-два запуска; `/design-review` — два субагента.

## 9. Внешние запросы (для «ок» владельца)

Нет. Тесты на моках, живая проверка только на `localhost:8000`. Страницы сами грузят снимки с хостов NASA/JPL — это штатное поведение продукта.

## 10. Вопросы владельцу

Решены по рекомендации (владелец поручил «как предлагает ревью», набор — по коду и макетам); можно пересмотреть после живой проверки:
1. **Вкладка Data у марсохода** (была в наброске) — не делаем: наборы данных общие для нескольких марсоходов и живут в меню Data шапки; вкладка увела бы со страниц марсохода без ряда вкладок.
2. **Developers в шапке** (был в наброске) — нет: в шапке API docs (как сейчас и в макете), For developers — в подвале. Status — в шапке.
