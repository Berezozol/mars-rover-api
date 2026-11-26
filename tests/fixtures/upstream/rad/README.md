# MSL RAD RDR fixtures (stages 21 and 22)

Trimmed copies of real files of the PDS volume `MSL-M-RAD-3-RDR-V1.0`, built offline by
`uv run python scripts/record_fixtures.py --part rad` from `data/rad_sample/` (git-ignored).
No requests are made. PHA sections are cut to a few lines; only a few observations are kept
(their original numbers `[OBSERVATION: NN]` are kept). `CHECKSUMS.TXT` carries the md5 of the
trimmed files.

| File | What it covers |
|------|----------------|
| `RAD_RDR_2012_228_20_41_0010_V00.TXT` | sol 10, early format (no `OBS_DURATION`, unquoted `QC`, `START_SOL=0010`): obs 0, 12 (garbage UTC `2013-123`), 13 (BU rate 0.03/s), 25 |
| `RAD_RDR_2026_080_17_18_4843_V00.TXT` | sol 4843, recent format (CRLF): obs 0 (histograms kept, 120 PHA lines, file > 64 KiB), 1 (PHA-only twin), 2, 22 (`QC=00000001`), 23 (twin), 174 (local time wrapped), 175 (twin) |
| `RAD_RDR_2026_079_17_02_4842_V00.TXT` | SYNTHETIC: copy of the 4843 file with `START_SOL=4842` and `START_TIME` 2026-079 17:02:00 |
| `RAD_RDR_2017_085_15_54_1649_V00.TXT`, `..._16_28_1649_V00.TXT` | sol 1649 duplicate pair: different `[FILE]` headers (`START_TIME`, `CREATION_TIME`), identical bodies (obs 1, 3, 14 with `QC=00000001`, 17 with UTC jump of about +6 h), same size, > 64 KiB |
| `RAD_RDR_2012_341_21_28_0120_V00.TXT` | sol 120 (2012, early format, `live_s` 1920 s, step 1984 s), ADR-7 revision 3: obs 0-20 and 43. Conflicting by the rule: 4 (starts 377 s after 3) and 18 (392 s after 17). 43 is on the next sol by UTC. Expected: accepted, `hourly_dropped` 3 (43 + 4 + 18), 19 observations in the hours. Share of conflicting: 2 of 21 = 9.5 % (the full file: 2 of 43 = 4.7 %; a run of neighbours long enough for the original share would be 450 KB) |
| `RAD_RDR_2013_212_05_04_0350_V00.TXT` | sol 350 (2013, early format, 960 s, step 1024 s), ADR-7 revision 3: obs 20-44 and 85. Conflicting: 34 (883 s after 32, overlap 77 s) and 35 (883 s after 33); 33 starts 141 s after 34 but is accepted, because the comparison is with the last *accepted* one (32). 85 is on the next sol. Expected: accepted, `hourly_dropped` 3 (85 + 34 + 35), 23 observations in the hours. Share: 2 of 25 = 8.0 % (the full file: 6 of 85 = 7.1 %) |
| `RAD_RDR_2017_086_17_39_1649_V00.TXT` | sol 1649, a different file than the pair above (not in `CHECKSUMS.TXT`, used by the unit and the integration tests, which serve it alone): two interleaved streams, 22 observations of the time-ordered run 79 ... 69. Conflicting: 101, 103, 107, 109, 69. Expected: `overlap`, no hours, `hourly_dropped` 22. Share: 5 of 22 = 22.7 % (the full file: 14 of 64 on the sol = 21.9 %, 16.7 % of the 84 used) |
| `CHECKSUMS.TXT` | rows of the files above (except the `_086_17_39_` file of sol 1649) + sol 222 x2 (md5 not of a local file), `.LBL`, `.FMT`, `CRUISE/` `.TXT`, `DOCUMENT/`, `AAREADME.TXT`, one broken line |
| `TIMING_ERRORS.TXT` | 2 `Timing Error` + 1 `Date Range Error` (CRLF); the sol 10 file is named in the latter |

The real `_15_37_` file of sol 1649 is truncated at the source and not used.

The sol 120, 350 and 1649 (`_086_17_39_`) files of stage 22 (ADR-7 revision 3) are trimmed the same way from
`data/rad_c5/` (git-ignored, files of the PDS volume downloaded for the C5 analysis, see
`docs/reports/stage-22-rad-hourly.md`); `--part rad` needs both `data/rad_sample/` and `data/rad_c5/`.
The 1649 pair above (`_085_`) stays `off_sol` for the hours: obs 1 and 3 are on sol 1648 by UTC (1 of 3 on the sol).
