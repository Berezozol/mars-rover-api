# curl examples

Set the base URL once. No API key is needed (an `api_key` parameter is accepted and ignored).

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

## Reference

```sh
curl "$BASE/openapi.json"   # OpenAPI schema
curl "$BASE/llms.txt"       # short reference for LLM assistants
```

Images courtesy of NASA/JPL-Caltech and instrument partners; keep each photo's `credit` field.
