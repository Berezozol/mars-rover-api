# Фронтенд: поток API-ключей (пакет 2 ревью 2026-11-26)

Редакция 1 (2026-11-26, HEAD `5a4e545`, миграции не затрагиваются). Основа: `docs/reviews/2026-11-26-keys.md` и сводка `2026-11-26-frontend-summary.md` (пакет 2); контракт `docs/API.md` §API-ключи (строки 569–618); проработки `docs/proposals/stage-20-api-keys.md`, `stage-20b-key-signup.md`; код `frontend/src/pages/Keys*.tsx`, `components/KeyParts.tsx`, `lib/keys.ts`, `keys.css` на HEAD.

## 0. Цель, границы, критерий

**Входит:**
- тексты, которые сейчас говорят неправду: «Email confirmed» до проверки, «Key not found» на любой 404, «opening it deletes» и «with one click» на удалении;
- подтверждение удаления по ссылке: явное «cannot be undone», опасная кнопка отличается формой, безопасная не мельче;
- ошибки ввода и фокус после ошибок — одинаково на всех формах потока;
- фокус и объявления вокруг отзыва ключа;
- ссылка «Lost it?» в кабинете и подсказка после перезагрузки страницы по ссылке из письма.

**Не входит:**
- контракт API и бэкенд — в том числе маска адреса на подтверждении удаления (вопрос 1);
- макеты в `design/`;
- P3 из отчёта: `beforeunload` у показанного ключа, `alert-title` как `h2`, `type="password"` у поля ключа, перенос строки `API: …`, форматы дат, `:focus` у заголовков.

**Готово, когда:**
- AC-1…AC-10 выполнены, vitest и e2e (с axe) зелёные, `ac32-keys.spec.ts` не сломан;
- `reviewer` без открытых замечаний;
- четыре страницы потока просмотрены в браузере на 375 и 1280 px в обеих темах.

## 1. Факты

Пути — относительно `frontend/src/`.

- **«Email confirmed».** `pages/KeysConfirm.tsx:70-72` выводит «Email confirmed / The link in your email is valid» для любого токена верной формы (43 символа, `lib/keys.ts:6`, `:12-18`). Запрос `POST /keys/confirm` уходит только по кнопке «Show my key». Ошибка показывается внутри той же карточки (`:73`). Формулировка пришла из макета `design/mockups/keys-confirm.html:58-59`.
- **404.** `components/KeyParts.tsx:79-82` превращает любую `404 not_found` в «Key not found — That key no longer exists on this account. Reload your account…». Этот текст написан под отзыв ключа (`DELETE /account/keys/{id}` → `404 not_found`, `docs/API.md:607`). Но ручки ключей отвечают `404` и тогда, когда выключены `API_KEYS_ENABLED` или `KEY_SIGNUP_ENABLED` (`docs/API.md:569`); код ответа `not_found` (`app/core/errors.py:84`). Ветку «Not available yet» (`KeyParts.tsx:83-85`) поэтому не видно ни на confirm (`KeysConfirm.tsx:73`, без `action`), ни на удалении (`KeysDelete.tsx:106`, `:188`, `action="deletion"`), ни при входе в кабинет (`KeysManage.tsx:246`). `/keys` обрабатывает 404 отдельно, фазой `closed` (`Keys.tsx:53-54`). Локально выдача ключей выключена, поэтому это видно вживую (отчёт ревью).
- **Тексты удаления.**
  - Форма: «Have it? Delete in My keys with one click. We email a link…; opening it deletes your email, keys and usage counters» (`KeysDelete.tsx:99-102`). На деле открытие ссылки ничего не удаляет: нужна кнопка на открытой странице. В кабинете тоже не «один щелчок»: кнопка, флажок, вторая кнопка.
  - Подтверждение (`:177-205`): «Delete your data?», текст «…for good», затем сплошная кнопка «Yes, delete my data» на всю ширину (`btn btn-danger btn-lg btn-block`) и ниже меньшая ссылка-кнопка «No, keep everything».
  - Цвета `--err #a8201a` и `--accent #a83c17` (`styles-base.css:21`, `:33`) почти неразличимы; `.btn-danger` — сплошная заливка `--err` (`keys.css:40-49`).
- **Ошибки и фокус.**
  - `/keys`: пустое поле получает ошибку у поля, `aria-invalid` и фокус, но текст про формат адреса, а не «введите адрес».
  - `/keys/manage`: пустой ключ создаёт `ApiError 401 api_key_required` (`KeysManage.tsx:72-82`) и показывает плашку над формой. У поля нет `aria-invalid`, ошибка не связана с полем, форма сдвигается, фокус остаётся на кнопке (`:236-270`).
  - Ошибки сервера: на confirm (`KeysConfirm.tsx:44` — `failed` не фокусируется), на `/keys` (`Keys.tsx:37-38`) и на удалении (`KeysDelete.tsx:48-50`) фокус уходит на `body`, потому что кнопка на время запроса становится `disabled`.
- **Отзыв ключа.** После «Revoke» фокус ставится на «Yes, revoke» (`KeysManage.tsx:386-390`), поэтому Enter или пробел сразу отзывают ключ. После успешного отзыва фокус уходит на заголовок аккаунта (`:139-144`), а «Key revoked» не объявляется.
- **«Lost it?».** В кабинете «Lost it? Delete my data with just your email» (`KeysManage.tsx:294-297`) ведёт на удаление. На `/keys` тот же вопрос правильно ведёт на «Request a new one».
- **Перезагрузка по ссылке.** `takeToken()` убирает токен из адреса сразу (`lib/keys.ts:12-18`). После перезагрузки `/keys/confirm` показывает «Open the link from your email», а `/keys/delete` — обычную форму удаления по почте (`KeysDelete.tsx:27-35`), без слов о том, что ссылка ещё действует.
- **Тесты.** Строку «Email confirmed» существующие тесты не проверяют (`grep` по `frontend/tests`, `frontend/e2e` пуст). Поток покрыт `tests/keys-*.test.tsx` и `e2e/ac32-keys.spec.ts` (моки API).

## 2. Критерии приёмки

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | `/keys/confirm#token=TOKEN43 (поддельный токен верной формы, 43 символа)` до нажатия → в карточке нет слов «confirmed» и «valid»; заголовок `One step left`, текст говорит, что кнопка создаст ключ и что о просроченной ссылке страница скажет здесь же | frontend-qa | компонентный |
| AC-2 | на `/keys/confirm` «Show my key» → ответ с ошибкой (400 уже даёт фазу «invalid»; для 404, 500, сетевой ошибки) → заголовок карточки меняется на `Could not create your key`, позитивного заголовка над ошибкой нет | frontend-qa | компонентный |
| AC-3 | ответ `404 not_found` на `POST /keys/confirm`, `POST /account/deletion-confirm`, `POST /account/deletion-requests` и `GET /account` (вход в кабинет) → заголовок `Not available right now`, текст «This part of Sol Index is not open right now»; на обеих формах удаления — плюс «Reply to any email we sent you and we will delete your data by hand»; слов «Key not found» и «Reload your account» нет | frontend-qa | компонентный, e2e (мок 404) |
| AC-4 | `DELETE /account/keys/{id}` → `404 not_found` в кабинете → прежний текст «Key not found … Reload your account…» (только здесь) | frontend-qa | компонентный |
| AC-5 | `/keys/delete`, форма → текст говорит, что письмо содержит ссылку, а удаление подтверждается кнопкой на открытой странице; про кабинет — без «one click» (например, «Have it? Delete in My keys»); на `/keys/delete#token=TOKEN43` → под вопросом `Delete your data?` есть фраза `This cannot be undone.` | frontend-qa | компонентный |
| AC-6 | `/keys/delete#token=TOKEN43` на 375 и 1280 px → «No, keep everything» стоит раньше «Yes, delete my data» в порядке DOM и табуляции; ширина опасной кнопки не больше ширины безопасной; опасная кнопка отличается от `.btn-primary` не только цветом: вычисленный `background-color` у неё прозрачный или `--surface`, рамка и текст цвета `--err` (контурная), на `:hover` — заливка; обе ≥ 44 px по высоте; то же правило для «Yes, delete everything» в кабинете | frontend-qa | e2e (`getComputedStyle`, `getBoundingClientRect`) |
| AC-7 | пустая отправка: `/keys` → у поля текст `Enter your email address.`; `/keys/manage` → у поля текст `Enter your API key.`, у поля `aria-invalid="true"`, `aria-describedby` указывает на текст ошибки, фокус в поле, плашки над формой нет; `/keys/delete` — как `/keys`; неверный формат адреса → прежний текст про формат | frontend-qa | компонентный |
| AC-8 | ошибка сервера (500 или сетевая) после отправки на `/keys`, `/keys/delete` (форма и подтверждение), `/keys/confirm` и при входе в `/keys/manage` → `document.activeElement` не `body`: фокус на плашке ошибки (`tabindex="-1"`) или на кнопке, которой снова можно нажать | frontend-qa | компонентный |
| AC-9 | кабинет: нажата «Revoke» у ключа → фокус на «Cancel» (не на «Yes, revoke»); Enter сразу после этого ключ не отзывает; успешный отзыв → область `role="status"` объявляет `Key si_XXXX… revoked`; успешное создание ключа — как сейчас (фокус на «Your new key») | frontend-qa | компонентный |
| AC-10 | `/keys/manage` вне аккаунта → ссылка «Lost it?» ведёт на `/keys` («Request a new key»), удаление — отдельной второй ссылкой; `/keys/confirm` и `/keys/delete` открыты с `#token=TOKEN43`, затем страница перезагружена без хеша → текст «Your link is still valid — open it again from the email» (вместо «Open the link from your email» и вместо формы-по-умолчанию без пояснения); токен при этом не хранится нигде, только флаг в `sessionStorage`, который снимается после показа | frontend-qa | компонентный, e2e (перезагрузка) |

Дополнительно к AC: axe (`serious` и выше) для четырёх страниц в светлой и тёмной теме на 375 и 1280 px остаётся чистым — это входит в e2e AC-6.

## 3. Решения

1. **Confirm до проверки.** Заголовок `One step left` и текст «Press the button to create your API key. If the link has expired or was already used, we will tell you here.» Строка «The key appears on this page only once» остаётся. Фаза `failed` получает свой заголовок `Could not create your key`, ошибка показывается под ним.
2. **404.** У `KeyError` появляется `action="revoke"`, его передаёт только ветка отзыва в кабинете. Текст «Key not found» выводится только при `action="revoke"`. Любая другая `404` — заголовок `Not available right now` с текстом «This part of Sol Index is not open right now. Please check back later.»; при `action="deletion"` добавляется путь «ответьте на письмо». Вход в кабинет передаёт `action="account"`, confirm — `action="key"`.
3. **Тексты удаления.** Форма: «Use this if you no longer have your key. Have it? Delete in My keys. We email you a link; on the page it opens you confirm the deletion. It removes your email, keys and usage counters.» Подтверждение: к нынешнему тексту добавляется отдельная строка «This cannot be undone.»
4. **Опасная кнопка.** `.btn-danger` становится контурной: прозрачный фон, рамка и текст `--err`, при наведении и фокусе — заливка `--err` с `--on-accent`. Кнопки «No, keep everything» и «Yes, delete my data» стоят в одном ряду одинаковой ширины (на 375 px — одна под другой, безопасная первой), обе `btn-lg`. Цветовые токены (`--err`, `--accent`) не меняются: это затронуло бы все ошибки сайта. Кабинет использует тот же класс, поэтому правило действует и там.
5. **Ошибки ввода.** Кабинет проверяет пустой ключ на месте, по той же схеме, что `/keys`: `field-err` под полем, `aria-invalid`, фокус в поле, без искусственного `ApiError`. Пустой адрес даёт отдельный текст «Enter your email address.»
6. **Фокус после ошибки сервера.** Плашка `KeyError` получает `tabIndex={-1}` и `ref`. После ошибки сервера страница переводит на неё фокус. Это касается всех четырёх страниц.
7. **Отзыв ключа.** Фокус после «Revoke» — на «Cancel». Успешный отзыв пишет в постоянную область `role="status"` кабинета «Key si_XXXX… revoked» (префикс ключа из `KeyInfo.prefix`).
8. **Перезагрузка по ссылке.** `takeToken()` при удачном чтении токена ставит `sessionStorage['sol-index-link:<path>'] = "1"` (только флаг, без токена). При загрузке без хеша страница с таким флагом показывает «Your link is still valid — open it again from the email» и снимает флаг. Доступ к `sessionStorage` обёрнут в `try/catch`: без хранилища поведение прежнее. Флаг снимается и тогда, когда ссылка использована или отвергнута (успешная выдача ключа, удаление, `400`), а также при хеше с токеном неверной формы (`clearLinkFlag()`, добавлено при реализации), иначе перезагрузка сказала бы «still valid» про уже использованную ссылку.
9. **«Lost it?»** В кабинете: «Lost it? Request a new key (if you already have two, the new one can replace them). Or delete my data with just your email.»

## 7. Задания ролям и общие имена

**Общие имена:**
- `KeyError` prop `action`: `"key" | "deletion" | "account" | "revoke"`; у плашки `data-testid="key-error"`, `tabindex="-1"`;
- заголовки: `One step left` (confirm, готовность), `Could not create your key` (confirm, ошибка), `Not available right now` (404);
- `data-testid="keys-status"` — область `role="status"` кабинета для объявления отзыва;
- флаг `sessionStorage` с ключом `sol-index-link:/keys/confirm` и `sol-index-link:/keys/delete`, значение `"1"`;
- id текстов ошибок полей: `email-err` (уже есть), `apikey-err` (новый).

Исполнители:
- `frontend-dev` (`frontend/src/`, кроме тестов): решения 1–9. Файлы: `pages/Keys.tsx`, `pages/KeysConfirm.tsx`, `pages/KeysManage.tsx`, `pages/KeysDelete.tsx`, `components/KeyParts.tsx`, `lib/keys.ts`, `keys.css`. Тесты не пишет.
- `frontend-qa` (`frontend/tests/`, `frontend/e2e/`): тесты по AC-1…AC-10 с префиксом `keys-flow AC-N`, таблица «AC → тест». Моки ответов — в форме из `docs/API.md` (тело ошибки v2 `{"error": {"code", "message", "details"}}`, 404 при выключенных ключах — `not_found`). `ac32-keys.spec.ts` и `tests/keys-*.test.tsx` не ослаблять; если старый тест противоречит AC, сообщить тимлиду.
- `reviewer`: соответствие AC и решениям, отсутствие токенов и ключей в адресе, `localStorage`, `sessionStorage` (кроме флага) и консоли.
- `backend-dev`, `qa-engineer`, `data-engineer`, `tech-writer`, `devops`, `designer`: не участвуют. Макет `keys-confirm.html` расходится с новым текстом, обновит его `designer` в своё время, если владелец захочет.

## 8. Проверка и бюджет

- **Команды** в `frontend/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npx playwright test -c e2e/preview.config.ts`.
- **Живая проверка:** четыре страницы на `localhost:8000` (локально выдача ключей выключена, так что 404 из AC-3 видно вживую), 375 и 1280 px, обе темы. Писем не отправлять, ключей не создавать: формы только пустые или с неверными значениями, токены поддельные.
- **Ревью:** `reviewer`. `security-reviewer` не нужен: ручки и хранение не меняются, добавляется только флаг без данных в `sessionStorage`. Если владелец решит иначе — его запуск после `reviewer`.
- **Ожидаемый расход:** `architect` и `data-engineer` не запускаются. `frontend-dev` и `frontend-qa` — по одному запуску, сопоставимо с пакетом 1. `reviewer` — один-два запуска.

## 9. Внешние запросы (для «ок» владельца)

Нет. Тесты на моках; живая проверка только на `localhost:8000`, без отправки писем.

## 10. Вопросы владельцу

**Решение владельца 2026-11-26 («Делай пакет 2»):** маска адреса — вне пакета (вопрос 1 остаётся открытым); вопросы 2 и 3 приняты по рекомендации.

1. **Маска адреса (u***@example.org) на подтверждении удаления по ссылке.** Это правка контракта: `POST /account/deletion-confirm` пришлось бы разделить на «проверить токен и вернуть маску» и «удалить». Рекомендация: не в этом пакете. «This cannot be undone» и контурная кнопка закрывают главный риск, а маску можно взять отдельной задачей через `/contract-change`, если захотите.
2. **Опасная кнопка контурной (решение 4)**, а не сплошной красной. Рекомендация: да. Сплошная `--err` почти не отличается от основной кнопки, а менять сам токен `--err` значит перекрасить все ошибки сайта.
3. **Флаг «ссылка уже открывалась» в `sessionStorage` (решение 8).** Хранится только `"1"` на время вкладки, без токена и адреса. Рекомендация: да. В почтовых приложениях вкладка часто перезагружается, а сейчас человек после этого теряется.
