# Фронтенд: неверные данные и тексты (пакет 1 ревью 2026-11-26)

Редакция 1 (2026-11-26, HEAD `c923ffe`, миграции не затрагиваются). Основа: сводка `docs/reviews/2026-11-26-frontend-summary.md` (пакет 1) и отчёты групп `2026-11-26-data.md`, `search-stats.md`, `browse.md`, `samples-flights.md`, `sol-report-rescore.md`; решение 3.1 из `docs/proposals/sol-report-polish.md`; макеты `design/mockups/radiation-range.html`, `weather-range.html`; код `frontend/src/` на HEAD.

## 0. Цель, границы, критерий

**Входит:**
- единицы измерения не искажаются регистром (µ не превращается в Μ);
- `credit` радиации и погоды стоит рядом с данными, как в макетах;
- ссылка Search в строке камеры Stats сохраняет фильтры страницы;
- кнопки ‹ › соседнего сола на странице снимков сохраняют фильтры;
- понятный текст ошибки «нижняя граница больше верхней»;
- страница не шире экрана: Stats «By camera» на 375 px, Flights на планшете;
- в отчёте по солу закреплённый час не сбрасывается наведением мыши.

**Не входит:**
- контракт API и бэкенд;
- макеты в `design/`;
- остальные пакеты ревью: ключи, «Назад» и состояние в адресе, навигация, первый экран, касание, второй круг отчёта по солу;
- прочие P2/P3 из отчётов, в том числе невидимая прокрутка таблицы Samples на планшете и слипшиеся ссылки `SolTable`.

**Готово, когда:**
- AC-1…AC-10 выполнены, vitest и e2e (с axe) зелёные;
- `reviewer` без открытых замечаний;
- затронутые экраны просмотрены в браузере на 375 и 1280 px в обеих темах.

## 1. Факты

Все пути — относительно `frontend/src/`.

- **Регистр единиц.** Подписи ячеек мобильной таблицы `SolTable` берутся из `data-label` (`components/SolTable.tsx:339`, `:347`). CSS `data.css:1003-1009` даёт им `text-transform: uppercase`. При этом «E, µGy/day» показывается как «E, MGY/DAY», «UV max, W/m²» — как «UV MAX, W/M²» (ревью, 375 px). Всего `text-transform: uppercase` встречается в CSS 20 раз (`app.css`, `data.css`, `keys.css`, `map.css`); какие из этих мест содержат единицы — `[не проверено]`, поэтому AC-1 написан по результату, а не по списку правил.
- **Credit радиации и погоды.** На непустой странице `credit` выводится только в About внизу (`components/DataRadiation.tsx:430`, `components/DataWeather.tsx:362` → `DataAbout.tsx:63-66`). В пустом состоянии он стоит в карточке (`DataRadiation.tsx:62`, `DataWeather.tsx:109-112`). Макеты ставят строку `Credit: …` под сводкой и под таблицей: `radiation-range.html:1394`, `:1440`; `weather-range.html:1398`, `:1443`. Замер ревью на 375 px: About на ≈13 400 px из 14 700 (радиация) и ≈18 850 из 19 900 (погода).
- **Ссылка Search в Stats.** Строка камеры строит ссылку через `searchLink(g.rover, g.camera)` (`pages/Stats.tsx:819-824`, `lib/search.ts:181-184`) — без периода и без «full frames». Карточки марсоходов используют `searchLinkFor(rover, f, known, alone)` (`Stats.tsx:499-521`), который переносит `when`, `solMin/solMax`, `dateMin/dateMax` и `sampleType`. По `design/NOTES.md:164` переход должен идти «с теми же фильтрами». Замер ревью: при солах 300–400 строка CHEMCAM_RMI показывает 1 170 фото, а ссылка находит 60 235.
- **‹ › на странице снимков.** Ссылки строят адрес `/rovers/${id}?sol=${prevSol|nextSol}` (`pages/Photos.tsx:555`, `:576`), поэтому `camera`, `sample_type` и `per_page` теряются. Кнопка «Go» с полем сола вызывает `onSol` → `go({ ...f, earth_date: undefined, sol: s, page: 1 })` (`Photos.tsx:235`) через `photosUrl` (`Photos.tsx:51`) и фильтры сохраняет. После перехода фокус оказывается на `body` (ревью).
- **Текст ошибки диапазона.** Бэкенд отвечает `<low> must be less than or equal to <high>`:
  - `app/services/v2.py:668` — поля `sol_min`/`sol_max` и другие пары;
  - `v2.py:485`, `locations.py:61` — `from_sol`/`to_sol`;
  - `radiation.py:63`, `weather.py:177` — `sol_min`/`sol_max`.

  Во фронтенде `api/errors.ts:55-63` по регулярке `/less than or equal|…/` подставляет для полей сола текст «must be a whole number from 0 to 2,147,483,647», и тот же вариант срабатывает на нарушение порядка границ. Регулярка нужна и для настоящей ошибки pydantic «Input should be less than or equal to 2147483647» (`tests/client.test.ts:19`, `:119`).
- **Ширина Stats.** Пустой заголовок последней колонки — `<span class="visually-hidden">Link</span>` (`Stats.tsx:785-787`). Класс `.visually-hidden` даёт `position: absolute` (`styles-base.css:235-242`), а у контейнера `.table-wrap` (`Stats.tsx:700`, `:756`) позиционирования нет. В итоге span не обрезается его прокруткой. Замер на 375 px: `documentElement.scrollWidth` = 683, без этого span — 375.
- **Ширина Flights.** У `.flights-card` стоит `overflow: hidden` (`app.css:966-968`), таблица из 9 колонок переходит в карточки только с 760 px. Замер на 800×900: таблица 893 px в контейнере 751 px, колонка Photos (`left` 835) недостижима, `scrollWidth` документа 812. Источник лишних 12 px не локализован `[не проверено]`.
- **Курсор часа.** Курсор хранится как `{ hour, active, live }` (`pages/SolReport.tsx:41-48`). `hourSetter(null)` всегда снимает `active`, а `null` приходит при `onPointerLeave` мыши (`components/HourChart.tsx:190-192`). Поэтому час, выбранный ползунком или клавишами, стирается от проведения мыши над графиком (проверили оба агента). Щелчок мышью час тоже не закрепляет: `onPointerDown` → `onHour(h)`, а уход мыши его снимает. Решение 3.1 из `sol-report-polish.md`: сброс — отдельная кнопка, а не уход мыши. Тест `tests/solreport-polish.test.tsx:128` проверяет, что уход мыши снимает *наведение*, когда час не закреплён; он остаётся верным.

## 2. Критерии приёмки

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | `/data/radiation`, `/data/weather` (Curiosity и Perseverance) и `/rovers/curiosity/sols/344/conditions` на 375 и 1280 px → ни один видимый элемент, в тексте которого есть единица измерения (`µ`, `W/m²`, `Pa`, `°C`, `m/s`), не имеет вычисленного `text-transform: uppercase`; в карточке таблицы радиации на 375 px видна подпись `E, µGy/day` | frontend-qa | e2e (обход DOM + `getComputedStyle`) |
| AC-2 | `/data/radiation` с данными → строка `Credit: …` с полем `credit` из ответа API есть (а) сразу под блоком графиков, до блока выбора диапазона, и (б) под таблицей солов; на 375 px строка (а) находится не ниже чем на 1 экран (812 px) после верха первого графика | frontend-qa | компонентный, e2e на 375 px |
| AC-3 | `/data/weather` с данными (Curiosity и Perseverance) → то же, что AC-2: строка `Credit: …` (поле `credit`) под графиком и под таблицей; при смене марсохода текст берётся из ответа нового марсохода | frontend-qa | компонентный, e2e на 375 px |
| AC-4 | `/stats?rovers=curiosity,perseverance&sol_min=300&sol_max=400&group_by=camera` (и вариант с датами `date_min`/`date_max` и с выключенным «Full frames only») → `href` ссылки Search в строке камеры содержит `rovers` и `camera` этой строки и те же период и тип кадра, что у страницы (как `searchLinkFor`); число найденных на `/search` фото совпадает с числом в строке Stats | frontend-qa | компонентный (href), e2e (число) |
| AC-5 | `/rovers/curiosity?sol=344&camera=NAV_LEFT_B&sample_type=full&per_page=48`, нажата `›` → адрес `/rovers/curiosity?sol=345&camera=NAV_LEFT_B&sample_type=full&per_page=48` (без `page`, без `earth_date`); `‹` → то же с `sol=343`; после перехода фокус на заголовке результатов (`#result-title`) | frontend-qa | компонентный, e2e |
| AC-6 | ответ API 422 с `{field: "query.sol_min", message: "sol_min must be less than or equal to sol_max"}` (и `from_sol`/`to_sol`) → интерфейс показывает «“From sol” must not be greater than “To sol”» (подписи — те же, что у полей экрана); `{field: "query.sol", message: "Input should be less than or equal to 2147483647"}` → прежний текст «must be a whole number from 0 to 2,147,483,647» | frontend-qa | модульный (`humanizeIssue`/`describeError`) |
| AC-7 | `/stats?rovers=curiosity&sol_min=500&sol_max=100` и `/search?rovers=curiosity&sol_min=500&sol_max=100` → текст из AC-6, поля «From sol» и «To sol» помечены `aria-invalid="true"` | frontend-qa | e2e |
| AC-8 | 375×812, `/stats?group_by=camera&rovers=curiosity,perseverance` и 1280 px → `document.documentElement.scrollWidth` равен ширине окна; у заголовка колонки ссылок остаётся доступное имя `Link` | frontend-qa | e2e |
| AC-9 | 800×900 и 940×900, `/rovers/ingenuity/flights` → `documentElement.scrollWidth` равен ширине окна; каждая ячейка колонки Photos достижима: таблица лежит в прокручиваемом регионе с `tabindex="0"`, `role="region"` и `aria-label`, либо при этой ширине показан карточный вид | frontend-qa | e2e |
| AC-10 | отчёт по солу (Curiosity 344), 1280 px: (а) ползунок на 04:00 → мышь проходит над графиком (курсор графиков временно следует за мышью) и уходит → снова выбран 04:00, кнопка `Clear hour` видна; (б) час не выбран → мышь проходит и уходит → `No hour selected` (как сейчас); (в) щелчок мышью по графику на 15:00 → закреплён 15:00, уход мыши его не снимает; (г) `Clear hour` после (а) или (в) → `No hour selected` | frontend-qa | компонентный (события указателя), e2e |

## 3. Решения

1. **Регистр.** У подписей, в которых бывают единицы (в первую очередь `data-label` в `SolTable`, `data.css:1003-1009`), убрать `text-transform: uppercase`. Капитель оставить только там, где единиц не бывает. Писать текст заглавными в исходнике ради «вида» нельзя. Для единиц возможен отдельный `<span>` без трансформации, но проще и надёжнее убрать uppercase у таких подписей целиком.
2. **Credit.** Строка выводится тем же классом `credit`, что в макетах и на странице снимка, в двух местах. About её сохраняет. Текст берётся из ответа API (`data.credit`), его не сокращают и не переписывают.
3. **Stats.** Строка камеры использует `searchLinkFor(g.rover, { ...applied, cameras: [g.camera] }, known, alone)`, а не `searchLink`. `lib/search.ts:searchLink` остаётся для других вызовов.
4. **‹ ›.** Адрес строится через `photosUrl(rover.id, { ...f, earth_date: undefined, sol, page: 1 })` — те же правила, что у кнопки «Go». Фильтры стоянки `site`/`drive_from`/`drive_to` сохраняются, как и у «Go». После перехода фокус переводится на `#result-title`.
5. **Текст ошибки.** В `api/errors.ts` до общей ветки сола распознаётся шаблон `^(\w+) must be less than or equal to (\w+)$`. Для пар `sol_min`/`sol_max` и `from_sol`/`to_sol` показывается «“From sol” must not be greater than “To sol”», для пар дат — то же с «date». Ошибку получают оба поля. Ветка «whole number» остаётся только для ошибок pydantic о границах числа и разборе.
6. **Ширина Stats.** Скролл-контейнеры таблиц (`.table-wrap`) получают `position: relative`, чтобы `.visually-hidden` внутри обрезался прокруткой. Альтернатива — `aria-label` на `th` вместо span; выбирает `frontend-dev`, AC-8 проверяет результат.
7. **Ширина Flights.** Вместо `overflow: hidden` у `.flights-card` — прокручиваемый регион (`overflow-x: auto`, `tabindex="0"`, `role="region"`, `aria-label="Ingenuity flights"`). Порог карточного вида не меняется: это меньшая правка, и она совпадает с паттерном таблиц Data. Порог можно поднять позже в пакете «касание».
8. **Курсор часа.** Курсор разделяется на закреплённый час (ползунок, клавиши, касание, щелчок мышью) и предпросмотр наведения. При уходе мыши показывается закреплённый час, если он есть, иначе состояние «нет часа». `Clear hour` снимает закрепление. Объявление `aria-live` и подсветка строки таблицы идут от закреплённого часа; наведение не объявляется, как сейчас.

## 7. Задания ролям и общие имена

**Общие имена:**
- `searchLinkFor` в `pages/Stats.tsx` — используется и в строке камеры;
- `photosUrl` в `pages/Photos.tsx`;
- функция `rangeOrderText(low, high)` в `api/errors.ts` — новая, возвращает текст AC-6, экспортируется для теста;
- класс `credit` для строки авторства;
- в курсоре отчёта по солу — поле `pinned: number | null` рядом с `hour` (предпросмотр); `data-testid` прежние;
- `data-testid="data-credit-chart"` и `data-testid="data-credit-table"` — строки AC-2/AC-3;
- `aria-label="Ingenuity flights"` у региона таблицы Flights.

- `frontend-dev` (`frontend/src/`, кроме тестов): решения 1–8. Файлы:
  - `data.css` и при необходимости другие CSS с uppercase у подписей с единицами;
  - `components/DataRadiation.tsx`, `components/DataWeather.tsx`, `components/SolTable.tsx` (если подвал таблицы общий);
  - `pages/Stats.tsx`, `pages/Photos.tsx`, `api/errors.ts`;
  - `styles-base.css` или `stage19.css` (`.table-wrap`), `app.css` и `pages/Flights.tsx`;
  - `pages/SolReport.tsx`, `components/HourChart.tsx`.

  Тесты не пишет.
- `frontend-qa` (`frontend/tests/`, `frontend/e2e/`): тесты по AC-1…AC-10, имена с номером (`AC-…` с префиксом `data-texts`, например `data-texts AC-5`), таблица «AC → тест» в отчёте. Существующие тесты не ослаблять. `solreport-polish.test.tsx:128` должен остаться зелёным (это AC-10б). Если старый тест противоречит AC — сообщить тимлиду, а не править молча.
- `reviewer`: соответствие AC и решениям, нет регресса в `sol-report*.spec.ts`, `ac25-stats-to-photo.spec.ts`, `ac46-*.spec.ts`.
- `backend-dev`, `qa-engineer`, `data-engineer`, `tech-writer`, `devops`, `designer`: не участвуют.

## 8. Проверка и бюджет

- **Команды** в `frontend/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, e2e по принятой в проекте команде (`npx playwright test` с `e2e/preview.config.ts`).
- **Живая проверка:** `/ui-check` на `/data/radiation`, `/data/weather`, `/stats?group_by=camera…`, `/rovers/curiosity?sol=344&camera=NAV_LEFT_B&per_page=48`, `/rovers/ingenuity/flights`, `/rovers/curiosity/sols/344/conditions` при 375, 800 и 1280 px, в светлой и тёмной теме. `/design-review` не нужен: экраны заметно не меняются.
- **Ревью:** `reviewer`. `security-reviewer` не нужен: публичная поверхность не меняется.
- **Ожидаемый расход:** `architect` и `data-engineer` не запускаются. `frontend-dev` и `frontend-qa` — по одному запуску среднего размера, сопоставимо с `sol-report-polish`. `reviewer` — один запуск.

## 9. Внешние запросы (для «ок» владельца)

Нет. Тесты работают на моках и фикстурах. При живой проверке браузер на `localhost:8000` сам грузит миниатюры снимков с хостов NASA/JPL — это штатное поведение страниц, переходов на внешние сайты нет.

## 10. Вопросы владельцу

**Решено владельцем 2026-11-26: все три — по рекомендации** (щелчок мышью закрепляет час; Flights — прокручиваемый регион; uppercase убрать у всех подписей с единицами).

1. **Курсор часа — щелчок мышью по графику закрепляет час (AC-10в).** Рекомендация: да. Сейчас закрепить час мышью нельзя ничем, кроме ползунка, а касание уже закрепляет.
2. **Flights на планшете — прокручиваемый регион (решение 7) или поднять порог карточного вида до ~960 px.** Рекомендация: регион сейчас, порог — в пакете «касание».
3. **Регистр подписей — убрать uppercase у всех подписей таблиц и плиток, где бывают единицы, даже если это немного меняет вид мобильных карточек.** Рекомендация: да. Правильная единица важнее капители.
