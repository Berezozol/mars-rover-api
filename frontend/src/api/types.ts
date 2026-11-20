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
  local_time?: LocalTime | null;
  in_sol?: InSol | null;
  /** Ingenuity only: number of the flight made on the sol of the photo, or null. Always null for other rovers. */
  flight?: number | null;
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
