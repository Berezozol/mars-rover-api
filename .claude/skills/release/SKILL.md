---
name: release
description: Выкатить запушенную версию на продакшен-VPS (api.solindex.net, solindex.net) — проверки, моё «ок», deploy/vps/update.sh по SSH (pull, сборка, бэкап, миграции, перезапуск, выпуск фронтенда), проверки после выкладки, smoke-тест на обоих хостах, итог.
argument-hint: "[--skip-pull]"
disable-model-invocation: true
---

Ты — тимлид, выкатываешь новую версию на продакшен: VPS Contabo, compose в `/srv/mars`, проект `mars`, рабочая копия `/srv/mars/repo` (ветка `master`, тянет с GitHub), Caddy впереди, наружу `api.solindex.net` и `solindex.net`. Подробности — `deploy/README.md`, раздел «VPS (Contabo) + Caddy». Аргументы: $ARGUMENTS (`--skip-pull` — собрать то, что сейчас в `/srv/mars/repo`, без `git pull`).

SSH — только так (ключ в ssh-agent; в PowerShell длинные команды передавай here-string `@'...'@`, иначе ломаются кавычки):
`C:\Windows\System32\OpenSSH\ssh.exe deploy@api.solindex.net '<команда>'`
Compose на сервере — из `/srv/mars` (`cd /srv/mars && docker compose ...`). IP сервера в отчёты и файлы не пиши.

## 1. Проверки — без изменений
- Локально `git status`, `git log --oneline -5`, `git fetch` и `git status -sb`: сервер тянет `origin/master`, значит выкатывается только **запушенное**. Локальные коммиты без пуша или незакоммиченные изменения в `app/`, `migrations/`, `frontend/`, `Dockerfile`, `deploy/` — остановись и скажи, что они в выкладку не попадут (нужен коммит и пуш с моим «ок»).
- Что сейчас на проде: `grep ^APP_TAG= /srv/mars/.env` и `readlink /srv/mars/www/current` (должны совпадать), `cd /srv/mars/repo && git log --oneline -1`. Изменения: локально `git log --oneline <APP_TAG>..origin/master`.
- Новые миграции: `git diff --stat <APP_TAG>..origin/master -- migrations/`; для каждой прочитай `downgrade` и оцени время на полном объёме.
- Что ещё задето: `git diff --stat <APP_TAG>..origin/master -- deploy/ .env.example`. Изменения `Caddyfile`/`compose.yml` — после выкладки нужен полный проход раздела 6 README; изменения `limits.sh`, новые переменные в `.env`, ручные шаги в README (раздел «Release ...» или новый раздел) — их выполняют **до** `update.sh`, по README, каждый отдельно в плане.
- CI на `origin/master` зелёный: `gh run list --branch master --limit 3`. Красный — стоп, сначала разбор.
- Сервер жив: `cd /srv/mars && docker compose ps`, `df -h /` (на образ и бэкап нужно место; ≥ 85% — сказать).

## 2. План — и стоп
Покажи мне: коммит, что на проде сейчас, список изменений одной строкой каждое, миграции (обратимы ли, сколько займут), ручные шаги до `update.sh`, что увидят клиенты, риски и откат. **Жди «ок».** Это изменение продакшена; «ок» покрывает только перечисленные шаги.

## 3. Выкладка
1. Ручные шаги из плана, если есть, — по README, с проверкой после каждого.
2. `/srv/mars/repo/deploy/vps/update.sh` (с `--skip-pull`, если передан). Сборка долгая: таймаут 600000 мс. Скрипт сам: `git pull --ff-only`, сборка `mars-rover-api:<commit>`, копирование `compose.yml`/`Caddyfile`, выпуск фронтенда в `www/<commit>`, бэкап `pre-update`, миграции, переключение `www/current` и `APP_TAG`, пересоздание app и Caddy, ожидание `/health`, сравнение `index.html` Caddy и приложения. Успех — строка `UPDATE OK: mars-rover-api:<commit> is serving (previous: <tag>)`.
3. Если упал (`UPDATE FAILED at step: ...`) — **ничего не откатывай сам**: покажи шаг и последние 200 строк `docker compose logs --tail 200 app` (и `caddy`, если упало на шаге 6–7), предложи следующий шаг и жди решения. До шага 6 старая версия продолжает работать.
4. `WARNING: Caddy and the app serve different index.html` — не провал, но разобрать до итога (`readlink /srv/mars/www/current`, лог Caddy).

## 4. Проверка
На сервере:
- `docker compose ps` — все `healthy`; `readlink /srv/mars/www/current` = новый `APP_TAG`;
- `curl -s http://127.0.0.1:8000/ | cmp - <(curl -sk --resolve solindex.net:443:127.0.0.1 https://solindex.net/)` — пусто;
- `docker compose exec app printenv UVICORN_LIMIT_CONCURRENCY` — `384` (не `env`: печатает секреты);
- `curl -s http://127.0.0.1:8000/health/sync` — планировщик жив, последние синхронизации без ошибок, отставания нет;
- `docker compose logs --since 10m app | grep -i -E 'error|traceback|permission denied'` — пусто или объяснимо.

Снаружи (это запросы к нашему же сервису, они входят в «ок» шага 2):
- `uv run python deploy/smoke.py https://api.solindex.net` — всё зелёное;
- `uv run python deploy/smoke.py https://solindex.net` — допустим один провал: `/llms.txt` содержит адрес API, а не `solindex.net`;
- если менялись `Caddyfile`, `compose.yml` или лимиты — строки раздела 6 README, которые они задевают (без проверок, останавливающих app, — те только с отдельного «ок»);
- если менялся фронтенд — открыть изменённую страницу во встроенном браузере, консоль без ошибок.

## 5. Итог
Коммит на проде и прошлый (`previous` из `UPDATE OK`), результат обоих smoke-тестов и проверок на сервере, путь к бэкапу из вывода `update.sh` (`/srv/mars/backups/preupdate-<дата>.dump`), что проверить мне руками (одна-две ссылки, например `https://solindex.net/rovers/perseverance`, `https://api.solindex.net/docs`), что не проверено. Откат — README, «2. Update and rollback»: прошлый тег в `APP_TAG` + `docker compose up -d app` + `publish-frontend.sh <тег>` (только если миграции обратно совместимы, иначе восстановление из бэкапа — с моего «ок»). Строка в «Журнал этапов» `docs/PLAN.md`, если выкладка закрывает этап.
