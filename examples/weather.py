"""Print daily weather of a rover and the hourly air temperature of one sol.

Standard library only. Usage:
    python examples/weather.py [BASE_URL] [ROVER] [SOL_MIN] [SOL_MAX]
Defaults: http://127.0.0.1:8000 perseverance 100 105
"""

import json
import sys
import urllib.error
import urllib.request


def get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "weather-example/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def main() -> int:
    args = sys.argv[1:]
    base = (args[0] if len(args) > 0 else "http://127.0.0.1:8000").rstrip("/")
    rover = args[1] if len(args) > 1 else "perseverance"
    sol_min = args[2] if len(args) > 2 else "100"
    sol_max = args[3] if len(args) > 3 else "105"

    try:
        data = get(f"{base}/api/v2/rovers/{rover}/weather?sol_min={sol_min}&sol_max={sol_max}")
    except urllib.error.HTTPError as error:
        print(f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:200]}")
        return 1

    # `credit` must be shown with the data.
    print(f"Credit: {data['credit']}")
    print(f"Latest sol we have: {data['source_max_sol']} (the archive lags by 6-8 months)")
    print(
        "Parameters:",
        ", ".join(f"{k} [{v['unit']}, {v['quality']}]" for k, v in data["parameters"].items()),
    )
    # Perseverance's ground temperature has quality "estimate" (about +-3 K).
    ground_quality = data["parameters"].get("ground_temp_c", {}).get("quality", "n/a")
    print(
        "sol  air min/mean/max (C)   pressure mean (Pa)  wind gust (m/s)  "
        f"ground mean (C, {ground_quality})  flags"
    )
    for item in data["items"]:
        air = item["air_temp_c"] or {}
        pressure = item["pressure_pa"] or {}
        # Curiosity has no "wind" key; Perseverance has wind: null outside sols 36-313.
        wind = item.get("wind") or {}
        ground = item.get("ground_temp_c") or {}
        flags = ",".join(flag["code"] for flag in item["flags"]) or "-"
        air_cols = " ".join(fmt(air.get(key)).rjust(6) for key in ("min", "mean", "max"))
        print(
            f"{item['sol']:<4} {air_cols}"
            f"   {fmt(pressure.get('mean')):>18}  {fmt(wind.get('gust_ms')):>15}"
            f"  {fmt(ground.get('mean')):>10}  {flags}"
        )

    if data["items"]:
        sol = data["items"][0]["sol"]
        detail = get(f"{base}/api/v2/rovers/{rover}/weather/{sol}")
        print(f"\nHourly air temperature, sol {sol} (LMST hours; - = no readings)")
        print(" ".join(f"{h['hour']:02d}:{fmt(h['air_temp_c'])}" for h in detail["hours"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
