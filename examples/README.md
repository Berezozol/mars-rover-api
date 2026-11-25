# Examples

- [curl.md](curl.md) and [httpie.md](httpie.md): command-line requests for v1 and v2.
- [bruno/](bruno/): a [Bruno](https://www.usebruno.com/) collection (open the folder, pick the
  `local` environment or set `baseUrl` to your host).

- [weather.py](weather.py) and [weather.mjs](weather.mjs): daily and hourly weather of Curiosity or
  Perseverance (standard library only; Node 18+ for the `.mjs`). Arguments: base URL, rover, `sol_min`, `sol_max`.

- [radiation.py](radiation.py) and [radiation.mjs](radiation.mjs): daily radiation of Curiosity and the hourly
  dose of one sol (standard library only; Node 18+ for the `.mjs`). Arguments: base URL, `sol_min`, `sol_max`.

## Check your old-API requests: `compat_check.py`

[compat_check.py](compat_check.py) sends typical requests of the archived Mars Rover Photos API
(rovers, a rover, photos by sol and camera, by `earth_date`, `latest_photos`, manifest, photo by id,
`page=1` and `page=2`, upper-case rover name) with `?api_key=DEMO_KEY`, under both `/api/v1` and
`/mars-photos/api/v1`. For each request it prints `OK` or `FAIL` with the status, the response time
and a short reason, for example a missing field or a wrong top-level key. It exits with 0 if
everything passes and 1 otherwise.

It needs only Python 3 and no packages. It sends one request at a time with the
User-Agent `compat-check/1.0`.

```bash
python examples/compat_check.py                          # http://127.0.0.1:8000
python examples/compat_check.py https://your-host.example
python examples/compat_check.py --base-url http://127.0.0.1:8000 --timeout 30
```

The photo checks need data for Curiosity sol 1000; an empty list is reported as `FAIL`.
The pagination check accepts both cases: a full page 1 (25 photos) needs a non-empty page 2 with
different ids, a shorter page 1 needs an empty page 2.

Photo counts for Curiosity before sol 4103 (until February 2024) are lower than in the old API.
This API serves only full-frame photos there, while the old API also kept other frame types.
From sol 4103 the counts match.
