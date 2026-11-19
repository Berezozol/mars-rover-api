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

Or build (`npm run build`) and open http://127.0.0.1:8000/ : the backend serves `frontend/dist` and the
interface routes (`/`, `/rovers/{rover}`, `/rovers/{rover}/timeline`, `/photos/{id}`).

## Configuration

`VITE_API_BASE` (build time, see `.env.example`): base URL of the API. Empty (default) means the same origin.

## Addresses

| Path                       | Screen                                                                                                                            |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `/`                        | rovers, quick jump, latest photos                                                                                                 |
| `/rovers/{rover}`          | photo browser; query `sol` or `earth_date`, `camera`, `sample_type`, `page`, `per_page`; without sol/date: latest sol (`max_sol`) |
| `/rovers/{rover}/timeline` | mission timeline; query `from_sol`, `to_sol`                                                                                      |
| `/photos/{id}`             | photo card; `?from=/rovers/...` carries the list for newer/older                                                                  |
| anything else              | "Page not found" inside the interface                                                                                             |

`/developers` and `/docs` are server pages: plain links, not SPA routes.

## Layout

- `src/api/` — the only API client (`client.ts`), v2 types, and error wording (`errors.ts`).
- `src/pages/` — Home, Photos, Photo, Timeline, NotFound. `src/components/` — shared pieces.
- `tests/` — unit tests (client, error parsing, routing) and screen tests with a mocked `fetch`.
