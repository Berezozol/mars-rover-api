# ruff: noqa: E501, SIM115, UP031, B905
"""Compact statistics over the MEDA daily CSV produced by weather_meda_daily.py."""

import csv
import statistics as st
import sys

rows = list(csv.DictReader(open(sys.argv[1])))
print("sols", len(rows))


def col(name, f=float):
    return [(int(r["sol"]), f(r[name])) for r in rows if r.get(name) not in (None, "")]


def rng(name, nd=2):
    c = col(name)
    if not c:
        print(name, "none")
        return
    v = sorted(x for _, x in c)
    mn = min(c, key=lambda t: t[1])
    mx = max(c, key=lambda t: t[1])
    print(
        f"{name}: n={len(v)} min={mn[1]} (sol {mn[0]}) p1={v[len(v) // 100]:.{nd}f} med={v[len(v) // 2]:.{nd}f} p99={v[-len(v) // 100 - 1]:.{nd}f} max={mx[1]} (sol {mx[0]})"
    )


for n in (
    "tmed_min",
    "tmed_max",
    "tmed_mean",
    "tmed_hmean",
    "p_min",
    "p_max",
    "p_mean",
    "p_hmean",
    "wind_mean",
    "wind_max",
    "wind_p99",
    "rh_mean",
    "rh_max",
    "ats_cov",
    "p_cov",
    "rh_cov",
    "ws_cov",
    "ats_hours_cov",
    "ats_rows",
    "ats_offsol",
    "ats_bad",
    "p_outrange",
    "rh_outrange",
    "ws_still_frac",
    "wind_dir_R",
):
    rng(n)
# sensor comparisons: mean diff of daily max / min vs tmed
for k in ("t1", "t2", "t3", "t4", "t5"):
    for s in ("max", "min", "mean"):
        d = [
            float(r[f"{k}_{s}"]) - float(r[f"tmed_{s}"])
            for r in rows
            if r.get(f"{k}_{s}") and r.get(f"tmed_{s}")
        ]
        if d:
            print(
                f"{k}-tmed {s}: mean={st.mean(d):+.2f} p5={sorted(d)[len(d) // 20]:+.2f} p95={sorted(d)[-len(d) // 20 - 1]:+.2f}"
            )
d = [float(r["tmed_mean"]) - float(r["tmed_hmean"]) for r in rows if r.get("tmed_hmean")]
print(
    "tmed mean-hmean abs: mean",
    round(st.mean(abs(x) for x in d), 2),
    "max",
    round(max(abs(x) for x in d), 2),
)
d = [float(r["p_mean"]) - float(r["p_hmean"]) for r in rows if r.get("p_hmean")]
print(
    "p mean-hmean abs: mean",
    round(st.mean(abs(x) for x in d), 2),
    "max",
    round(max(abs(x) for x in d), 2),
)
# coverage histogram
cov = [float(r["ats_cov"]) for r in rows if r.get("ats_cov")]
for lo, hi in ((0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)):
    print(f"ats_cov [{lo},{hi}): {sum(1 for c in cov if lo <= c < hi)}")
cov = [int(r["ats_hours_cov"]) for r in rows if r.get("ats_hours_cov")]
for lo, hi in ((0, 6), (6, 12), (12, 18), (18, 24), (24, 25)):
    print(f"hours covered [{lo},{hi}): {sum(1 for c in cov if lo <= c < hi)}")
# suspicious
print(
    "tmed_max>300 or tmed_min<150:",
    [
        (r["sol"], r["tmed_min"], r["tmed_max"])
        for r in rows
        if r.get("tmed_min") and (float(r["tmed_max"]) > 300 or float(r["tmed_min"]) < 160)
    ][:15],
)
print(
    "p<500 or >900:",
    [
        (r["sol"], r["p_min"], r["p_max"])
        for r in rows
        if r.get("p_min") and (float(r["p_min"]) < 500 or float(r["p_max"]) > 950)
    ][:15],
)
print(
    "no pressure:",
    [r["sol"] for r in rows if not r.get("p_min")][:20],
    sum(1 for r in rows if not r.get("p_min")),
)
print("no ats vals:", [r["sol"] for r in rows if not r.get("tmed_min")][:20])
print("rh_max>100 n:", sum(1 for r in rows if r.get("rh_max") and float(r["rh_max"]) > 100))
print(
    "wind>25 max:",
    [
        (r["sol"], r["wind_max"], r["wind_p99"])
        for r in rows
        if r.get("wind_max") and float(r["wind_max"]) > 25
    ][:10],
    sum(1 for r in rows if r.get("wind_max") and float(r["wind_max"]) > 25),
)
print(
    "wind sols",
    sum(1 for r in rows if r.get("wind_mean")),
    "range",
    min((int(r["sol"]) for r in rows if r.get("wind_mean")), default=None),
    max((int(r["sol"]) for r in rows if r.get("wind_mean")), default=None),
)
print(
    "ls missing:",
    sum(1 for r in rows if not r.get("ls_start")),
    "ls range",
    min(float(r["ls_start"]) for r in rows if r.get("ls_start")),
    max(float(r["ls_start"]) for r in rows if r.get("ls_start")),
)
print("versions ats", {r["ats_ver"] for r in rows}, "ws", {r.get("ws_ver") for r in rows})
# seasonal means: tmed_hmean by sol decile per 300 sols
for a in range(0, 1800, 300):
    xs = [
        float(r["tmed_hmean"]) for r in rows if r.get("tmed_hmean") and a <= int(r["sol"]) < a + 300
    ]
    ps = [float(r["p_hmean"]) for r in rows if r.get("p_hmean") and a <= int(r["sol"]) < a + 300]
    if xs:
        print(
            f"sols {a}-{a + 299}: T hmean {min(xs) - 273.15:.0f}..{max(xs) - 273.15:.0f} C; P {min(ps):.0f}..{max(ps):.0f}"
            if ps
            else ""
        )
