# Этап 20B: самостоятельная выдача ключей API через e-mail — черновик архитектора

Редакция 1 (2026-11-24, HEAD `514b86d`, миграции до `0018`). Основа: `docs/PLAN.md` (этап 20), `docs/proposals/stage-20-api-keys.md` (20A, решения владельца 2026-11-22; разделы 4.2, 4.5, ADR-5…ADR-8 этого черновика — исходный набросок 20B), `docs/API.md` («Общие правила v2» — ошибки ключа, «Admin» — учётные записи, «Ограничение частоты»), `docs/LEGACY-API.md:22`, код `app/core/apikeys.py`, `app/api/apikeys.py`, `app/services/api_keys.py`, `app/repositories/api_keys.py`, `app/api/v2/admin.py`, `app/core/config.py`, `app/core/logging.py`, миграция `migrations/versions/20261122_0016_api_keys.py`. Решения владельца 2026-11-24: регистрация по почте, письма — с личного Gmail владельца по SMTP (пароль приложения), всё о почте — из `Settings`, без секретов в репозитории; выдача и отзыв через admin — в этом же этапе.

## 0. Цель, границы, критерий

**Входит:**
- самостоятельная выдача: e-mail → письмо со ссылкой → подтверждение кнопкой → ключ один раз;
- кабинет по ключу: посмотреть свои ключи и использование, выпустить второй ключ, отозвать ключ, удалить учётную запись;
- удаление адреса без ключа (ключ потерян): e-mail → письмо со ссылкой → подтверждение удаления;
- отправка писем через SMTP с очередью в базе и повторами; защита от массовой регистрации и спама на чужой адрес;
- экраны Sol Index «Get an API key», «Confirm», «My keys», «Delete my data»; текст о данных на `/developers` и в интерфейсе; секреты SMTP на VPS;
- admin-выдача и отзыв — уже работают с 20A (`API.md`, «Admin»), в 20B добавляется только видимость очереди писем в admin (AC-27).

**Не входит:** капча стороннего сервиса; список одноразовых доменов из внешнего источника; «больше не присылать мне письма» (подавление адреса); пароли и OAuth; смена e-mail у учётной записи; HTML-письма; ключи для самого интерфейса Sol Index (он анонимный); включение `API_KEYS_ENABLED=true` на проде (отдельный шаг с «ок» владельца, раздел 7, `devops`).

**Готово, когда:** при `API_KEYS_ENABLED=true` и настроенном SMTP человек без участия владельца получает ключ по e-mail, видит и отзывает свои ключи, удаляет свои данные (с ключом и без), ответы на запрос ключа не раскрывают, известен ли адрес, а в базе, логах и репозитории нет ни ключей, ни токенов в открытом виде, ни секретов SMTP.

## 1. Факты

- 20A сделан и на проде с `API_KEYS_ENABLED=false` (`PLAN.md`, этап 20). Таблицы `api_accounts`, `api_keys`, `api_key_requests`, `api_key_usage_daily` уже созданы миграцией `0016` (`migrations/versions/20261122_0016_api_keys.py`). `api_key_requests`: `email`, `token_hash bytea NOT NULL UNIQUE`, `created_at`, `expires_at NOT NULL`, `used_at`, индекс `(email, created_at)`; кодом пока не наполняется, только чистится (`cleanup_api_keys`, `app/services/api_keys.py:298`, удаляет истёкшие старше 24 ч).
- Admin-ручки учётных записей и ключей есть (`app/api/v2/admin.py:155-250`, `API.md`, «Admin»): поиск, карточка, ручная выдача, блокировка, отзыв, удаление. `ApiKeyService.issue_key()` (`app/services/api_keys.py:120`) создаёт запись или добавляет ключ существующей, уважает `API_KEY_MAX_PER_ACCOUNT` (2) и сбрасывает кэш резолвера при отзыве и блокировке. `normalize_email()` — там же, строка 46.
- Формат и хэш ключа (`si_` + 32 base62, SHA-256) — `app/core/apikeys.py`; лимит частоты по учётной записи — `app/api/apikeys.py`, `app/api/ratelimit.py`.
- `Settings.public_base_url` есть (`app/core/config.py:165`); настроек почты (`SMTP_*`, `MAIL_FROM`) нет. Зависимости для SMTP в `pyproject.toml` нет.
- `redact_api_key()` (`app/core/logging.py:79`) вычищает `api_key` из строки запроса в логах; e-mail и токены подтверждения сейчас нигде не логируются, потому что ручек выдачи нет.
- Интерфейс Sol Index — собственный роутер `frontend/src/router.tsx` (`matchRoute`), страницы в `frontend/src/pages/`; экранов ключей нет.
- Интерфейс и API — одно приложение на `solindex.net` и `api.solindex.net`, CORS — только `GET` (`stage-20-api-keys.md`, раздел 1): `POST` интерфейса идут с того же источника.
- Gmail: отправка по SMTP `smtp.gmail.com:587` (STARTTLS) или `:465` (TLS) с паролем приложения, который требует двухэтапной аутентификации; Gmail подставляет в `From` адрес аккаунта или подтверждённый алиас; отправленное через SMTP сохраняется в папке «Отправленные» — `[по моим знаниям, не проверено]`.
- Суточный предел Gmail для обычного аккаунта — около 500 писем (получателей) в сутки, для SMTP-клиентов часто называют меньше (порядка 100–500) — `[не проверено]`; превышение блокирует отправку аккаунта на сутки и может задеть личную почту владельца. Поэтому глобальный предел ставим с большим запасом (ADR-4).
- Открыт ли исходящий порт 587 у Contabo VPS — `[не проверено]`, задача `devops` (раздел 7) — одна проверка соединения при выкладке, после «ок» владельца.

## 2. Критерии приёмки

Обозначения: `E` — корректный новый e-mail; `E2` — адрес существующей активной записи; `EB` — адрес заблокированной записи; `T` — токен из письма; `K` — действующий ключ записи; `M` — `MemoryMailer` в `app.state.mailer` (письма складываются в список); «очередь» — строки `api_key_requests` со `status = 'pending'`; `run_outbox()` — один проход отправщика. Лимиты в тестах уменьшаются через `Settings`/`app.state`. Все ответы ручек этапа — `Cache-Control: no-store`.

| № | Критерий (вход → результат) | Кто проверяет | Слой теста |
|---|---|---|---|
| AC-1 | `alembic upgrade head` → у `api_key_requests` новые столбцы и ограничения раздела 6, `token_hash` и `expires_at` допускают `NULL`; `downgrade -1` → схема как после `0018`, ожидающие строки (`token_hash IS NULL`) удалены; `upgrade` снова проходит | qa-engineer | integration |
| AC-2 | `POST /api/v2/keys/requests {"email": E}` → `202 {"status": "accepted", "mail_delayed": false}`; в очереди одна строка `purpose = 'key'`, `token_hash IS NULL`; после `run_outbox()` в `M` одно письмо на `E` со ссылкой `{PUBLIC_BASE_URL}/keys/confirm#token=…`, у строки `status = 'sent'`, `token_hash` = SHA-256 токена из письма, `expires_at = sent_at + KEY_TOKEN_TTL_MINUTES`; сам токен не найден ни в одной таблице | qa-engineer | api |
| AC-3 | `POST /keys/requests` с `E`, `E2`, `EB`, `E` сверх `KEY_SIGNUP_PER_EMAIL_PER_DAY` и с непустым полем-ловушкой `website` → у всех один и тот же код, тело и набор заголовков (`202`, сравнение побайтно, кроме `X-Request-Id`); письмо после `run_outbox()` — только для `E` и `E2` в пределах лимита | qa-engineer | api |
| AC-4 | `email`: без `@`, два `@`, пробел, `CR`/`LF`, длиннее 254, домен без точки, не-ASCII, домен из `KEY_EMAIL_BLOCKED_DOMAINS`, лишнее поле в теле, тело не объект → `422 validation_error`; без `Content-Type: application/json` → `415 unsupported_media_type`; тело длиннее 2 КиБ → `413` | qa-engineer | api |
| AC-5 | `KEY_SIGNUP_PER_IP_PER_HOUR`+1-й запрос с одного IP за час (на разные адреса) → `429 rate_limited` с `Retry-After`; с другого IP — `202`; IPv6 считается по `/64` | qa-engineer | api |
| AC-6 | при `KEY_SIGNUP_GLOBAL_PER_DAY` строк очереди за последние 24 ч → любой следующий запрос (любой адрес) → `503 mail_unavailable` с `Retry-After`; после перезапуска приложения предел считается по базе, а не с нуля | qa-engineer | api |
| AC-7 | `POST /api/v2/keys/confirm {"token": T}` → `201 {"key", "key_info", "account"}`, ключ проходит как `X-Api-Key` (расширенный лимит); учётная запись создана с `verified_at`; повтор с `T` → `400 invalid_token`; `T` после `expires_at` → `400 invalid_token`; случайная строка нужной длины → `400 invalid_token`; `token` не 43 символа base64url → `422` | qa-engineer | api |
| AC-8 | два одновременных `confirm` одним `T` → ровно один `201`, второй `400 invalid_token`; ключей выпущено ровно 1 | qa-engineer | integration |
| AC-9 | `confirm` для `E2` при 2 действующих ключах → `409 key_limit_reached`, токен не израсходован; с `"revoke_existing": true` → `201`, прежние ключи `revoke_reason = 'replaced'` и сразу получают `401 api_key_revoked` | qa-engineer | api |
| AC-10 | `confirm` токеном, выданным до блокировки записи → `403 account_blocked`, ключ не выдан | qa-engineer | api |
| AC-11 | `MailerError` при отправке (сбой SMTP) → строка остаётся `pending`, `attempts + 1`, `next_attempt_at` по графику `KEY_MAIL_RETRY_MINUTES` (1, 5, 15); после `KEY_MAIL_MAX_ATTEMPTS` (4) — `status = 'failed'`, письма нет; токен не создан, пока письмо не ушло | qa-engineer | integration |
| AC-12 | после неудачной отправки за последние `KEY_MAIL_DEGRADED_MINUTES` (15) без успешной после неё → `POST /keys/requests` отвечает `202 {"status": "accepted", "mail_delayed": true}` для любого адреса | qa-engineer | api |
| AC-13 | перезапуск приложения при строках `pending` → отправщик (задача планировщика) отправляет их без повторного запроса; два прохода `run_outbox()` одновременно → каждое письмо отправлено один раз (`FOR UPDATE SKIP LOCKED`) | qa-engineer | integration |
| AC-14 | `SMTP_HOST` не задан или `KEY_SIGNUP_ENABLED=false` → `POST /keys/requests`, `/keys/confirm`, `/account/deletion-requests`, `/account/deletion-confirm` → `404`; `/api/v2/account*` с ключом работают; `API_KEYS_ENABLED=false` → все ручки этапа `404` | qa-engineer | api |
| AC-15 | `GET /api/v2/account` с `K` → `200`: `email`, `status`, `created_at`, `keys` (без ключей и хэшей), `usage`, `limits`; без ключа → `401 api_key_required`; отозванным → `401 api_key_revoked`; заблокированной записи → `403 account_blocked` | qa-engineer | api |
| AC-16 | `POST /api/v2/account/keys` с `K` → `201` новый ключ; третий действующий → `409 key_limit_reached`; `DELETE /api/v2/account/keys/{id}` своего ключа → `204`, `revoke_reason = 'owner'`, этот ключ сразу `401 api_key_revoked`; чужой или несуществующий `id` → `404 not_found`; `id` вне `1..2^63-1` → `422` | qa-engineer | api |
| AC-17 | `DELETE /api/v2/account` с `K` → `204`; в базе нет записи, её ключей, счётчиков и строк `api_key_requests` с её e-mail; следующий запрос с `K` → `401 invalid_api_key` | qa-engineer | api |
| AC-18 | `POST /api/v2/account/deletion-requests {"email": E2}` → тот же ответ, что AC-3, для известного и неизвестного адреса; письмо — только известному, со ссылкой `{PUBLIC_BASE_URL}/keys/delete#token=…`; `POST /api/v2/account/deletion-confirm {"token": T}` → `204`, данные удалены как в AC-17; токен ключа (`purpose = 'key'`) в `deletion-confirm` → `400 invalid_token` и наоборот | qa-engineer | api |
| AC-19 | `POST` ручек этапа и `DELETE /account*` с заголовком `Origin` чужого сайта → ответ без `Access-Control-Allow-Origin`; предзапрос `OPTIONS` с `Access-Control-Request-Method: POST` → без разрешения `POST` | qa-engineer | api |
| AC-20 | весь поток AC-2, AC-7, AC-17 с захватом логов (`caplog`, журнал доступа) → в логах нет ни e-mail, ни токена, ни ключа, ни `SMTP_PASSWORD`; при `MailerError` с текстом, содержащим адрес, в лог попадает только класс ошибки и код SMTP | qa-engineer | unit |
| AC-21 | `SmtpMailer` с подменённым `smtplib.SMTP` → письмо `text/plain; charset=utf-8`, `From` = `MAIL_FROM` (с `MAIL_FROM_NAME`), `To` = адрес, тема из шаблона, ссылка с хостом из `PUBLIC_BASE_URL` (не из `Host` запроса), `STARTTLS` вызван при `SMTP_STARTTLS=true`, таймаут `SMTP_TIMEOUT_SECONDS`; сеть не используется | qa-engineer | unit |
| AC-22 | `Settings`: `KEY_SIGNUP_ENABLED=true` без `SMTP_HOST`, `MAIL_FROM` или `PUBLIC_BASE_URL` → ошибка запуска с именем недостающей переменной; `SMTP_PASSWORD` — `SecretStr`, в `repr(settings)` и логе запуска не виден | qa-engineer | unit |
| AC-23 | запрос с заголовком `Host: evil.example` → ссылка в письме всё равно на `PUBLIC_BASE_URL` | qa-engineer | api |
| AC-24 | `cleanup_api_keys` → удалены строки `sent`/`failed`, у которых `expires_at` (или `created_at` для `failed`) старше 24 ч, и `pending` старше 24 ч; свежие не тронуты | qa-engineer | integration |
| AC-25 | `GET /developers` → раздел «API keys» с текстом о данных (что храним, сколько, кто отправляет письма, как удалить — ссылки на `/keys/manage` и `/keys/delete`), без личного адреса владельца; `/llms.txt` упоминает, как получить ключ | qa-engineer | api |
| AC-26 | `uv run python -m app.cli openapi --output docs/openapi.json` → в схеме ручки раздела 4, новые коды ошибок; ruff и `pytest -q -n 12` зелёные | reviewer | — |
| AC-27 | admin `GET /api/v2/admin/key-mail` → счётчики очереди `{"pending", "sent_24h", "failed_24h", "global_limit", "last_error_at", "last_success_at"}` без адресов; без `X-Admin-Token` → `401` | qa-engineer | api |
| AC-28 | экран `/keys`: ввод e-mail → «проверьте почту» (одинаково для любого адреса); при `mail_delayed: true` — предупреждение о задержке; `422` — текст у поля; `429` и `503` — сообщение с временем повтора; кнопка заблокирована на время запроса; поле-ловушка скрыто от людей и вспомогательных технологий | frontend-qa | компонентный |
| AC-29 | экран `/keys/confirm#token=T` → токен убран из адресной строки сразу при загрузке (`history.replaceState`), запрос уходит только по кнопке; ключ показан один раз с кнопкой копирования и предупреждением; `409` → предложение заменить ключи (повтор с `revoke_existing`); `400` → «ссылка устарела» со ссылкой на `/keys` | frontend-qa | компонентный |
| AC-30 | экран `/keys/manage`, ввод ключа в поле → ключ не пишется в `localStorage`/`sessionStorage`/cookie и не попадает в URL; список ключей, «новый ключ», «отозвать», «удалить учётную запись» с подтверждением; `401`/`403` — понятный текст | frontend-qa | компонентный |
| AC-31 | экран `/keys/delete`: e-mail → «если адрес зарегистрирован, письмо придёт»; `/keys/delete#token=T` → подтверждение кнопкой → «данные удалены» | frontend-qa | компонентный |
| AC-32 | сквозной сценарий в браузере (API замокан на уровне сети браузера): `/keys` → `/keys/confirm#token=…` → ключ → `/keys/manage` → отзыв; 360 px и тёмная тема без горизонтальной прокрутки | frontend-qa | e2e |

## 3. Решения

1. **Ссылка подтверждения, а не ключ в письме** (ADR-1). Токен — 32 случайных байта, base64url (43 символа), в базе — SHA-256, одноразовый, живёт `KEY_TOKEN_TTL_MINUTES` = 60 от момента отправки письма. Ссылка ведёт на экран интерфейса, токен — во фрагменте (`#token=`), расходуется только `POST` по кнопке: почтовые сканеры ссылок его не тратят, в журналы сервера и `Referer` он не попадает.
2. **Очередь писем в `api_key_requests`, отправка в фоне** (ADR-2). Ручка только записывает заявку и отвечает `202`; отправщик создаёт токен в момент отправки и пишет его хэш. Время ответа не зависит от адреса и от SMTP, сырой токен нигде не хранится, заявки переживают перезапуск.
3. **SMTP — стандартный `smtplib` в `asyncio.to_thread`** (ADR-3), без новой зависимости; таймаут `SMTP_TIMEOUT_SECONDS` = 15; повторы — расписанием очереди (1, 5, 15 мин, всего 4 попытки), не внутри запроса.
4. **Одинаковый ответ для любого корректного адреса**: `202 {"status": "accepted", "mail_delayed": <bool>}`. Причины не отправлять (запись заблокирована, лимит адреса, ловушка) не видны снаружи. Различаются только ответы, не зависящие от адреса: `422` (формат), `429` (лимит IP), `503` (глобальный предел). `mail_delayed` — общее состояние почты, не адреса.
5. **Лимиты** (ADR-4): с IP — `KEY_SIGNUP_PER_IP_PER_HOUR` = 5 (в памяти, IPv6 по `/64`, IP не храним); на адрес — `KEY_SIGNUP_PER_EMAIL_PER_DAY` = 3 письма за 24 ч (по базе, ключевые и на удаление вместе); глобально — `KEY_SIGNUP_GLOBAL_PER_DAY` = 100 строк очереди за 24 ч (по базе) — с запасом ниже суточного предела Gmail; ключей на запись — 2 (как в 20A). Каждый запрос `/keys/requests` списывается и с обычного лимита IP.
6. **Без капчи; поле-ловушка** (ADR-5): необязательное поле `website` в теле; непустое → `202` без письма. Против скриптов по API не помогает, их сдерживают лимиты п. 5.
7. **Удаление данных**: с ключом — `DELETE /api/v2/account`; без ключа — по ссылке из письма (`purpose = 'delete'`); по письму владельцу — admin `DELETE /api/v2/admin/accounts/{id}` (есть с 20A). Удаление каскадно убирает ключи, счётчики и заявки с этим адресом.
8. **Хранение**: e-mail — пока существует учётная запись; заявки — до 24 ч после истечения или неудачи; счётчики использования — 90 дней (как в 20A); IP при регистрации не хранится; неактивные записи — вопрос В3. Письма в «Отправленных» Gmail владельца — вне нашей базы, вопрос В2.
9. **Логи**: e-mail, токены, ключи, тела писем, `SMTP_PASSWORD` не логируются. Отправщик пишет `request_id` заявки, `purpose`, исход и код SMTP; исключения SMTP — только класс и код (текст ошибок `smtplib` может содержать адрес).
10. **Выключатели**: ручки выдачи работают, только если `API_KEYS_ENABLED=true`, `KEY_SIGNUP_ENABLED=true` и задан `SMTP_HOST`; иначе `404` (AC-14). `KEY_SIGNUP_ENABLED` позволяет быстро закрыть регистрацию при злоупотреблении, не трогая ключи.
11. **Всё о почте — в `Settings`**: `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD` (`SecretStr`), `SMTP_STARTTLS` (true; при `SMTP_PORT=465` — `SMTP_SSL=true`), `SMTP_TIMEOUT_SECONDS`, `MAIL_FROM`, `MAIL_FROM_NAME` («Sol Index»), `MAIL_REPLY_TO` (необязателен), `MAIL_PROVIDER_NAME` (для текста о данных: «Google (Gmail)»). В `.env.example` и спецификации — только плейсхолдеры.
12. **Письма** — простой текст на английском, без отслеживания и картинок; в письме: ссылка, срок действия, «если вы не запрашивали — проигнорируйте», пометка о неофициальном проекте, ссылка на удаление данных. Тексты пишет `tech-writer`, вставляет `backend-dev` (`app/services/mail_templates.py`).
13. **Admin** — ручки выдачи, отзыва, блокировки, удаления остаются как в 20A; добавляется только сводка очереди писем без адресов (AC-27).

## 4. Текст для `API.md` (если меняется контракт)

Ломает ли клиентов: нет. Добавляются новые ручки (существуют только при включённых ключах и почте) и коды ошибок; действующие ручки и ответы не меняются. В `LEGACY-API.md` изменений нет.

```diff
@@ «Общие правила v2», пункт «Ключ API (этап 20)»
-... Ключ — строка `si_` и 32 латинские буквы и цифры; выдаёт его администратор (самостоятельная выдача по e-mail — позже). ...
+... Ключ — строка `si_` и 32 латинские буквы и цифры; получить его можно по e-mail (раздел «Ключи API») или у администратора. ...

@@ «Общие правила v2», таблица ошибок ключа
   | 403 | `account_blocked` | учётная запись ключа заблокирована |
-  | 409 | `key_limit_reached` | у учётной записи уже `API_KEY_MAX_PER_ACCOUNT` (2) действующих ключей (admin-выдача) |
+  | 409 | `key_limit_reached` | у учётной записи уже `API_KEY_MAX_PER_ACCOUNT` (2) действующих ключей |
+  | 401 | `api_key_required` | ручка `/api/v2/account*` без ключа |
+  | 400 | `invalid_token` | токен из письма не найден, использован, истёк или выдан для другого действия |
+  | 413 | `payload_too_large` | тело `POST` ручек ключей больше 2 КиБ |
+  | 415 | `unsupported_media_type` | `POST` ручек ключей без `Content-Type: application/json` |
+  | 503 | `mail_unavailable` | исчерпан суточный предел писем сервера; `Retry-After` |

+## Ключи API (этап 20B)
+
+Ручки есть, только если `API_KEYS_ENABLED=true`; ручки с письмами (`/keys/*`, `/account/deletion-*`) — ещё и при включённой регистрации (`KEY_SIGNUP_ENABLED=true`, настроена почта), иначе `404`. Все ответы — `Cache-Control: no-store`; запросы считаются в обычном лимите частоты. Тела `POST` — JSON (`Content-Type: application/json`), не больше 2 КиБ, лишние поля → `422`. CORS для этих ручек не разрешён: они рассчитаны на интерфейс Sol Index и прямые вызовы (curl, скрипты).
+
+#### `POST /api/v2/keys/requests`
+Тело `{"email": "user@example.org"}`. `email` — до 254 символов, ровно один `@`, без пробелов и управляющих символов, только ASCII, домен с точкой; хранится в нижнем регистре. Ставит в очередь письмо со ссылкой `https://solindex.net/keys/confirm#token=…` (одноразовая, действует 60 минут с отправки письма).
+```json
+{"status": "accepted", "mail_delayed": false}
+```
+Ответ одинаков для любого корректного адреса: письмо не отправляется на адрес заблокированной записи и сверх 3 писем на адрес за 24 часа, но ответ тот же. `mail_delayed: true` — у сервера сейчас сбой отправки, письмо будет отправлено повторно (до 4 попыток за ~20 минут). `202` · `413` · `415` · `422 validation_error` · `429 rate_limited` (больше 5 запросов с IP за час) · `503 mail_unavailable` (суточный предел писем сервера).
+
+#### `POST /api/v2/keys/confirm`
+Тело `{"token": "TOKEN_FROM_LINK", "revoke_existing": false}`; `token` — 43 символа base64url. Создаёт учётную запись (если её нет) и выдаёт ключ:
+```json
+{
+  "key": "si_EXAMPLEEXAMPLEEXAMPLEEXAMPLE0000",
+  "key_info": {"id": 41, "prefix": "si_EXAMPLEE", "created_at": "2026-12-01T10:00:00Z", "last_used_at": null, "revoked_at": null},
+  "account": {"email": "user@example.org", "created_at": "2026-12-01T10:00:00Z"}
+}
+```
+Ключ показывается только здесь. `revoke_existing: true` при лимите ключей отзывает действующие ключи и выдаёт новый. `201` · `400 invalid_token` · `403 account_blocked` · `409 key_limit_reached` (токен не расходуется) · `413` · `415` · `422`.
+
+#### `GET /api/v2/account`
+Ключ в `X-Api-Key` (или `api_key`). Ответ:
+```json
+{
+  "email": "user@example.org",
+  "status": "active",
+  "created_at": "2026-12-01T10:00:00Z",
+  "keys": [{"id": 41, "prefix": "si_EXAMPLEE", "created_at": "2026-12-01T10:00:00Z", "last_used_at": "2026-12-03T08:12:00Z", "revoked_at": null}],
+  "usage": {"today": 312, "last_30_days": 10450},
+  "limits": {"per_minute": 120, "per_hour": 10000, "concurrent": 8}
+}
+```
+`keys` — действующие и отозванные за 30 дней, новые первыми; `usage` — по UTC-суткам, с задержкой до минуты. `401 api_key_required | invalid_api_key | api_key_revoked` · `403 account_blocked`.
+
+#### `POST /api/v2/account/keys`
+Без тела. `201` — объект как у `/keys/confirm` без `account`. `409 key_limit_reached` · ошибки ключа как у `GET /account`.
+
+#### `DELETE /api/v2/account/keys/{id}`
+Отзывает ключ своей учётной записи (можно и тот, которым сделан запрос). `204` · `404 not_found` (нет такого ключа у этой записи) · `422` (`id` вне 1…2⁶³−1).
+
+#### `DELETE /api/v2/account`
+Удаляет учётную запись, её ключи, счётчики и заявки с её адресом безвозвратно. `204`.
+
+#### `POST /api/v2/account/deletion-requests`
+Тело `{"email": "user@example.org"}` — для тех, у кого нет ключа. Ответ, лимиты и ошибки — как у `/keys/requests`; письмо со ссылкой `https://solindex.net/keys/delete#token=…` уходит, только если запись с таким адресом есть.
+
+#### `POST /api/v2/account/deletion-confirm`
+Тело `{"token": "TOKEN_FROM_LINK"}`. Удаляет данные, как `DELETE /api/v2/account`. `204` · `400 invalid_token` · `413` · `415` · `422`.

@@ «Admin», после «Учётные записи и ключи (этап 20)»
+- `GET /api/v2/admin/key-mail` (этап 20B) → `{"pending": 0, "sent_24h": 17, "failed_24h": 0, "global_limit": 100, "last_success_at": "...", "last_error_at": null}` — состояние очереди писем, без адресов.

@@ «/developers»
+Раздел «API keys» (этап 20B): зачем ключ, как получить (`/keys` в Sol Index или `POST /api/v2/keys/requests`), заголовок против параметра, лимиты, ошибки, текст о данных, ссылки на `/keys/manage` и `/keys/delete`. Шаг «ключ не нужен» в «Migrating» → «ключ api.nasa.gov можно не убирать — он игнорируется; для расширенного лимита получите наш».
```

Пример ссылки — с хостом из `PUBLIC_BASE_URL`; `solindex.net` в тексте контракта — значение продакшена.

### 4.1. Текст о данных (для `/developers` и экранов ключа; `tech-writer` переводит на английский)

«Для ключа мы храним ваш e-mail, даты создания и подтверждения, ключи (только в виде хэша) и число запросов по дням. E-mail нужен, чтобы выдать ключ и связаться при злоупотреблении; мы не рассылаем по нему ничего, кроме писем, которые вы запросили, и никому его не передаём. Письма отправляются через {MAIL_PROVIDER_NAME}. Неподтверждённые заявки удаляются в течение суток, счётчики запросов — через 90 дней, учётная запись — {срок по В3}. Удалить данные можно в любой момент: в разделе „My keys“ с вашим ключом, по ссылке из письма на странице „Delete my data“ или ответом на любое наше письмо.» Личный адрес владельца в тексте не пишется: «ответом на письмо» достаточно.

## 5. ADR

**ADR-1. Подтверждение владения адресом.** (a) ключ сразу в письме — один шаг, не нужен экран подтверждения; но ключ навсегда лежит в чужом ящике и в «Отправленных» личного Gmail владельца (у владельца копии всех ключей), а письмо, отправленное на чужой адрес, дарит ключ получателю без его согласия. (b) ссылка с токеном, ключ по `GET` — сканеры ссылок (Outlook Safe Links и т. п.) расходуют токен раньше человека. (c) ссылка на экран с токеном во фрагменте, ключ по `POST` с кнопки — ключ нигде, кроме браузера пользователя; в «Отправленных» — только просроченный токен. (d) 6-значный код — ввод руками, нужен лимит перебора. **Рекомендую (c)** (как в ADR-5 20A). Срок ссылки 60 мин: письма Gmail иногда задерживаются серыми списками получателя, 30 мин мало.

**ADR-2. Когда отправлять письмо.** (a) в запросе, с ожиданием SMTP — сбой SMTP виден сразу (`503`), но время ответа выдаёт, ушло ли письмо (то есть заблокирован ли адрес и исчерпан ли его лимит), и запрос висит до 15 с; (b) `asyncio`-задача в памяти — ответ быстрый, но заявки теряются при перезапуске, повторы не переживают перезапуск; (c) очередь в `api_key_requests` + отправщик (событие сразу после записи и задача планировщика раз в минуту), токен создаётся при отправке — одинаковое время ответа, повторы переживают перезапуск, сырой токен не хранится, без новых таблиц и сервисов. **Рекомендую (c).** Цена — пользователь не узнаёт о сбое конкретного письма; общий сбой показывается флагом `mail_delayed`.

**ADR-3. Библиотека SMTP.** (a) `aiosmtplib` — нативно async, но новая зависимость; (b) `smtplib` + `asyncio.to_thread` — стандартная библиотека, писем единицы в час, поток не мешает; тест — подменой `smtplib.SMTP`/`SMTP_SSL`. **Рекомендую (b)** (правило «без новых зависимостей, если можно»). Протокол `Mailer` скрывает выбор: заменить на (a) — одна правка `SmtpMailer`.

**ADR-4. Числа лимитов.** Суточный предел Gmail `[не проверено]` — сотни писем; превышение блокирует отправку аккаунта владельца, в том числе личную. (a) глобальный предел 400 — ближе к пределу, риск задеть личную почту; (b) 100 в сутки — с четырёхкратным запасом, для неофициального API этого хватает с большим запасом (ожидаем единицы регистраций в день — оценка); (c) без глобального предела — недопустимо. **Рекомендую (b)**, в `Settings`. На адрес — 3 письма в сутки (защищает чужой ящик от бомбардировки: худший случай — 3 письма в сутки), с IP — 5 в час.

**ADR-5. Капча без сторонних сервисов.** (a) сторонняя (Turnstile, hCaptcha) — запрещено условием (третьи лица, скрипты); (b) своя proof-of-work в браузере — задерживает и людей, а скрипты считают её на сервере, плюс код и тесты; (c) поле-ловушка — бесплатно, отсекает простые боты форм; (d) ничего. **Рекомендую (c)**, вместе с лимитами ADR-4. Вернуться к (b), если сводка AC-27 покажет упор в глобальный предел.

**ADR-6. Удаление без ключа.** (a) только письмом владельцу — ручная работа и личный адрес в тексте; (b) ссылка из письма на тот же механизм заявок (`purpose = 'delete'`) — самообслуживание, 2 ручки и экран; ответ одинаков для любого адреса. **Рекомендую (b)**, плюс (a) как запасной путь «ответьте на письмо».

## 6. Модель данных, миграция, поток

### Миграция `0019_key_mail_queue` (меняет `api_key_requests` из `0016`)

Объём: не больше `KEY_SIGNUP_GLOBAL_PER_DAY` (100) строк в сутки, живут ≤ 2 суток — до ~200 строк.

- `purpose text not null default 'key'`, `check (purpose in ('key','delete'))`;
- `status text not null default 'pending'`, `check (status in ('pending','sent','failed'))`;
- `attempts smallint not null default 0`, `next_attempt_at timestamptz not null default now()`, `sent_at timestamptz null`;
- `token_hash` и `expires_at` → `NULL` допустим; `check (status <> 'sent' or (token_hash is not null and expires_at is not null and sent_at is not null))`; `unique (token_hash)` остаётся (несколько `NULL` допустимы);
- индекс `ix_api_key_requests_pending (next_attempt_at) where status = 'pending'` — выборка отправщика; подсчёт за 24 ч на адрес — существующий `(email, created_at)`; глобальный за 24 ч — последовательный просмотр (≤ 200 строк).
- `downgrade`: удалить строки с `token_hash IS NULL or expires_at IS NULL` (неотправленные заявки — потеря допустима, срок жизни минуты), снять индекс и `check`, вернуть `NOT NULL`, удалить столбцы. Данных других таблиц не трогает.

### Поток выдачи

1. `POST /keys/requests` (роутер `app/api/v2/keys.py`) → проверка `Content-Type`, размера, схемы → лимит IP (`signup_limiter` в `app.state`) → `KeySignupService.request(email, purpose, honeypot)`: глобальный счётчик за 24 ч (`503`) → если ловушка, лимит адреса исчерпан, запись заблокирована (`purpose='key'`) или записи нет (`purpose='delete'`) — ничего не пишет → иначе `INSERT` заявки `pending`, `commit`, `outbox.wake()` → `202` с `mail_delayed = outbox.degraded`.
2. Отправщик `KeyMailOutbox.run_once()` (`app/services/key_mail.py`): в транзакции `SELECT ... WHERE status='pending' AND next_attempt_at <= now() ORDER BY id LIMIT 10 FOR UPDATE SKIP LOCKED` → для каждой: токен `secrets.token_urlsafe(32)`, письмо из шаблона, `await mailer.send(msg)` → успех: `token_hash`, `sent_at = now()`, `expires_at = now() + TTL`, `status='sent'` → `MailerError`: `attempts+1`, `next_attempt_at` по графику или `status='failed'`; состояние `degraded` в памяти. Запуск: по `wake()` (одна фоновая задача, не больше одной одновременно) и задача APScheduler `send_key_mail` раз в 60 с (подбирает повторы и хвосты после перезапуска). Идемпотентность: строка блокируется на время отправки; сбой процесса между отправкой и `commit` даёт одно лишнее письмо с недействительным токеном (хэш не записан) — пользователь повторит запрос; допускаю.
3. `POST /keys/confirm` — одна транзакция: `SELECT ... FOR UPDATE` по `token_hash` и `purpose='key'`, проверка `status='sent'`, `used_at IS NULL`, `expires_at > now()` → `ApiKeyService.issue_from_signup(session, email, revoke_existing)` (общая логика с `issue_key()` 20A: запись `ON CONFLICT (email)`, `verified_at`, блокировка, лимит ключей) → `used_at = now()` только при успехе.
4. `POST /account/deletion-confirm` — так же с `purpose='delete'` → `ApiKeyService.delete_account()` (есть с 20A) + удаление заявок с этим адресом.
5. Кабинет `/api/v2/account*` (`app/api/v2/account.py`) — личность из `scope["mars.rate_identity"]` (20A), без ключа → `401 api_key_required`.
6. Очистка — существующая `cleanup_api_keys` расширяется по AC-24.

## 7. Задания ролям и общие имена

**Общие имена:**
- модули: `app/services/mail.py` (`Mailer` протокол `async send(message: EmailMessage) -> None`, `MailerError(smtp_code: int | None)`, `SmtpMailer`, `MemoryMailer` с полем `sent: list[EmailMessage]` и `fail_next: int`), `app/services/mail_templates.py` (`key_link_message(...)`, `delete_link_message(...)`), `app/services/key_mail.py` (`KeyMailOutbox`: `run_once()`, `wake()`, `degraded`, `status()`; задача `send_key_mail`), `app/services/key_signup.py` (`KeySignupService`: `request()`, `confirm_key()`, `confirm_delete()`); роутеры `app/api/v2/keys.py`, `app/api/v2/account.py`, admin `GET /admin/key-mail` — в `app/api/v2/admin.py`; репозиторий — расширение `app/repositories/api_keys.py`;
- миграция `migrations/versions/20261130_0019_key_mail_queue.py` (дата — по дню создания), ORM `ApiKeyRequest` — новые поля;
- `app.state`: `mailer`, `key_mail_outbox`, `signup_limiter`; ошибки `app/core/errors.py`: `ApiKeyRequired`, `InvalidToken`, `MailUnavailable`, `UnsupportedMediaType`, `PayloadTooLarge`;
- настройки: `KEY_SIGNUP_ENABLED` (false), `KEY_SIGNUP_PER_IP_PER_HOUR` (5), `KEY_SIGNUP_PER_EMAIL_PER_DAY` (3), `KEY_SIGNUP_GLOBAL_PER_DAY` (100), `KEY_TOKEN_TTL_MINUTES` (60), `KEY_MAIL_MAX_ATTEMPTS` (4), `KEY_MAIL_RETRY_MINUTES` ("1,5,15"), `KEY_MAIL_DEGRADED_MINUTES` (15), `KEY_EMAIL_BLOCKED_DOMAINS` (пусто), `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD` (`SecretStr`), `SMTP_STARTTLS` (true), `SMTP_SSL` (false), `SMTP_TIMEOUT_SECONDS` (15), `MAIL_FROM`, `MAIL_FROM_NAME` ("Sol Index"), `MAIL_REPLY_TO`, `MAIL_PROVIDER_NAME`; `PUBLIC_BASE_URL` обязателен при `KEY_SIGNUP_ENABLED`;
- маршруты интерфейса: `/keys`, `/keys/confirm`, `/keys/manage`, `/keys/delete`.

- `backend-dev`: миграция `0019` и ORM; `Mailer`/`SmtpMailer`/`MemoryMailer` (`EmailMessage`, проверка заголовков на `CR`/`LF`); `KeyMailOutbox` и задача планировщика; `KeySignupService`; роутеры keys/account, admin `key-mail`; проверка `Content-Type`/размера тела для `POST`; лимит IP регистрации; расширение `cleanup_api_keys`; запрет логирования e-mail/токенов (фильтр в `app/core/logging.py` для журнала доступа не нужен — тела не логируются, но проверить исключения); `Settings` и `.env.example` с плейсхолдерами (`SMTP_USERNAME=sender@example.com`, `SMTP_PASSWORD=change-me`); тексты писем, `/developers`, `/llms.txt` — от `tech-writer`; `docs/openapi.json`. Тест-переключатель: в тестах `app.state.mailer = MemoryMailer()`, `SmtpMailer` в тестах не создаётся.
- `qa-engineer`: AC-1…AC-27 по таблице «AC → тест»; граничные случаи: адрес в разном регистре (`User@Example.org` и `user@example.org` — один адрес и один лимит), пробелы по краям (`422`, не обрезаем), повторный `confirm` после `409` с `revoke_existing`, удаление записи между отправкой письма и `confirm` (`confirm` создаёт новую запись — допустимо, проверить), блокировка между `request` и отправкой (письмо не уходит — проверка статуса в отправщике), `MemoryMailer.fail_next`, часы — через подмену `now` (без `sleep`). Сеть не нужна.
- `designer`: макеты в `design/` для четырёх экранов на данных v2 — `/keys` (форма, «проверьте почту», задержка почты, `422`/`429`/`503`), `/keys/confirm` (кнопка «Show my key», ключ один раз с копированием и предупреждением «сохраните сейчас», `409` с заменой ключей, просроченная ссылка), `/keys/manage` (поле ключа, список ключей, новый ключ, отзыв, удаление с подтверждением), `/keys/delete` (форма и подтверждение); текст о данных под формой; где ссылка на `/keys` — шапка/подвал и `/developers`; 360 px и тёмная тема.
- `frontend-dev`: экраны по макетам в `frontend/src/pages/`, маршруты в `frontend/src/router.tsx`; токен читается из `location.hash` и сразу убирается (`history.replaceState`); ключ живёт только в состоянии компонента; запросы — через общий клиент `frontend/src/api/`; поле-ловушка скрыто (`aria-hidden`, вне фокуса).
- `frontend-qa`: AC-28…AC-32; компонентные тесты в `frontend/tests/`, сквозной — в `frontend/e2e/` с моками API.
- `tech-writer`: английские тексты: раздел «API keys» на `/developers`, текст о данных (раздел 4.1), два письма (тема и тело), строки для `/llms.txt`, README («как получить ключ»); отдать в отчёте — вставляет `backend-dev`.
- `security-reviewer`: перечисление адресов (тело, заголовки, время ответа, `mail_delayed`), бомбардировка чужого ящика, токены (энтропия, одноразовость, гонки, фрагмент, сканеры), подмена `Host` в ссылке, внедрение заголовков в письмо, CSRF/CORS на `POST`/`DELETE`, утечки в логах и исключениях SMTP, хранение `SMTP_PASSWORD` на VPS, исчерпание квоты Gmail как DoS почты владельца.
- `devops`: `SMTP_*` и `MAIL_*` в `deploy/vps` (секрет `SMTP_PASSWORD` — как `ADMIN_TOKEN`, вне git, права только для приложения); проверить, что Caddy закрывает `/api/v2/admin/key-mail` тем же правилом, что остальной admin; миграция `0019` при выкладке; одна проверка исходящего соединения с VPS до `smtp.gmail.com:587` (после «ок» владельца, раздел 9); **включение** `API_KEYS_ENABLED=true` и `KEY_SIGNUP_ENABLED=true` — отдельный шаг после «ок» владельца, с проверкой: письмо на адрес владельца → ключ → `X-RateLimit-Limit: 120`.
- `data-engineer`: не нужен.

## 8. Проверка и бюджет

- Команды: `uv run ruff check .`, `uv run pytest -q -n 12`, `/migration-check` (`0019` вверх-вниз-вверх), `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` в `frontend/`, `uv run python -m app.cli openapi --output docs/openapi.json`.
- Живая проверка (после выкладки и «ок» на включение): письмо на адрес владельца, подтверждение, ключ, кабинет, отзыв, удаление; `admin/key-mail`; в журналах приложения и Caddy нет адреса, токена и ключа. `/ui-check`: четыре экрана, состояния ошибок, 360 px, тёмная тема.
- Ревью: `reviewer`; `security-reviewer` обязателен (новая публичная поверхность, персональные данные, секреты).
- Ожидаемый расход: architect — этот черновик ≈ 2%; разработка — средний этап (меньше 20A: модель и лимиты уже есть), основной объём — тесты очереди и фронтенд.

## 9. Внешние запросы (для «ок» владельца)

Для проектирования и тестов — нет. При выкладке: `devops` — одно соединение VPS → `smtp.gmail.com:587` (проверка порта); владелец — одно-два тестовых письма на свой адрес после включения. Пароль приложения Gmail создаёт и кладёт на VPS владелец или `devops` по его указанию.

## 10. Вопросы владельцу

**Решение владельца 2026-11-24:** В1–В5 приняты по рекомендациям; контракт раздела 4 внесён в `API.md` (ручки — в «Эндпоинты», подраздел «Ключи API (этап 20B)»), срок хранения из В3 — там же.

**Правки по ревью и проверке безопасности (2026-11-24):** адрес — только addr-spec (плюс `parseaddr(e)[1] == e`), один получатель у письма; каждый корректный запрос пишет строку (`pending` или `skipped` — ловушка, лимит адреса, заблокированная запись, неизвестный адрес при удалении), глобальный предел считает все строки — уточнение AC-6 (запрос без письма учитывается в глобальном пределе; ответ тот же); статус `dropped` — запись заблокирована или адрес удалён между запросом и отправкой, в `failed_24h` не входит; очередь коммитит каждую заявку отдельно, исключение не-`MailerError` → `failed`; лимит на адрес у gmail.com/googlemail.com — без точек и `+tag`; настройка `KEY_MAIL_JOB_ENABLED`; неактивность записи (В3) — по ключам (`API_ACCOUNT_INACTIVE_DAYS=730`). Пограничные случаи из тестов: `revoke_existing: true` при незаполненном лимите ничего не отзывает; `usage.last_30_days` включает сегодня; хэш токена — SHA-256 строки токена.

- **В1. Глобальный предел писем — 100 в сутки.** Защищает личный Gmail от блокировки за превышение (точный предел Gmail не проверен). Рекомендую: 100; поднимать, только если сводка `admin/key-mail` покажет упор.
- **В2. Личный адрес виден получателям и копии писем остаются в «Отправленных».** Каждый, кто запросит ключ, увидит адрес владельца в `From`; адреса пользователей копятся в «Отправленных» вне нашей базы. Рекомендую: принять на время, отправитель с именем «Sol Index»; удалять «Отправленные» с темой письма раз в месяц вручную или фильтром; при переезде на `noreply@solindex.net` — сменить переменные.
- **В3. Срок хранения учётных записей.** Рекомендую: удалять автоматически записи без единого запроса за 24 месяца (одна задача в `cleanup_api_keys`); остальное — до удаления пользователем.
- **В4. Контракт раздела 4** (ручки `/keys/*`, `/account*`, `/account/deletion-*`, admin `key-mail`, новые коды ошибок). Рекомендую: принять; клиентов не ломает.
- **В5. Капча не ставится** — поле-ловушка и лимиты (ADR-5). Рекомендую: да.
