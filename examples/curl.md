# curl examples

Set the base URL once. No API key is needed: without one, requests are limited per IP.
An `api_key` that is not one of our keys (for example an api.nasa.gov key) is ignored.
To get a higher limit, see [API key](#api-key-optional) below.

```sh
BASE=https://your-host.example   # or http://127.0.0.1:8000 for a local server
```

## v1 (compatible with the old API)

```sh
# Photos of a sol, one camera
curl "$BASE/api/v1/rovers/curiosity/photos?sol=1000&camera=fhaz"

# The same under the old path prefix
curl "$BASE/mars-photos/api/v1/rovers/curiosity/photos?sol=1000&camera=fhaz"

# Photos by Earth date, second page of 10
curl "$BASE/api/v1/rovers/perseverance/photos?earth_date=2021-06-01&page=2&per_page=10"

# Latest photos
curl "$BASE/api/v1/rovers/perseverance/latest_photos"

# Mission manifest
curl "$BASE/api/v1/manifests/opportunity"
```

## v2

```sh
# Rovers and cameras
curl "$BASE/api/v2/rovers"
curl "$BASE/api/v2/rovers/perseverance/cameras"

# Photos of a sol
curl "$BASE/api/v2/rovers/perseverance/photos?sol=100&per_page=50"

# Photos taken within 2 km of a point (one rover: curiosity or perseverance)
curl "$BASE/api/v2/photos?rovers=perseverance&near=18.44,77.39&radius_km=2&per_page=5"

# Only some keys of each photo (id and credit are always included)
curl "$BASE/api/v2/photos?rovers=perseverance&sol=1000&fields=sol,img_src,camera"

# Manifest
curl "$BASE/api/v2/rovers/perseverance/manifest"

# Position of the rover: latest, or the route as GeoJSON
curl "$BASE/api/v2/rovers/perseverance/location?latest=true"
curl "$BASE/api/v2/rovers/perseverance/traverse"

# Ingenuity flights and Perseverance rock samples
curl "$BASE/api/v2/rovers/ingenuity/flights"
curl "$BASE/api/v2/rovers/perseverance/samples"

# Daily radiation summaries of Curiosity (RAD); sol_min and sol_max are optional
curl "$BASE/api/v2/rovers/curiosity/radiation?sol_min=1600&sol_max=1700"

# Daily weather summaries (curiosity or perseverance); sol_min and sol_max are optional
curl "$BASE/api/v2/rovers/perseverance/weather?sol_min=100&sol_max=110"

# One sol with 24 hourly values (mean, _min, _max) and the source files
curl "$BASE/api/v2/rovers/perseverance/weather/100"

# RSS feed of new photos
curl "$BASE/api/v2/rovers/perseverance/feed.xml"
```

## Time fields in v2 photos

`taken_at` is Earth time (UTC). `local_time` is the local solar time on Mars where the rover stood:
`{"time": "17:40:12", "kind": "lmst", "planet": "mars"}`. `kind` is `lmst` (mean time: Curiosity,
Perseverance, Ingenuity) or `ltst` (true time: Spirit, Opportunity). It is `null` when the source gives no time.

With `near`, each item also has `near_stop`: the rover stop the photo is tied to and its distance to your point,
`{"sol": 2020, "site": 93, "drive": 1840, "lat": 18.4447, "lon": 77.3951, "distance_km": 0.42}`.

`mast` is the camera direction at the time of the shot: `{"azimuth": 309.89, "elevation": 16.9}` in degrees,
measured from the rover body, not from north (0 is roughly forward, so it is not a compass bearing).
It is `null` when the source gives no angles.

`fields=` trims the response to the listed photo keys (comma-separated, lower case). It saves size only: it does not
make searches faster or change rate limits.

## Radiation data

Each item is one sol: `dose_b_ugy_day` and `dose_e_ugy_day` (`mean`, `min`, `max`) are the absorbed dose rate in
the instrument's detectors in microgray per day, not equivalent dose. The dataset lags the mission by months,
so the latest sols are missing. The response has `credit` and `citation`: keep them when you show the data.

## Weather data

Each item of the list is one sol. `air_temp_c`, `pressure_pa`, `humidity_pct` (and `ground_temp_c`, `uv_wm2` for
Curiosity) have `min`, `max`, `mean`; units are °C, Pa, %, W/m2. `mean` is the mean of the hourly means
(LMST hours), not of all readings. Perseverance has `wind` (`speed_mean_ms`, `gust_ms`, `direction_mode_deg`) only
on sols 36-313, and `null` elsewhere; Curiosity has no `wind` key. Perseverance has no UV, and its `ground_temp_c`
is an estimate (`quality: "estimate"`, about ±3 K, `null` on sol 1 and on incomplete sols). Wind direction is an azimuth clockwise from
north as the source gives it. Humidity is `preliminary` (see `parameters` in the response).

`flags` can hold `partial_coverage` (readings in fewer than 18 hours), `humidity_over_100` and `out_of_range`.
Ignore codes you do not know. The archive lags by 6-8 months, so the latest sols are missing.
The response has `credit`: keep it when you show the data.

```sh
# Mean air temperature of each sol, with jq
curl -s "$BASE/api/v2/rovers/perseverance/weather?sol_min=100&sol_max=110" \
  | jq '.items[] | {sol, mean: .air_temp_c.mean, flags: [.flags[].code]}'
```

Hourly values of one sol (`hours`, LMST): the key without a suffix is the hourly mean, `_min` and `_max` are the
lowest and highest valid values of the hour, `null` when the hour has no readings. Perseverance also has
`wind_speed_ms`, `wind_gust_ms` (99th percentile of the hour's wind speed) and `wind_direction_deg`, `null` outside
sols 36-313.

```sh
# Hourly air temperature range and wind gust of one sol
curl -s "$BASE/api/v2/rovers/perseverance/weather/100"   | jq '.hours[] | {hour, air_temp_c, air_temp_c_min, air_temp_c_max, wind_gust_ms}'
```

More: [weather.py](weather.py) (Python, standard library) and [weather.mjs](weather.mjs) (JavaScript, `fetch`).

## API key (optional)

A key raises the rate limit. This works only on servers where key sign-up is turned on.
Ask for a key, then open the link in the email and press the button on that page:

```sh
curl -X POST "$BASE/api/v2/keys/requests" \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.org"}'
# 202 {"status": "accepted", "mail_delayed": false}
```

The answer is the same for any valid address. The key is shown once, on the confirmation page.
To use it, send it in the `X-Api-Key` header (it stays out of proxy logs and browser history)
or in the `api_key` parameter. If both are present, the header wins.

```sh
KEY=si_EXAMPLEEXAMPLEEXAMPLEEXAMPLE0000   # your key

# Your account: keys, usage, limits
curl "$BASE/api/v2/account" -H "X-Api-Key: $KEY"

# Any other request works the same way
curl "$BASE/api/v2/rovers/perseverance/photos?sol=100" -H "X-Api-Key: $KEY"
```

## Reference

```sh
curl "$BASE/openapi.json"   # OpenAPI schema
curl "$BASE/llms.txt"       # short reference for LLM assistants
```

Images courtesy of NASA/JPL-Caltech and instrument partners; keep each photo's `credit` field.
