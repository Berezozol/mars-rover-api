"""Print daily radiation of Curiosity (RAD) and the hourly dose of one sol.

Standard library only. Usage:
    python examples/radiation.py [BASE_URL] [SOL_MIN] [SOL_MAX]
Defaults: http://127.0.0.1:8000 1600 1605
"""

import json
import sys
import urllib.error
import urllib.request


def get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "radiation-example/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fmt(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def main() -> int:
    args = sys.argv[1:]
    base = (args[0] if len(args) > 0 else "http://127.0.0.1:8000").rstrip("/")
    sol_min = args[1] if len(args) > 1 else "1600"
    sol_max = args[2] if len(args) > 2 else "1605"

    try:
        data = get(f"{base}/api/v2/rovers/curiosity/radiation?sol_min={sol_min}&sol_max={sol_max}")
    except urllib.error.HTTPError as error:
        print(f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:200]}")
        return 1

    # `credit` and `citation` must be shown with the data.
    print(f"Credit: {data['credit']}")
    print(f"Citation: {data['citation']}")
    print(f"Latest sol we have: {data['source_max_sol']} (the dataset lags by months)")
    # Daily fields are in microgray per DAY (the hourly ones below are per HOUR).
    print("sol   dose E mean (uGy/day)  dose B mean (uGy/day)  coverage  used")
    for item in data["items"]:
        print(
            f"{item['sol']:<5} {fmt(item['dose_e_ugy_day']['mean']):>21}"
            f"  {fmt(item['dose_b_ugy_day']['mean']):>21}"
            f"  {fmt(item['coverage']):>8}  {item['observations']['used']:>4}"
        )

    if data["items"]:
        sol = data["items"][0]["sol"]
        detail = get(f"{base}/api/v2/rovers/curiosity/radiation/{sol}")
        if detail["timing_caveat"]:
            print(
                f"\nSol {sol}: the dataset lists timing errors for this sol; hours may be shifted."
            )
        hours = detail["hours"]
        if hours is None:
            # Hours were not computed, or the observation times of the sol contradict each other.
            print(f"\nSol {sol}: no hourly values (hours is null)")
            return 0
        # Hourly fields are in microgray per HOUR; `hour` is LMST computed from UTC.
        print(f"\nHourly dose, sol {sol} (LMST hours; - = no observations)")
        print("hour  coverage  obs  dose E (uGy/h)  dose B (uGy/h)")
        for h in hours:
            print(
                f"{h['hour']:02d}    {fmt(h['coverage']):>8}  {h['observations']:>3}"
                f"  {fmt(h['dose_e_ugy_h']):>14}  {fmt(h['dose_b_ugy_h']):>14}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
