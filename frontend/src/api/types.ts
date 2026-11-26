// Object shapes of API v2 (docs/API.md).

export interface Rover {
  id: string;
  name: string;
  mission: string;
  launch_date: string;
  landing_date: string;
  /** Official end of the mission; null for active rovers. */
  end_date?: string | null;
  status: string;
  max_sol: number | null;
  max_date: string | null;
  total_photos: number;
  cameras_count: number;
  last_synced_at: string | null;
  /** "sync" = regular feed, "archive" = one-off archive import. */
  data_source?: "sync" | "archive" | string | null;
}

export interface Camera {
  code: string;
  name: string;
  total_photos: number;
}

export interface Images {
  small: string | null;
  medium: string | null;
  large: string | null;
  full: string;
}

export interface Photo {
  id: number;
  rover: string;
  sol: number;
  earth_date: string;
  taken_at: string;
  camera: { code: string; name: string };
  sample_type: string;
  title: string | null;
  img_src: string;
  images: Images;
  credit: string | null;
  source: { id: string; link: string | null };
  /** Pixel size of the full frame; both null when the source does not say. */
  width?: number | null;
  height?: number | null;
  /** Local solar time on Mars at the moment of the shot; null when unknown, absent on older servers. */
  local_time?: LocalTime | null;
  /** Only in `/photos?near=` results: the route stop nearest to the photo. */
  near_stop?: NearStop | null;
}

export interface NearStop {
  sol: number;
  site: number;
  drive: number;
  lat: number;
  lon: number;
  distance_km: number;
}

export interface PhotoLocation {
  sol: number;
  site: number;
  drive: number;
  lat: number;
  lon: number;
  elev_geoid: number | null;
  /** true: taken at this stop; false: taken later (the last stop before the photo). */
  exact: boolean;
}

export interface LocalTime {
  /** HH:MM:SS */
  time: string;
  /** lmst = mean, ltst = true solar time (Spirit, Opportunity). */
  kind: "lmst" | "ltst" | string;
  planet?: string;
}

export interface InSol {
  position: number | null;
  total: number;
  /** Previous in the list (taken later). */
  newer_id: number | null;
  /** Next in the list (taken earlier). */
  older_id: number | null;
}

export interface PhotoDetail extends Photo {
  caption: string | null;
  received_at: string | null;
  site: number | null;
  drive: number | null;
  extra: Record<string, unknown>;
  location?: PhotoLocation | null;
  in_sol?: InSol | null;
  /** Ingenuity only: number of the flight made on the sol of the photo, or null. Always null for other rovers. */
  flight?: number | null;
  /** Perseverance only: numbers of the samples sealed on the photo's sol, ascending; `[]` otherwise. */
  samples?: number[];
}

/** One Ingenuity flight from the flight log (`GET /rovers/ingenuity/flights`). */
export interface Flight {
  flight: number;
  sol: number;
  /** Pacific time, as in the log; the sol is the reliable key. */
  date: string;
  distance_m: number;
  max_altitude_m: number;
  max_groundspeed_mps: number;
  duration_s: number;
  from: string;
  /** null when the log does not name a landing site. */
  to: string | null;
  /** All Ingenuity photos of the flight's sol (not only those from the flight). */
  total_photos: number;
}

export interface PhotoPage {
  items: Photo[];
  page: number;
  per_page: number;
  total: number;
  /** Set only for an empty result of a sol / earth_date search. */
  nearest_sols?: NearestSols | null;
}

export interface NearestSols {
  before: number | null;
  after: number | null;
}

export interface LatestPhotos extends PhotoPage {
  sol: number | null;
}

export interface ManifestSol {
  sol: number;
  earth_date: string;
  total_photos: number;
  cameras: string[];
}

export interface Manifest {
  rover: Rover;
  sols: ManifestSol[];
}

export interface PhotoQuery {
  sol?: number;
  earth_date?: string;
  camera?: string;
  sample_type?: string;
  /** Stop filter: `drive_from` / `drive_to` only together with `site` (inclusive). */
  site?: number | string;
  drive_from?: number | string;
  drive_to?: number | string;
  page?: number;
  per_page?: number;
}

/** Query of `GET /api/v2/photos` (cross-rover search, stage 18B). Every value is sent as given. */
export interface SearchQuery {
  rovers?: string;
  sol?: number | string;
  sol_min?: number | string;
  sol_max?: number | string;
  earth_date?: string;
  date_min?: string;
  date_max?: string;
  camera?: string;
  sample_type?: string;
  sort?: string;
  local_time_min?: string;
  local_time_max?: string;
  golden_hour?: string;
  min_width?: number | string;
  min_height?: number | string;
  near?: string;
  radius_km?: number | string;
  page?: number;
  per_page?: number;
}

export type StatsGroupBy = "rover" | "camera" | "year" | "month";

/** Query of `GET /api/v2/photos/stats` (stage 18C). */
export interface StatsQuery {
  rovers?: string;
  camera?: string;
  sol_min?: number | string;
  sol_max?: number | string;
  date_min?: string;
  date_max?: string;
  sample_type?: string;
  group_by?: StatsGroupBy;
}

export interface StatsGroup {
  rover: string;
  /** Camera code for `group_by=camera`, else null. */
  camera: string | null;
  /** `YYYY` or `YYYY-MM` for `group_by=year|month`, else null. */
  period: string | null;
  total_photos: number;
  sols_with_photos: number;
  first_sol: number;
  last_sol: number;
  first_date: string;
  last_date: string;
}

export interface StatsResponse {
  group_by: StatsGroupBy;
  total_photos: number;
  sols_with_photos: number;
  first_date: string | null;
  last_date: string | null;
  groups: StatsGroup[];
}

export type ImageSize = "small" | "medium" | "large" | "full";

/** Sample types accepted by the `sample_type` filter. */
export const FILTER_SAMPLE_TYPES = ["full", "subframe", "downsampled"] as const;

export interface Waypoint {
  sol: number;
  site: number;
  drive: number;
  lat: number;
  lon: number;
  elev_geoid: number | null;
  easting: number | null;
  northing: number | null;
  roll: number | null;
  pitch: number | null;
  yaw: number | null;
  tilt: number | null;
  dist_m: number | null;
  dist_total_m: number | null;
}

export interface LocationResponse {
  rover: string;
  available: boolean;
  updated_at: string | null;
  source_max_sol: number | null;
  items: Waypoint[];
}

export interface TraverseFeature {
  type: "Feature";
  geometry: { type: "LineString"; coordinates: [number, number][] };
  properties: { sol: number | null; length_m: number | null };
}

/** GeoJSON FeatureCollection with the extra top-level members of the v2 contract. */
export interface Traverse {
  type: "FeatureCollection";
  rover: string;
  available: boolean;
  updated_at: string | null;
  source_max_sol: number | null;
  features: TraverseFeature[];
}

export type SampleStatus = "on_rover" | "depot";
export type SampleKind = "rock_core" | "regolith" | "atmospheric";
export type SampleMatch = "exact" | "nearest_before" | "by_sol";

/** The stop where the rover stood on the sealing sol. `by_sol` is approximate. */
export interface SampleLocation {
  sol: number;
  site: number;
  drive: number;
  lat: number;
  lon: number;
  elev_geoid: number | null;
  match: SampleMatch;
}

/** One sample tube of Perseverance. Text fields are shown exactly as the source page publishes them. */
export interface Sample {
  number: number;
  name: string;
  feature_name: string | null;
  sampling_location: string | null;
  /** null: a value the page uses that we do not know; `current_location` then says it in words. */
  status: SampleStatus | null;
  current_location: string;
  sample_type: SampleKind | null;
  sample_type_label: string;
  rock_type: string | null;
  sealed_on: string | null;
  sol: number | null;
  height_cm: number | null;
  height_note: string | null;
  dossier_url: string | null;
  total_photos: number | null;
  location: SampleLocation | null;
}

export interface SamplesResponse {
  rover: string;
  source_url: string;
  source_updated_at: string | null;
  updated_at: string | null;
  items: Sample[];
}

/** Dose rate of one detector, micrograys per day (the mean is weighted by live time). */
export interface DoseStats {
  mean: number;
  min: number;
  max: number;
}

/** Daily summary of Curiosity's RAD instrument (`GET /rovers/curiosity/radiation`). */
export interface RadiationSol {
  sol: number;
  earth_date: string;
  dose_b_ugy_day: DoseStats;
  dose_e_ugy_day: DoseStats;
  /** Raw count rate of detector B, events per second of live time. */
  count_rate_b_per_s: number;
  /** Share of the sol covered by measurements, 0..1. */
  coverage: number;
  observations: {
    used: number;
    dropped_qc: number;
    dropped_low_rate: number;
    dropped_invalid: number;
  };
  timing_caveat: boolean | null;
  source: { file: string; link: string };
}

/**
 * One LMST hour of RAD data. Hand-written from docs/API.md (stage 22); replace with the generated type
 * when docs/openapi.json has it. Doses are µGy per HOUR (the daily fields are per day).
 */
export interface RadiationHour {
  hour: number;
  /** Share of the hour covered by live time, 0..1. */
  coverage: number;
  observations: number;
  dose_e_ugy_h: number | null;
  dose_b_ugy_h: number | null;
  count_rate_b_per_s: number | null;
}

/** `GET /rovers/curiosity/radiation/{sol}`: RadiationSol plus credit and the hours of the sol. */
export interface RadiationSolDetail extends RadiationSol {
  rover: string;
  credit: string;
  citation: string;
  /** `null`: hours not computed for this sol or the observation times contradict each other. */
  hours: RadiationHour[] | null;
}

export interface RadiationResponse {
  rover: string;
  credit: string;
  citation: string;
  source_url: string;
  updated_at: string | null;
  source_max_sol: number | null;
  /** Date of the last archive release (UTC date). */
  source_release: string | null;
  items: RadiationSol[];
}

// ---- Weather by sol (stage 17): `GET /rovers/{rover}/weather` and `/weather/{sol}` ----

/** Smallest, largest and mean value of one parameter over a sol. */
export interface WeatherStat {
  min?: number | null;
  max?: number | null;
  mean?: number | null;
}

/** UV has only `max` and `mean`. */
export interface WeatherUv {
  max: number | null;
  mean: number | null;
}

export interface WeatherWind {
  speed_mean_ms: number | null;
  gust_ms: number | null;
  direction_mode_deg: number | null;
}

export interface WeatherFlag {
  /** `partial_coverage`, `humidity_over_100`, `out_of_range`; the list grows, so unknown codes occur. */
  code: string;
  /** The parameter key the flag is about, or null (`partial_coverage`). */
  parameter: string | null;
  count: number | null;
}

/**
 * One sol. Parameter keys depend on the rover and are the same for all its sols: a key that is
 * absent means the rover has no such sensor, `null` means the sensor exists but the sol has no data.
 */
export interface WeatherSol {
  sol: number;
  earth_date: string;
  ls: number | null;
  coverage: number;
  hours_covered: number;
  air_temp_c?: WeatherStat | null;
  pressure_pa?: WeatherStat | null;
  humidity_pct?: WeatherStat | null;
  ground_temp_c?: WeatherStat | null;
  uv_wm2?: WeatherUv | null;
  wind?: WeatherWind | null;
  flags: WeatherFlag[];
  source_version?: Record<string, string>;
}

/** What a parameter is: its unit and how far to trust it (`measured`, `preliminary`, `estimate`). */
export interface WeatherParameter {
  unit: string;
  quality: string;
}

export interface WeatherResponse {
  rover: string;
  credit: string;
  source_url: string;
  updated_at: string | null;
  source_max_sol: number | null;
  parameters: Record<string, WeatherParameter>;
  items: WeatherSol[];
}

/**
 * One local mean solar time hour of `GET /weather/{sol}`. A key without a suffix is the hourly mean;
 * `_min` / `_max` are the lowest / highest valid value of the hour (stage 22; absent on servers that
 * have not re-parsed the archive yet). A missing measurement is `null`. Keys follow the rover.
 */
export interface WeatherHour {
  hour: number;
  coverage: number;
  air_temp_c?: number | null;
  air_temp_c_min?: number | null;
  air_temp_c_max?: number | null;
  pressure_pa?: number | null;
  pressure_pa_min?: number | null;
  pressure_pa_max?: number | null;
  humidity_pct?: number | null;
  humidity_pct_min?: number | null;
  humidity_pct_max?: number | null;
  ground_temp_c?: number | null;
  ground_temp_c_min?: number | null;
  ground_temp_c_max?: number | null;
  uv_wm2?: number | null;
  uv_wm2_min?: number | null;
  uv_wm2_max?: number | null;
  wind_speed_ms?: number | null;
  /** 99th percentile of the hour's wind speeds (Perseverance). */
  wind_gust_ms?: number | null;
  wind_direction_deg?: number | null;
}

export interface WeatherSolDetail extends WeatherSol {
  rover: string;
  /** Credit line of the data, same as in the list; always present. */
  credit: string;
  hours: WeatherHour[];
  source: { files: { product: string; version: string; file: string; link: string }[] };
}

/** API keys (stage 20B). */
export interface KeyInfo {
  id: number;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** `202` of `POST /keys/requests` and `POST /account/deletion-requests`; same for any address. */
export interface MailAccepted {
  status: "accepted";
  mail_delayed: boolean;
}

/** `201` of `POST /keys/confirm`. */
export interface IssuedKey {
  key: string;
  key_info: KeyInfo;
  account: { email: string; created_at: string };
}

/** `201` of `POST /account/keys`. */
export interface NewKey {
  key: string;
  key_info: KeyInfo;
}

export interface AccountInfo {
  email: string;
  status: string;
  created_at: string;
  keys: KeyInfo[];
  usage: { today: number; last_30_days: number };
  limits: { per_minute: number; per_hour: number; concurrent: number };
}
