// Object shapes of API v2 (docs/API.md).

export interface Rover {
  id: string;
  name: string;
  mission: string;
  launch_date: string;
  landing_date: string;
  status: string;
  max_sol: number | null;
  max_date: string | null;
  total_photos: number;
  cameras_count: number;
  last_synced_at: string | null;
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
}

export interface PhotoDetail extends Photo {
  caption: string | null;
  received_at: string | null;
  site: number | null;
  drive: number | null;
  extra: Record<string, unknown>;
}

export interface PhotoPage {
  items: Photo[];
  page: number;
  per_page: number;
  total: number;
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
  page?: number;
  per_page?: number;
}

export type ImageSize = "small" | "medium" | "large" | "full";

/** Sample types accepted by the `sample_type` filter. */
export const FILTER_SAMPLE_TYPES = ["full", "subframe", "downsampled"] as const;
