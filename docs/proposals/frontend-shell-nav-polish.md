# Фронтенд: доводка шапки и вкладок (P2 ревью 2026-11-27)

Редакция 1 (2026-11-27, HEAD `22d6981`, миграции не затрагиваются). Основа: `docs/reviews/2026-11-27-shell-nav.md`, находки P2 № 2, 4, 5, 7 — в выборе владельца это пункты 1, 3, 4, 6 («1, 3, 4, 6», 2026-11-27); проработка пакета 4 `docs/proposals/frontend-shell-nav.md`.

## 0. Цель, границы, критерий

**Цель:** убрать четыре мелких неудобства новой навигации, найденных ревью дизайна.

**Входит:**
- вкладки Perseverance помещаются на 375 px (пункт 1);
- переключатель марсохода на карте не ведёт в тупик (пункт 3);
- в шапке выделен раздел Rovers на страницах марсохода (пункт 4);
- текущий пункт шапки отмечен не только оттенком (пункт 6).

**Не входит:**
- слои навигации на карте и переключатель режима (пункт 2) и цели 44 px (пункт 5) — владелец их не выбрал;
- P3 отчёта, бэкенд, контракт, макеты.

**Готово, когда:**
- AC-1…AC-4 выполнены, vitest и e2e (с axe) зелёные, прежние тесты не ослаблены;
- `reviewer` без открытых замечаний;
- живая проверка на `localhost:8000`, 375 и 1280 px, обе темы.

## 2. Критерии приёмки

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | 375×812, обе темы, `/rovers/perseverance/map` и `/rovers/perseverance/samples` → у `nav[data-testid=view-tabs]` `scrollWidth ≤ clientWidth`, атрибута `data-overflow` нет, все четыре вкладки видны целиком; на 1280 вид ряда не меняется. | frontend-qa, тимлид | e2e |
| AC-2 | `/rovers/perseverance/map` и `/rovers/curiosity/map` → в `nav.rover-tabs` ровно Perseverance и Curiosity (марсоходы, у которых в `roverViews` есть `map`); ни одна ссылка не ведёт на `/rovers/<id>/map` марсохода без `map`. На Photos и Timeline переключатель показывает все марсоходы, как сейчас. | frontend-qa | компонент + e2e |
| AC-3 | `/rovers/curiosity`, `/rovers/curiosity/timeline`, `/rovers/perseverance/map`, `/rovers/perseverance/samples`, `/rovers/ingenuity/flights`, `/rovers/curiosity/sols/344/conditions` → пункт Rovers в `nav.nav` с `aria-current="true"` и видимо выделен (как текущий); кнопка Data на этих страницах не отмечена текущей. На `/` у Rovers по-прежнему `aria-current="page"`; на `/data`, `/data/weather` текущая — Data. | frontend-qa, тимлид | компонент + e2e |
| AC-4 | `/stats` (и любой текущий пункт шапки, в том числе кнопка Data на `/data/*`), обе темы, 1280 и 375 → у текущего пункта есть индикатор, различимый без цвета фона: линия снизу толщиной ≥ 2 px цвета `--accent`, контраст линии к фону шапки ≥ 3:1. Высота шапки на 375 не больше нынешних 85 px. | frontend-qa, тимлид | e2e |

## 3. Решения

1. **Вкладки на 375.** При ширине ≤ 640 px горизонтальный отступ `.view-tabs a` уменьшается до `var(--s3)` (`app.css`, `.view-tabs a`); если четырёх вкладок всё равно не хватает — `var(--s2)`. Шрифт не уменьшается.
2. **Переключатель марсохода.** `RoverTabs` получает необязательный проп `view?: View`; если он задан, показываются только марсоходы, у которых `roverViews(id)` содержит этот вид. Карта передаёт `view="map"`, Photos и Timeline — ничего (как сейчас). Если текущий марсохода в отфильтрованном списке нет (`/rovers/ingenuity/map`), переключатель показывает отфильтрованный список без текущего.
3. **Раздел Rovers.** В `Layout.tsx` пункт Rovers получает `aria-current="true"` на маршрутах `photos`, `timeline`, `map`, `samples`, `flights`, `solReport`; на `home` — `"page"`. Меню Data считается текущим только на `data` и `dataSet` (из `here` убираются `samples` и `solReport`).
4. **Индикатор текущего пункта.** `.nav a[aria-current]` и `.nav-menu-btn[aria-current]` получают линию снизу 2 px `--accent` (`box-shadow: inset 0 -2px 0 var(--accent)`, без изменения размеров), фон `--surface-2` остаётся. Проверить контраст `--accent` к фону шапки в обеих темах (≥ 3:1).

## 7. Задания ролям и общие имена

**Общие имена:** проп `view` у `RoverTabs`; `aria-current="true"` у Rovers на страницах марсохода; индикатор — `box-shadow` снизу 2 px `--accent`.

Исполнители:
- `frontend-dev` (`frontend/src/`, кроме тестов): решения 1–4. Файлы: `app.css`, `styles-base.css`, `components/RoverTabs.tsx`, `pages/RouteMap.tsx`, `components/Layout.tsx`. Без `git stash/reset/checkout --/switch`, без коммитов.
- `frontend-qa` (`frontend/tests/`, `frontend/e2e/`): тесты AC-1…AC-4 с префиксом `shell-polish AC-N` — в `tests/shell-nav.test.tsx` и `e2e/shell-nav.spec.ts`. Старые тесты, которые ждут Data текущим на Samples или все пять марсоходов на карте, обновить под решения 2–3, перечислить.
- `reviewer`: соответствие AC и решениям, прежнее поведение пакета 4 не сломано.

## 8. Проверка и бюджет

- **Команды** в `frontend/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npx playwright test -c e2e/preview.config.ts`.
- **Живая проверка:** `localhost:8000`, 375 и 1280 px, обе темы: карта и Samples Perseverance, карта Curiosity, `/stats`, `/data/weather`.
- **Ожидаемый расход:** `architect` и `data-engineer` не запускаются; `frontend-dev`, `frontend-qa`, `reviewer` — по одному короткому запуску. Повторный `/design-review` не нужен (правки мелкие, проверка в живой проверке).

## 9. Внешние запросы (для «ок» владельца)

Нет. Тесты на моках, живая проверка только на `localhost:8000`.

## 10. Вопросы владельцу

Нет: объём выбран владельцем («1, 3, 4, 6»), решения следуют черновикам AC из отчёта ревью.
