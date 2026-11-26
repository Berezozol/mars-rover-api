# Фронтенд: состояние в адресе и «Назад» (пакет 3 ревью 2026-11-26)

Редакция 1 (2026-11-26, HEAD `da13e27`, миграции не затрагиваются). Основа: сводка `docs/reviews/2026-11-26-frontend-summary.md` (пакет 3) и отчёты `2026-11-26-search-stats.md` (находка 2), `samples-flights.md` (1), `timeline-map.md` (2, 6, 12), `data.md` (12), `browse.md` (16); код `frontend/src/` на HEAD.

## 0. Цель, границы, критерий

**Цель:** то, что человек выбрал на странице, переживает «Назад» и ссылку. Фильтры, сортировка и выбор лежат в адресе, им можно поделиться. Позиция прокрутки и страница таблицы восстанавливаются при возврате.

**Входит:**
- восстановление прокрутки при «Назад»/«Вперёд» на всех страницах;
- Search → снимок: ссылка «Back to search results»;
- крошка «Sol N» на снимке помнит страницу списка, из которого пришли;
- Samples: фильтры в адресе; Flights: сортировка в адресе;
- RouteMap: выбранная стоянка в адресе; `?sol=` без стоянки в этот сол;
- Data, погода: параметр в адресе; таблица солов (погода и радиация) помнит страницу, сортировку и подсвеченную строку при возврате;
- Timeline: неверный диапазон не попадает в адрес, «Назад» отменяет смену диапазона.

**Не входит:**
- контракт API и бэкенд;
- Newer/Older на снимке, открытом из поиска (они остаются по солу);
- кнопки Swap / Last 100 sols у Timeline, сброс неверных `?from=&to=` у радиации, зум и сдвиг карты в адресе;
- остальные пакеты сводки (4–7).

**Готово, когда:**
- AC-1…AC-10 выполнены, vitest и e2e (с axe) зелёные, прежние тесты не ослаблены;
- `reviewer` без открытых замечаний;
- живая проверка на `localhost:8000`: каждый сценарий «Назад» из AC пройден руками на 1280 и 375 px.

## 1. Факты

Пути — относительно `frontend/src/`.

- **Прокрутка.** `components/Layout.tsx:113-122` при любой смене `pathname` делает `window.scrollTo(0, 0)` — и на переходе по ссылке, и на «Назад» (`popstate`). `router.tsx` не хранит ничего в `history.state` (`navigate` пишет `null`), `history.scrollRestoration` не задан. На Photos «Назад» сейчас иногда возвращает позицию — штатное восстановление браузера успевает после `scrollTo(0,0)`, потому что список уже в кеше; на Search, Samples, Flights данные приходят позже, и позиция теряется (отчёты).
- **Search → снимок.** Плитки поиска не передают `from` (`pages/Search.tsx:577`). `pages/Photo.tsx:50` (`parseFrom`) принимает только `/rovers/<id>?…`, поэтому из поиска снимок не знает, откуда пришли.
- **Крошка «Sol N».** `Photo.tsx:231-233` ведёт на `/rovers/<id>?sol=N` без страницы и фильтров списка `from`.
- **Samples / Flights.** Фильтры Samples — `useState` (`pages/Samples.tsx:74-75`), значения: статус `on_rover | depot`, тип `rock_core | regolith | atmospheric` (`api/types.ts:273-274`). Сортировка Flights — `useState` (`pages/Flights.tsx:87-88`), ключи `flight | sol | date | distance | alt | speed | dur | photos` (`lib/flights.ts:40`).
- **RouteMap.** Выбор стоянки читается из `?sol&site&drive` один раз (`RouteMap.tsx:382-385`), но `select` (`:473-483`) адрес не меняет. `lib/maplinks.ts:4-19` (`findStop`) без точной стоянки ищет только стоянку ровно в этот сол, иначе `null` — `?sol=344` у Perseverance молча ничего не выбирает.
- **Погода.** `components/DataWeather.tsx` читает `?rover&from&to` (`lib/solRange.ts:98-120`, `replaceState`), параметр — локальное состояние `k` (`:237-246`).
- **Таблица солов.** `components/SolTable.tsx:56-61`: сортировка, страница и подсветка — `useState`, при возврате из отчёта по солу таблица снова на странице 1 без подсветки.
- **Timeline.** `pages/Timeline.tsx:158-159` пишет диапазон `navigate(…, {replace: true})` после проверки только на «целые числа» (`:384-391`). `?from_sol=1500&to_sol=100` записывается и потом показывает «Invalid range»; у Perseverance 3000–4000 обрезается до `max_sol` и сообщает «From больше To». У полей нет `max`.

## 2. Критерии приёмки

Проверяют `frontend-qa` (vitest — логика и компоненты; e2e Playwright — переходы «Назад» в браузере) и тимлид (живая проверка).

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | `/search?rover=curiosity&sol_min=300&sol_max=400` (результатов больше одного экрана), прокрутка к плитке ниже первого экрана → открыт снимок → «Назад» → та же позиция прокрутки (±50 px) после загрузки результатов; при открытии новой страницы по ссылке прокрутка по-прежнему наверху. Так же на Photos, Samples, Flights. | frontend-qa, тимлид (живая) | e2e |
| AC-2 | Снимок, открытый из поиска → адрес `/photos/<id>?from=/search?…`, на странице ссылка «Back to search results» ведёт ровно на адрес поиска. `from`, не начинающийся с `/search` или `/rovers/` (в том числе `//host`, `https://…`, `javascript:`), ссылку не даёт. | frontend-qa, тимлид (живая) | компонент + e2e |
| AC-3 | `/photos/<id>?from=/rovers/curiosity?sol=344&page=3` (снимок сола 344) → крошка «Sol 344» ведёт на `/rovers/curiosity?sol=344&page=3`; если сол снимка не совпадает с `sol` из `from` — на `/rovers/curiosity?sol=SOL (сол снимка)`, как сейчас. | frontend-qa, тимлид (живая) | компонент |
| AC-4 | Samples: выбраны «Depot» и «Rock core» → адрес `/rovers/perseverance/samples?status=depot&type=rock_core`; прямой заход по этому адресу показывает те же фильтры; неизвестное значение (`?status=foo`) → фильтр «All», адрес очищается от него. Открыт «Show on route map» с прокруткой к образцу № 25 → «Назад» → те же фильтры, образец № 25 в первом экране. | frontend-qa, тимлид (живая) | компонент + e2e |
| AC-5 | Flights: сортировка «Distance» по убыванию → адрес `/rovers/ingenuity/flights?sort=distance:desc`; прямой заход даёт ту же сортировку (заголовок с `aria-sort="descending"`, на телефоне — то же в select); неверный `sort` → сортировка по умолчанию. Из строки → снимки → «Назад» → та же сортировка и строка в первом экране. | frontend-qa, тимлид (живая) | компонент + e2e |
| AC-6 | RouteMap: выбрана стоянка (щелчок по маркеру, строке списка или клавишами) → адрес содержит `sol`, `site`, `drive` этой стоянки, без новой записи в истории на каждый выбор. «Photos at this stop» → «Назад» → та же стоянка выбрана, её маркер в видимой области карты, список на странице с ней. | frontend-qa, тимлид (живая) | e2e |
| AC-7 | `/rovers/perseverance/map?sol=344`, где в сол 344 стоянки нет → выбрана последняя стоянка с sol ≤ 344 и видна подпись «No stop on sol 344 — showing the last stop before it, sol N.» Сол раньше первой стоянки → ничего не выбрано, подпись «No stops on or before sol 344.» `?sol=1500&site=60&drive=0` без такой стоянки → то же правило по `sol`. | frontend-qa, тимлид (живая) | юнит + компонент |
| AC-8 | `/data/weather?rover=curiosity&param=pressure_pa&from=300&to=400` → выбран параметр давления и диапазон 300–400; смена параметра чипом обновляет `param` в адресе (без новой записи в истории); неизвестный `param` → первый параметр, `param` убран из адреса. | frontend-qa, тимлид (живая) | компонент |
| AC-9 | Таблица солов погоды (и радиации): страница 2, сортировка не по умолчанию → строка сола открывает отчёт по солу → «Назад» → та же страница, сортировка, строка этого сола подсвечена и в первом экране. При новом заходе на страницу по ссылке таблица начинается как сейчас (стр. 1, сол по убыванию). | frontend-qa, тимлид (живая) | e2e |
| AC-10 | Timeline: в форме From > To, From или To больше `max_sol`, или не целое → адрес не меняется, поле с ошибкой `aria-invalid="true"`, текст называет допустимый диапазон («Sols run 0–2031.» или «“From” must not be greater than “To”.»), у полей `min="0"` и `max=<max_sol>`. Верный диапазон → новая запись в истории: «Назад» возвращает прежний диапазон и остаётся на Timeline. | frontend-qa, тимлид (живая) | компонент + e2e |

## 3. Решения

1. **Две полки состояния.** То, чем делятся (фильтры, сортировка, параметр, диапазон, выбранная стоянка), — в query; переходное (прокрутка, страница и подсветка `SolTable`) — в `history.state` текущей записи. Таблица солов не засоряет адрес и не путает того, кто открыл ссылку.
2. **Router.** `router.tsx` получает:
   - `history.scrollRestoration = "manual"`;
   - перед `navigate` (push) текущая запись получает `scrollY` через `replaceState({...history.state, scroll})`; так же `navigate(..., {replace:true})` и `writeRangeQuery` сохраняют чужие поля `history.state`, а не пишут `null`;
   - `readHistoryState<T>(key)` / `writeHistoryState(key, value)` — для `SolTable`;
   - `useRestoreScroll(ready: boolean)` — страница с загрузкой вызывает его, когда данные показаны; при `popstate` прокрутка ставится на сохранённую, с повторами до 2 с, пока высоты документа не хватает; любое действие пользователя (колесо, касание, клавиша) повтор отменяет.
3. **Layout.** Прокрутка наверх и фокус на `<main>` — только при переходе вперёд (push/replace по ссылке), не при `popstate`. Признак — событие роутера с полем `kind: "push" | "pop"`.
4. **Хук адреса.** `useQueryParam(name, parse, fallback)` в `router.tsx` → `[value, set]`; `set` пишет `navigate(..., {replace: true})`, значение по умолчанию из адреса убирает. Неверное значение даёт `fallback` и вычищается из адреса `replace`-ом. Пользуются Samples (`status`, `type`), Flights (`sort` = `<key>:<asc|desc>`), погода (`param`), RouteMap (`sol`, `site`, `drive` вместе).
5. **Search → снимок.** `PhotoTile` в поиске получает `from` = текущий `/search?…`. В `Photo.tsx` отдельная функция `backToSearch(from)`: возвращает адрес, только если он начинается с `/search` (не `//`); ссылка «← Back to search results» над крошками. `parseFrom` и соседи по солу не меняются.
6. **Крошка «Sol N».** Если `parseFrom(from)` даёт тот же марсоход и тот же `sol`, что у снимка, крошка ведёт на `from` без `camera` и `sample_type` (как `withPage`), иначе — как сейчас.
7. **RouteMap `?sol=`.** `findStop` при отсутствии стоянки в сол берёт последнюю с sol ≤ N и сообщает, что подставил (`{ index, exact: false }`); подпись — в той же области `role="status"`, что и описание стоянки. Выбор пишет `sol/site/drive` через `replace`. При начальном выборе карта центрируется на стоянке.
8. **Timeline.** Проверка формы до `navigate`: целые, From ≤ To, оба ≤ `max_sol`; запись — `navigate` без `replace`. Ошибка — у поля (`field-err`), как в формах ключей. Ручной адрес с неверным диапазоном показывает нынешнее «Invalid range».
9. **Тексты** (английский интерфейс): «Back to search results», «No stop on sol N — showing the last stop before it, sol M.», «No stops on or before sol N.», «Sols run 0–M.», «“From” must not be greater than “To”.»

## 7. Задания ролям и общие имена

**Общие имена:**
- `router.tsx`: `useQueryParam`, `readHistoryState`, `writeHistoryState`, `useRestoreScroll`; событие навигации с `detail.kind`;
- `lib/maplinks.ts`: `findStop` возвращает `{ index: number; exact: boolean } | null`;
- `pages/Photo.tsx`: `backToSearch(from: string | null): string | null`; ссылка `data-testid="back-to-search"`;
- RouteMap: подпись о подстановке `data-testid="stop-fallback"`;
- `history.state` таблицы: ключ `solTable:` + id таблицы со значением `{ page, sort, hl }`.

Исполнители:
- `frontend-dev` (`frontend/src/`, кроме тестов): решения 1–9. Файлы: `router.tsx`, `components/Layout.tsx`, `components/PhotoTile.tsx`, `components/SolTable.tsx`, `components/DataWeather.tsx`, `components/DataRadiation.tsx` (если нужно для AC-9), `lib/maplinks.ts`, `lib/solRange.ts`, `pages/Search.tsx`, `pages/Photo.tsx`, `pages/Photos.tsx`, `pages/Samples.tsx`, `pages/Flights.tsx`, `pages/RouteMap.tsx`, `pages/Timeline.tsx`. Тесты не пишет, `git stash` и другие команды, меняющие рабочее дерево целиком, не выполняет.
- `frontend-qa` (`frontend/tests/`, `frontend/e2e/`): тесты по AC-1…AC-10 с префиксом `url-state AC-N`, таблица «AC → тест»; e2e — новый `e2e/url-state.spec.ts`, добавить в `testMatch` `e2e/preview.config.ts`. Прокрутку проверять в e2e (jsdom не считает высоту). Прежние тесты не ослаблять; если старый тест противоречит AC, сообщить тимлиду.
- `reviewer`: соответствие AC и решениям; `from` не даёт открытого перенаправления; `history.state` не теряет чужие поля; нет лишних записей в истории.
- Остальные роли не участвуют. `security-reviewer` не нужен: ручки и раздача не меняются, `from` проверяет `reviewer`.

## 8. Проверка и бюджет

- **Команды** в `frontend/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npx playwright test -c e2e/preview.config.ts`.
- **Живая проверка:** `localhost:8000`, сценарии AC-1…AC-10 на 1280 и 375 px, одна тема (тексты и цвета не меняются, кроме подписей AC-7 и AC-10 — их в обеих).
- **Ожидаемый расход:** `architect` и `data-engineer` не запускаются. `frontend-dev` — один запуск, крупнее пакета 2 (затронуто ~15 файлов); `frontend-qa` — один запуск, сопоставимо с пакетом 2; `reviewer` — один-два запуска.

## 9. Внешние запросы (для «ок» владельца)

Нет. Тесты на моках, живая проверка только на `localhost:8000`. Страницы сами грузят снимки с хостов NASA/JPL — это штатное поведение продукта.

## 10. Вопросы владельцу

1. **«Назад» после смены фильтров Samples, сортировки Flights, параметра погоды и выбора стоянки.** Рекомендация: `replace` — «Назад» уводит со страницы, а не перебирает каждый щелчок; Timeline (диапазон вводится формой) — `push`, как просит ревью.
2. **Страница таблицы солов не в адресе, а в `history.state`.** Рекомендация: так — ссылкой делятся диапазоном и параметром, а «стр. 3, сортировка по ветру» нужна только при возврате.

**Решение владельца 2026-11-26 («Ок, по рекомендациям»):** вопросы 1 и 2 приняты по рекомендации.
