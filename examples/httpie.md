# HTTPie examples

Set the base URL once. No API key is needed: without one, requests are limited per IP.
An `api_key` that is not one of our keys (for example an api.nasa.gov key) is ignored.
To get a higher limit, see [API key](#api-key-optional) below.

```sh
BASE=https://your-host.example   # or http://127.0.0.1:8000 for a local server
```

## v1 (compatible with the old API)

```sh
http GET "$BASE/api/v1/rovers/curiosity/photos" sol==1000 camera==fhaz
http GET "$BASE/mars-photos/api/v1/rovers/curiosity/photos" sol==1000 camera==fhaz
http GET "$BASE/api/v1/rovers/perseverance/photos" earth_date==2021-06-01 page==2 per_page==10
http GET "$BASE/api/v1/rovers/perseverance/latest_photos"
http GET "$BASE/api/v1/manifests/opportunity"
```

## v2

```sh
http GET "$BASE/api/v2/rovers"
http GET "$BASE/api/v2/rovers/perseverance/photos" sol==100 per_page==50
http GET "$BASE/api/v2/rovers/perseverance/manifest"
http GET "$BASE/api/v2/rovers/perseverance/location" latest==true
http GET "$BASE/api/v2/rovers/perseverance/traverse"
http GET "$BASE/api/v2/rovers/ingenuity/flights"
http GET "$BASE/api/v2/rovers/perseverance/samples"
http GET "$BASE/api/v2/rovers/curiosity/radiation" sol_min==1600 sol_max==1700
http GET "$BASE/api/v2/rovers/perseverance/weather" sol_min==100 sol_max==110
http GET "$BASE/api/v2/rovers/curiosity/weather/1000"
http GET "$BASE/api/v2/rovers/perseverance/feed.xml"
```

## API key (optional)

A key raises the rate limit. This works only on servers where key sign-up is turned on.
Ask for a key, then open the link in the email and press the button on that page:

```sh
http POST "$BASE/api/v2/keys/requests" email=user@example.org
# 202 {"status": "accepted", "mail_delayed": false}
```

The answer is the same for any valid address. The key is shown once, on the confirmation page.
Send it in the `X-Api-Key` header (or the `api_key` parameter; the header wins if both are present):

```sh
KEY=si_EXAMPLEEXAMPLEEXAMPLEEXAMPLE0000   # your key

http GET "$BASE/api/v2/account" "X-Api-Key:$KEY"
http GET "$BASE/api/v2/rovers/perseverance/photos" sol==100 "X-Api-Key:$KEY"
```

## Reference

```sh
http GET "$BASE/openapi.json"
http GET "$BASE/llms.txt"
```

Images courtesy of NASA/JPL-Caltech and instrument partners; keep each photo's `credit` field.
