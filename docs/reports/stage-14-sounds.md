# Этап 14. Проверка источника звуков Марса (2026-11-20)

Запросов к внешним источникам: **3 GET** (2 + 2 разрешённых; четвёртый не нужен), общий User-Agent, без ретраев и переходов по ссылкам. Ответы: `data/sounds/` (`r1`..`r3`, `.html` и `.headers`).

| # | URL | Статус | Размер | Итог |
|---|-----|--------|--------|------|
| 1 | `https://science.nasa.gov/mission/mars-2020-perseverance/participate/sounds-of-mars/` | 404 | 224 656 Б | адрес угадан неверно |
| 2 | `https://mars.nasa.gov/mars2020/participate/sounds/` | 302 | 154 Б | на `.../mars-2020-perseverance/sounds-of-mars/` |
| 3 | `https://science.nasa.gov/mission/mars-2020-perseverance/sounds-of-mars/` | 200 | 303 389 Б, `text/html` | редакционная страница, 3 записи |

## Главное
- Страница «Sounds of Mars» — не каталог: общий текст, 3 кнопки «Listen» (SuperCam: лазерные выстрелы; EDL: езда, сол 16, 90 с; Voyager «Hello from the children of planet Earth» — не запись ровера) и видео-подборка (mp4, 290 МБ). Машиночитаемого списка нет (нет `<audio>`, JSON, `data`-URL, `wp-json` с данными). Четвёртый GET по условию не делали.
- Поля: название, URL, авторство `NASA/JPL-Caltech`. Длительности нет (кроме «90-second highlights» в названии), сол — только у одной записи, описаний на запись нет. Файлы `.mp4` на `science.nasa.gov/wp-content/uploads/` и `assets.science.nasa.gov/content/dam/`.
- Две из трёх записей ведут на страницы `science.nasa.gov/resource/<slug>/` (прямой URL файла там [не проверен]).
- Свежесть: страница изменена 2026-05-07; полного списка «всех опубликованных записей», на который рассчитывал PLAN, на NASA-страницах мы не нашли.

## Рекомендация
1. **Реалистично:** курируемый набор (seed) вручную — по записям со страниц `science.nasa.gov/resource/...` (нужен ещё 1 GET на каждую для прямого URL, длительности, даты, сола) плюс страница «Sounds of Mars» как источник списка. Это 2–10 записей, не «все». Парсер HTML ради 3 строк не оправдан.
2. **Полный охват:** PDS Geosciences, набор микрофона SuperCam (`.wav`) — сырые продукты, сотни файлов, длительность и сол извлекаются из меток; без описаний. Требует отдельного исследования (этап 15 или расширение этапа 14) [не проверено].
3. Модель (общая для обоих вариантов): `sounds(id, rover_id, source ENUM(supercam, edl, other), sol NULL, title, description NULL, duration_s NULL, audio_url UNIQUE, page_url NULL, format, credit, published_at NULL)`; файлы не храним, ручка отдаёт ссылку/редирект. Voyager-запись в набор не включаем (не Perseverance) или помечаем `other`.

## Вопросы владельцу
1. Цель этапа 14 — «все записи» (тогда PDS, отдельная проверка) или курируемая подборка NASA (3–10 записей)?
2. Если подборка: разрешить N GET на страницы `/resource/<slug>/` (по одному на запись, 2 уже известны)?
3. Включать ли видео-подборку «Sample More Sounds From Mars» (mp4, 290 МБ) как запись с `duration_s = NULL`?
