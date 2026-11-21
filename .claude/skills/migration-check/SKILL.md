---
name: migration-check
description: Проверить новую миграцию Alembic на отдельной временной базе — upgrade, downgrade на шаг, снова upgrade, alembic check, зеркальность downgrade, индексы под запросы. Базы разработки и тестовые не затрагиваются.
argument-hint: "[ревизия, по умолчанию последняя]"
---

Ты тимлид. Проверка миграции $ARGUMENTS (по умолчанию последняя в `migrations/versions/`). Работаешь на временной базе `mars_migcheck` в контейнере разработки и после проверки её удаляешь. Базы `mars`, `mars_test` и `mars_test_gw*` не трогаешь.

## 1. Чтение
Прочитай `upgrade` и `downgrade`: зеркальны ли (всё создаваемое удаляется в обратном порядке), нет ли необратимой потери данных без пометки, подходит ли способ для больших таблиц (`CONCURRENTLY` для индексов на `photos`, значения по умолчанию без полной перезаписи таблицы). Автогенерацию проверяй глазами: лишние `drop`, переименования, типы.

## 2. Прогон
1. `docker compose up -d --wait db`.
2. Создай базу: `docker compose exec -T db psql -U mars -d postgres -c "CREATE DATABASE mars_migcheck"`.
3. Укажи её через `DATABASE_URL` (формат из `.env.example`, имя базы `mars_migcheck`, порт 5433) и выполни по очереди: `uv run alembic upgrade head`, `uv run alembic downgrade -1`, `uv run alembic upgrade head`, `uv run alembic check` (модели и миграции совпадают).
4. Для каждого нового индекса найди запрос, под который он создан, и сделай `EXPLAIN` на базе разработки `mars` (только чтение): индекс используется.
5. Удали базу: `docker compose exec -T db psql -U mars -d postgres -c "DROP DATABASE mars_migcheck"`.

## 3. Отчёт
Прошли ли все шаги, зеркален ли `downgrade`, время `upgrade` (оценка для `photos` на полном объёме порядка 1,7 млн строк), используется ли индекс, что делать на проде (`alembic upgrade head` отдельным шагом до запуска новой версии — это `devops`). Замечания — `backend-dev`.
