# Weather fixtures (stage 17)

Thinned copies of the raw PDS files (MEDA `mars2020_meda`, REMS `mslrem_1001`), built offline by
`uv run python scripts/record_fixtures.py --part weather` from `data/weather_raw/` (git-ignored).
No requests are made. Same layout as `data/weather_raw/`: `meda/<collection>/sol_AAAA_BBBB/sol_NNNN/`
and `rems/DATA/SOL_AAAAA_BBBBB/SOLNNNNN/`. Label files (`.xml`, `.LBL`) are copied whole. Original
line endings are kept byte for byte (CRLF in all of them): do not let git normalise them
(`.gitattributes`: `tests/fixtures/upstream/weather/** -text`).

Thinning: MEDA - the first row of every 30 s of LMST; REMS - the first row of every 60 s of LMST
(all hours of the sol keep rows). Rows with humidity > 100 % (MEDA) and with a ground temperature
below 120 K (REMS) are always kept.

| Sol | Files | What it covers |
|-----|-------|----------------|
| MEDA 100 | `CAL_ATS` P01, `CAL_WS` P03, `DER_PS` P01, `DER_RHS` P02, `DER_TIRS` P03 (one row per 60 s, 17G ground estimate) | all 24 hours, wind (booms with stable flags), the SYNTHETIC rows below |
| MEDA 400 | `CAL_ATS` P01, `DER_PS` P01, `DER_RHS` P01 | no `CAL_WS` (wind only on sols 36-315 -> `wind: null`) |
| MEDA 1230 | `CAL_ATS` P01, `DER_PS`, `DER_RHS` | short sol: ATS only in hours 0-7 (night), 16 humidity values over 100 % |
| MEDA 1852 | `CAL_ATS` P01, `DER_PS`, `DER_RHS` | 1601 humidity values in 100-200 % (max 183.76) |
| REMS 9 | `RMD` version `PA` (not `P1`) | hours 12-23 only; 40 columns, quoted masks, `-999.00` |
| REMS 30 | `RMD` version `PA` | all hours; `BRIGHTNESS_TEMP` 92.64 and 114.54 K with a "bad" mask (`0111`): the glitch of AC-5 |

SYNTHETIC rows appended at the end of the sol 100 files (everything else is real data):

| File | Row (LMST) | Meaning |
|------|-----------|---------|
| `CAL_ATS` P01 | `00100M12:00:01.000`, `ATS_LOCAL_TEMP2` = 999.9 | T2 outside 150-300 K (T1 200.0, T3 200.5) |
| `DER_PS` P01 | `00100M12:00:02.000`, `PRESSURE` = 50.0 | pressure below 300 Pa |
| `DER_RHS` P02 | `00100M12:00:03.000` = 2680332.0 | humidity outlier (> 200 %) |
| `DER_RHS` P02 | `00100M12:00:04.000` = 104.5 | humidity over 100 % but within 200 % |
| `DER_RHS` P02 | `00100M12:00:05.000` = 999999999 | MEDA gap marker |
| `DER_RHS` P02 | `00100M12:00:06.000` = (empty) | empty field |
| `DER_TIRS` P03 | `00100M12:00:10.000`, `UPWARD_LW_IRRADIANCE` = 1.0, tilt 1, wheel 1 | 64.8 K: out of the 120-330 K bounds, counted |
| `DER_TIRS` P03 | `00100M12:00:11.000`, flux 300.0, `ROVER_LOW_TILT` = 0 | filtered out (rover tilted), not counted |
| `DER_TIRS` P03 | `00100M12:00:12.000`, flux 300.0, wheel flag 0 | filtered out (wheel in the field of view), not counted |

## SYNTHETIC: `synthetic/single_reading/` (stage 22, hourly min/max; no real data at all)

Built with the helpers of `tests/_weather.py` (MEDA format, sol 7, CRLF). The real fixtures have no
hour with a single reading, so this small set covers the edge cases of the hourly `_min`/`_max`:

| File | Hour 3 | Hour 4 | Hour 5 | Hour 6 |
|------|--------|--------|--------|--------|
| `sol_0007_CAL_ATS.csv` | one reading, 210 K | 205 K and 207 K | two readings of 999.9 K (all out of range) | 220 K and 224 K |
| `sol_0007_DER_PS.csv` | one reading, 740.5 Pa | 741.0 and 743.0 Pa | two readings of 50 Pa (all out of range) | none |
| `sol_0007_CAL_WS.csv` | one speed, 4.0 m/s | 2.0 and 6.0 m/s | none | none |

Other versions of a product (`CAL_ATS` P02 of sol 100) and the quirk files are built in the tests.
