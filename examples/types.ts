// Generated from openapi.json - do not edit; regenerate with
// `uv run python -m app.cli openapi-ts --output examples/types.ts`

export interface ApiKeyInfoOut {
  created_at: string;
  id: number;
  last_used_at: string | null;
  prefix: string;
  revoked_at: string | null;
}

export interface CameraOut {
  code: string;
  name: string;
  total_photos: number;
}

export interface CamerasResponse {
  items: CameraOut[];
}

export interface DoseStats {
  max: number;
  mean: number;
  min: number;
}

export interface ErrorBody {
  code: string;
  details?: Record<string, string>[] | null;
  message: string;
}

export interface ErrorResponse {
  error: ErrorBody;
}

export interface FlightOut {
  date: string;
  distance_m: number;
  duration_s: number;
  flight: number;
  from: string;
  max_altitude_m: number;
  max_groundspeed_mps: number;
  sol: number;
  to: string | null;
  total_photos: number;
}

export interface FlightsResponse {
  items: FlightOut[];
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

export interface ImagesOut {
  full: string;
  large: string | null;
  medium: string | null;
  small: string | null;
}

export interface InSolOut {
  newer_id: number | null;
  older_id: number | null;
  position: number | null;
  total: number;
}

export interface IssuedAccountOut {
  created_at: string;
  email: string;
}

export interface IssuedKeyOut {
  account: IssuedAccountOut;
  key: string;
  key_info: ApiKeyInfoOut;
}

export interface LimitsOut {
  concurrent: number;
  per_hour: number;
  per_minute: number;
}

export interface LineStringOut {
  coordinates: number[][];
  type?: "LineString";
}

export interface LocalTimeOut {
  kind: "lmst" | "ltst";
  planet: "mars";
  time: string;
}

export interface LocationResponse {
  available: boolean;
  items: WaypointOut[];
  rover: string;
  source_max_sol: number | null;
  updated_at: string | null;
}

export interface MailRequestOut {
  mail_delayed: boolean;
  status?: "accepted";
}

export interface ManifestOut {
  landing_date: string;
  launch_date: string;
  max_date: string | null;
  max_sol: number | null;
  name: string;
  photos: ManifestSolOut[];
  status: string;
  total_photos: number;
}

export interface ManifestSolOut {
  cameras: string[];
  earth_date: string;
  sol: number;
  total_photos: number;
}

export interface MastOut {
  azimuth: number;
  elevation: number;
}

export interface NearStopOut {
  distance_km: number;
  drive: number;
  lat: number;
  lon: number;
  site: number;
  sol: number;
}

export interface NearestSolsOut {
  after: number | null;
  before: number | null;
}

export interface NewKeyOut {
  key: string;
  key_info: ApiKeyInfoOut;
}

export interface ObservationCounts {
  dropped_invalid: number;
  dropped_low_rate: number;
  dropped_qc: number;
  used: number;
}

export interface OwnAccountOut {
  created_at: string;
  email: string;
  keys: ApiKeyInfoOut[];
  limits: LimitsOut;
  status: "active" | "blocked";
  usage: UsageOut;
}

export interface ParameterInfo {
  quality: "estimate" | "measured" | "preliminary";
  unit: string;
}

export interface PhotoBatchResponse {
  items: app__api__v2__schemas__PhotoOut[];
  missing: number[];
}

export interface PhotoDetailOut {
  camera: app__api__v2__schemas__PhotoCameraOut;
  caption: string | null;
  credit: string | null;
  drive: number | null;
  earth_date: string;
  extra: Record<string, unknown>;
  flight: number | null;
  height: number | null;
  id: number;
  images: ImagesOut;
  img_src: string;
  in_sol: InSolOut;
  local_time: LocalTimeOut | null;
  location: PhotoLocationOut | null;
  mast: MastOut | null;
  received_at: string | null;
  rover: string;
  sample_type: string;
  samples: number[];
  site: number | null;
  sol: number;
  source: SourceOut;
  taken_at: string;
  title: string | null;
  width: number | null;
}

export interface PhotoListResponse {
  items: app__api__v2__schemas__PhotoOut[];
  page: number;
  per_page: number;
  total: number;
}

export interface PhotoLocationOut {
  drive: number;
  elev_geoid: number | null;
  exact: boolean;
  lat: number;
  lon: number;
  site: number;
  sol: number;
}

export interface PhotoResponse {
  photo: app__api__legacy__schemas__PhotoOut;
}

export interface PhotoStatsGroupOut {
  camera: string | null;
  first_date: string;
  first_sol: number;
  last_date: string;
  last_sol: number;
  period: string | null;
  rover: string;
  sols_with_photos: number;
  total_photos: number;
}

export interface PhotoStatsResponse {
  first_date: string | null;
  group_by: "camera" | "month" | "rover" | "year";
  groups: PhotoStatsGroupOut[];
  last_date: string | null;
  sols_with_photos: number;
  total_photos: number;
}

export interface RadiationResponse {
  citation: string;
  credit: string;
  items: RadiationSol[];
  rover: string;
  source_max_sol: number | null;
  source_release: string | null;
  source_url: string;
  updated_at: string | null;
}

export interface RadiationSol {
  count_rate_b_per_s: number;
  coverage: number;
  dose_b_ugy_day: DoseStats;
  dose_e_ugy_day: DoseStats;
  earth_date: string;
  observations: ObservationCounts;
  sol: number;
  source: RadiationSource;
  timing_caveat: boolean | null;
}

export interface RadiationSource {
  file: string;
  link: string;
}

export interface RoverCameraOut {
  full_name: string;
  name: string;
}

export interface RoverResponse {
  rover: app__api__legacy__schemas__RoverOut;
}

export interface SampleLocationOut {
  drive: number;
  elev_geoid: number | null;
  lat: number;
  lon: number;
  match: "by_sol" | "exact" | "nearest_before";
  site: number;
  sol: number;
}

export interface SampleOut {
  current_location: string | null;
  dossier_url: string | null;
  feature_name: string | null;
  height_cm: number | null;
  height_note: string | null;
  location: SampleLocationOut | null;
  name: string;
  number: number;
  rock_type: string | null;
  sample_type: ("atmospheric" | "regolith" | "rock_core") | null;
  sample_type_label: string | null;
  sampling_location: string | null;
  sealed_on: string | null;
  sol: number | null;
  status: ("depot" | "on_rover") | null;
  total_photos: number | null;
}

export interface SamplesResponse {
  items: SampleOut[];
  rover: string;
  source_updated_at: string | null;
  source_url: string;
  updated_at: string | null;
}

export interface SourceOut {
  id: string;
  link: string | null;
}

export interface Stat3 {
  max: number;
  mean: number;
  min: number;
}

export interface TraverseFeatureOut {
  geometry: LineStringOut;
  properties: TraverseProperties;
  type?: "Feature";
}

export interface TraverseProperties {
  length_m: number | null;
  sol: number | null;
}

export interface TraverseResponse {
  available: boolean;
  features: TraverseFeatureOut[];
  rover: string;
  source_max_sol: number | null;
  type?: "FeatureCollection";
  updated_at: string | null;
}

export interface UsageOut {
  last_30_days: number;
  today: number;
}

export interface UvStats {
  max: number;
  mean: number;
}

export interface ValidationError {
  ctx?: Record<string, unknown>;
  input?: unknown;
  loc: (number | string)[];
  msg: string;
  type: string;
}

export interface WaypointOut {
  dist_m: number | null;
  dist_total_m: number | null;
  drive: number;
  easting: number | null;
  elev_geoid: number | null;
  lat: number;
  lon: number;
  northing: number | null;
  pitch: number | null;
  roll: number | null;
  site: number;
  sol: number;
  tilt: number | null;
  yaw: number | null;
}

export interface WeatherFlag {
  code: string;
  count: number | null;
  parameter: string | null;
}

export interface WeatherHour {
  air_temp_c: number | null;
  air_temp_c_max: number | null;
  air_temp_c_min: number | null;
  coverage: number;
  ground_temp_c?: number | null;
  ground_temp_c_max?: number | null;
  ground_temp_c_min?: number | null;
  hour: number;
  humidity_pct: number | null;
  humidity_pct_max: number | null;
  humidity_pct_min: number | null;
  pressure_pa: number | null;
  pressure_pa_max: number | null;
  pressure_pa_min: number | null;
  uv_wm2?: number | null;
  uv_wm2_max?: number | null;
  uv_wm2_min?: number | null;
  wind_direction_deg?: number | null;
  wind_gust_ms?: number | null;
  wind_speed_ms?: number | null;
}

export interface WeatherResponse {
  credit: string;
  items: WeatherSol[];
  parameters: Record<string, ParameterInfo>;
  rover: string;
  source_max_sol: number | null;
  source_url: string;
  updated_at: string | null;
}

export interface WeatherSol {
  air_temp_c: Stat3 | null;
  coverage: number;
  earth_date: string;
  flags: WeatherFlag[];
  ground_temp_c?: Stat3 | null;
  hours_covered: number;
  humidity_pct: Stat3 | null;
  ls: number | null;
  pressure_pa: Stat3 | null;
  sol: number;
  source_version: Record<string, string>;
  uv_wm2?: UvStats | null;
  wind?: WindStats | null;
}

export interface WeatherSolDetail {
  air_temp_c: Stat3 | null;
  coverage: number;
  earth_date: string;
  flags: WeatherFlag[];
  ground_temp_c?: Stat3 | null;
  hours: WeatherHour[];
  hours_covered: number;
  humidity_pct: Stat3 | null;
  ls: number | null;
  pressure_pa: Stat3 | null;
  rover: string;
  sol: number;
  source: WeatherSource;
  source_version: Record<string, string>;
  uv_wm2?: UvStats | null;
  wind?: WindStats | null;
}

export interface WeatherSource {
  files: WeatherSourceFile[];
}

export interface WeatherSourceFile {
  file: string;
  link: string;
  product: string;
  version: string;
}

export interface WindStats {
  direction_mode_deg: number | null;
  gust_ms: number;
  speed_mean_ms: number;
}

export interface app__api__legacy__schemas__LatestPhotosResponse {
  latest_photos: app__api__legacy__schemas__PhotoOut[];
}

export interface app__api__legacy__schemas__ManifestResponse {
  photo_manifest: ManifestOut;
}

export interface app__api__legacy__schemas__PhotoCameraOut {
  full_name: string;
  id: number;
  name: string;
  rover_id: number;
}

export interface app__api__legacy__schemas__PhotoOut {
  camera: app__api__legacy__schemas__PhotoCameraOut;
  earth_date: string;
  id: number;
  img_src: string;
  rover: app__api__legacy__schemas__RoverOut;
  sol: number;
}

export interface app__api__legacy__schemas__PhotosResponse {
  photos: app__api__legacy__schemas__PhotoOut[];
}

export interface app__api__legacy__schemas__RoverOut {
  cameras: RoverCameraOut[];
  id: number;
  landing_date: string;
  launch_date: string;
  max_date: string | null;
  max_sol: number | null;
  name: string;
  status: string;
  total_photos: number;
}

export interface app__api__legacy__schemas__RoversResponse {
  rovers: app__api__legacy__schemas__RoverOut[];
}

export interface app__api__v2__schemas__LatestPhotosResponse {
  items: app__api__v2__schemas__PhotoOut[];
  page: number;
  per_page: number;
  sol: number | null;
  total: number;
}

export interface app__api__v2__schemas__ManifestResponse {
  rover: app__api__v2__schemas__RoverOut;
  sols: ManifestSolOut[];
}

export interface app__api__v2__schemas__PhotoCameraOut {
  code: string;
  name: string;
}

export interface app__api__v2__schemas__PhotoOut {
  camera: app__api__v2__schemas__PhotoCameraOut;
  credit: string | null;
  earth_date: string;
  height: number | null;
  id: number;
  images: ImagesOut;
  img_src: string;
  local_time: LocalTimeOut | null;
  mast: MastOut | null;
  near_stop?: NearStopOut;
  rover: string;
  sample_type: string;
  sol: number;
  source: SourceOut;
  taken_at: string;
  title: string | null;
  width: number | null;
}

export interface app__api__v2__schemas__PhotosResponse {
  items: app__api__v2__schemas__PhotoOut[];
  nearest_sols: NearestSolsOut | null;
  page: number;
  per_page: number;
  total: number;
}

export interface app__api__v2__schemas__RoverOut {
  cameras_count: number;
  data_source: "archive" | "sync";
  end_date: string | null;
  id: string;
  landing_date: string;
  last_synced_at: string | null;
  launch_date: string;
  max_date: string | null;
  max_sol: number | null;
  mission: string;
  name: string;
  status: string;
  total_photos: number;
}

export interface app__api__v2__schemas__RoversResponse {
  items: app__api__v2__schemas__RoverOut[];
}
