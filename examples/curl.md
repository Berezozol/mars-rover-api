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

# Manifest
curl "$BASE/api/v2/rovers/perseverance/manifest"

# Position of the rover: latest, or the route as GeoJSON
curl "$BASE/api/v2/rovers/perseverance/location?latest=true"
curl "$BASE/api/v2/rovers/perseverance/traverse"

# Ingenuity flights and Perseverance rock samples
curl "$BASE/api/v2/rovers/ingenuity/flights"
curl "$BASE/api/v2/rovers/perseverance/samples"

# RSS feed of new photos
curl "$BASE/api/v2/rovers/perseverance/feed.xml"
```

## Reference

```sh
curl "$BASE/openapi.json"   # OpenAPI schema
curl "$BASE/llms.txt"       # short reference for LLM assistants
```

Images courtesy of NASA/JPL-Caltech and instrument partners; keep each photo's `credit` field.
