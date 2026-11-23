# MSL RAD RDR fixtures (stage 21)

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
| `CHECKSUMS.TXT` | rows of the files above + sol 222 x2 (md5 not of a local file), `.LBL`, `.FMT`, `CRUISE/` `.TXT`, `DOCUMENT/`, `AAREADME.TXT`, one broken line |
| `TIMING_ERRORS.TXT` | 2 `Timing Error` + 1 `Date Range Error` (CRLF); the sol 10 file is named in the latter |

The real `_15_37_` file of sol 1649 is truncated at the source and not used.
