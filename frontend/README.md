# Sol Index frontend

Web interface for the Mars rover raw-image archive, built on API v2 only (`docs/API.md`).
Unofficial project: no NASA/JPL marks; image credit is shown on every photo.

Stack: Vite + TypeScript (strict) + Preact, own tiny History API router (`src/router.tsx`), no UI or CSS
frameworks. Design tokens and components are ported from `design/mockups/styles.css`.
Versions are pinned in `package.json`, the lock file is `package-lock.json`. Node.js 22+ (developed on 26 LTS).

## Commands (Windows, from `frontend/`)

If `npm` is not on PATH in your shell, use `"C:\Program Files\nodejs\npm.cmd"` instead of `npm`.

| Command             | What it does                                                                |
| ------------------- | --------------------------------------------------------------------------- |
| `npm ci`            | install the locked dependencies                                             |
| `npm run dev`       | dev server on http://localhost:5173, proxies the API                        |
| `npm run build`     | type-check and build into `frontend/dist` (`index.html` + hashed `assets/`) |
| `npm run preview`   | serve the built `dist` locally                                              |
| `npm test`          | Vitest + Testing Library (jsdom); the network is mocked                     |
| `npm run lint`      | ESLint and a Prettier format check                                          |
| `npm run typecheck` | `tsc --noEmit`                                                              |
| `npm run format`    | Prettier write                                                              |

## Running against the API locally

1. From the repository root: `docker compose up -d db`, then `uv run uvicorn app.main:app` with `SYNC_ENABLED=false`
   (PowerShell: `$env:SYNC_ENABLED="false"; uv run uvicorn app.main:app`).
2. In `frontend/`: `npm run dev`. The Vite dev server proxies `/api`, `/health`, `/docs`, `/openapi.json` and
   `/developers` to `http://127.0.0.1:8000`, so the interface uses relative `/api/v2/...` paths.
   To preview against another backend (e.g. the public deployment) set `SOL_INDEX_BACKEND`, e.g.
   `SOL_INDEX_BACKEND=https://example.ts.net npm run dev` (PowerShell: `$env:SOL_INDEX_BACKEND="https://..."`).
   An `https://` target also gets `changeOrigin` and certificate checking.

Or build (`npm run build`) and open http://127.0.0.1:8000/ : the backend serves `frontend/dist` and the
interface routes (`/`, `/rovers/{rover}`, `/rovers/{rover}/timeline`, `/rovers/{rover}/map`, `/photos/{id}`).

## Configuration

`VITE_API_BASE` (build time, see `.env.example`): base URL of the API. Empty (default) means the same origin.

## Addresses

| Path                                           | Screen                                                                                                                                                                                                                                                                                    |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/`                                            | rovers, quick jump, latest photos                                                                                                                                                                                                                                                         |
| `/rovers/{rover}`                              | photo browser; query `sol` or `earth_date`, `camera`, `sample_type`, `page`, `per_page`; stop filter `site`+`drive_from`[+`drive_to`] (all sols of a stop); without sol/date/stop: latest sol (`max_sol`)                                                                                 |
| `/rovers/{rover}/timeline`                     | mission timeline; query `from_sol`, `to_sol`                                                                                                                                                                                                                                              |
| `/rovers/{rover}/map`                          | route map (SVG on a lat/lon grid, no basemap); query `from_sol`, `to_sol` (highlight), `sol`+`site`+`drive` (selected stop)                                                                                                                                                               |
| `/rovers/{rover}/flights`                      | Ingenuity flight log: summary tiles, sortable table, `#flight-N` anchors                                                                                                                                                                                                                  |
| `/rovers/{rover}/map?near=LAT,LON&radius_km=R` | "Photos near a point" mode of the route map (Curiosity and Perseverance only); also `camera`, `sol_min`, `sol_max`, `sort` (`-sol`/`sol`), `per_page`, `page`; `near=` alone opens the mode without a point                                                                               |
| `/search`                                      | cross-rover photo search (`GET /api/v2/photos`); the address uses the API parameter names (`rovers`, `sol`/`sol_min`/`sol_max`, `earth_date`/`date_min`/`date_max`, `camera`, `sample_type`, `sort`, `golden_hour`, `local_time_min`/`max`, `min_width`/`min_height`, `page`, `per_page`) |
| `/stats`                                       | photo counts (`GET /api/v2/photos/stats`); query `rovers`, `camera`, `sol_min`/`sol_max` or `date_min`/`date_max`, `sample_type=full`, `group_by=month                                                                                                                                    | year | camera`(tab),`view=table`, `page` (table page) |
| `/photos/{id}`                                 | photo card; `?from=/rovers/...` carries the list's camera/sample type (for `in_sol`) or an Earth-date list (page walking)                                                                                                                                                                 |
| anything else                                  | "Page not found" inside the interface                                                                                                                                                                                                                                                     |

`/developers` and `/docs` are server pages: plain links, not SPA routes.

## Layout

- `src/api/` — the only API client (`client.ts`), v2 types, and error wording (`errors.ts`).
- `src/pages/` — Home, Photos, Photo, Timeline, RouteMap, Flights, Search, Stats, NotFound. `src/lib/search.ts`, `stats.ts`, `near.ts` — logic of the stage 19 screens without a DOM (address <-> filters <-> API query, validation, `fillPeriods`, radius slider, distances); `src/components/NearView.tsx` — the "near a point" mode of the map. `src/lib/flights.ts` — flight log helpers (totals, sorting, records); `src/lib/maplinks.ts` — stop photos recipe. `src/lib/mapgeo.ts` — pure map geometry (projection, fit, compact paths). `src/components/` — shared pieces.
- `tests/` — unit tests (client, error parsing, routing) and screen tests with a mocked `fetch`.

## Test fixtures

`tests/fixtures/*.json` are small, decimated real responses (route, stops, rovers; about 120 KB in total, minified, listed in `.prettierignore`). Regenerate them from a live API with `node scripts/make-fixtures.mjs https://your-deployment.example`.
