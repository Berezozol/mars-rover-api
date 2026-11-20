# Этап 12: источник журнала Ingenuity и данные для предложений дизайнера

Дата 2026-11-20. Подробные факты и причуды — в `docs/DATA-SOURCES.md`, разделы «Полёты Ingenuity» и «Данные для предложений дизайнера».

## A. Источник журнала полётов (обновлено: 2 разрешённых запроса к данным Ingenuity)
- Ранее (проверка страницы миссии): 2 GET к `science.nasa.gov`, оба без нужных данных (404 и страница миссии без таблицы полётов).
- Дополнительно по «ок» владельца ровно 2 GET (UA `sol-index-research/1.0`, пауза 2 с, без повторов):
  1. PDF журнала `.../2024/06/ingenuity-helicopter-flight-log.pdf` — 200, 548 273 Б, sha256 `dcf0b9e5...bf4e11`, Last-Modified 2024-07-01, ETag `c5aa4c661e823127`.
  2. `https://mars.nasa.gov/rss/api/?feed=raw_images&category=ingenuity&feedtype=json&num=100&page=0&order=sol+desc` — 200, 213 675 Б.
- Файлы в `data/ingenuity/` (в `.gitignore`): `flight-log.pdf`, `flights.csv` (72 строки), `raw_images_ingenuity_p0.json`.
- PDF: 72 полёта, 4 страницы, таблицы извлекаются `pdfplumber`; итоги сходятся с шапкой (128,86 мин, 16 971 м, макс. высота 24 м). Колонки: flight, sol, date (PT), дистанция, высота, скорость (м и футы/мили, футы с `~`), длительность (с), маршрут from/to (`to` пуст в 27 полётах). Причуды (многострочные даты, `\uf030`, лигатура fi как `\x00`, дубль таблицы на стр. 4) — в `DATA-SOURCES.md`. Полёт 72: сол 1035, 2024-01-18.
- Фид `category=ingenuity`: 14 553 снимка, камеры `HELI_NAV`/`HELI_RTE`, формат как у mars2020, 17 полей; на странице только сол 1069 (22.02.2024, после полёта 72); номера полёта в записях нет. В нашей базе этих снимков 0 (проверено по 100 `imageid` и по кодам камер).
- Рекомендация: сид `ingenuity_flights` из CSV; связь с фото по солу (`flight = null` для солов без полёта); отдельный `rover` `ingenuity` и адаптер на `category=ingenuity`; v2: `/ingenuity/flights`, `/ingenuity/flights/{n}`; v1 не трогать.
- Открыто: нижняя граница солов и распределение снимков (нужны страницы 1–145 или фильтр по солу); природа кадров сола 1069; текст лицензии NASA; действует ли `order=sol+desc`.

## B. Данные для API v2
Сводка по предложениям дизайнера (`design/NOTES.md`, «Чего не хватает»):
| Предложение | Данные есть? |
|---|---|
| width/height | Perseverance 100% (`extra.extended.dimension`); MER 100% (`extra.lines/line_samples`, размер EDR); Curiosity нет |
| local_time | Curiosity 49% (`extended.lmst`, LMST); MER 100% (`local_true_solar_time`, LTST, другая шкала); Perseverance в фиде есть (`date_taken_mars`), но адаптер не сохраняет |
| миниатюры Curiosity | в списке нет; фид даёт отдельные записи-миниатюры (`is_thumbnail`), адаптер их отбрасывает |
| end_date MER | в базе нет; только max_sol/max_date |
| счётчики камер за сол | таблица `camera_sol_counts` готова; чтение одного сола 0,8 мс |
| соседи в соле | индекс `ix_photos_rover_id_sol_taken_at` подходит: prev/next 0,04–0,14 мс, position 0,33 мс (сол 1717, 3 950 снимков) |

Замечание: у Opportunity `max_date` 2018-06-10, а `full_max_date` 2018-06-11 при одном `max_sol` 5111 — разобрать отдельно.

EXPLAIN (ANALYZE, BUFFERS), dev-база, сол 1717 Perseverance, позиция 2000: NEXT 0,141 мс (Index Only Scan, 4 буфера); PREV 0,037 мс; POSITION 0,328 мс (18 буферов); живой GROUP BY по камерам 19,4 мс (Bitmap Heap Scan, 1 019 буферов); `camera_sol_counts` по PK 0,82 мс; Curiosity сол 0 живой GROUP BY 6,6 мс; все `camera_sol_counts` Curiosity 8,5 мс.
