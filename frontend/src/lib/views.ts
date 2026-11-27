import { SAMPLES_ROVER } from "./samples";

/** The views of one rover (the row of tabs on all its pages). */
export type View = "photos" | "timeline" | "map" | "samples" | "flights";

/** Rovers whose stops are published (the route map exists for these two only). */
const MAP_ROVERS = ["curiosity", "perseverance"];

export const VIEW_LABEL: Record<View, string> = {
  photos: "Photos",
  timeline: "Timeline",
  map: "Route map",
  samples: "Samples",
  flights: "Flights",
};

/** Views a rover has, in display order. A view the rover does not have is not offered. */
export function roverViews(roverId: string): View[] {
  const views: View[] = ["photos", "timeline"];
  if (MAP_ROVERS.includes(roverId)) views.push("map");
  if (roverId === SAMPLES_ROVER) views.push("samples");
  if (roverId === "ingenuity") views.push("flights");
  return views;
}

export function viewHref(roverId: string, view: View): string {
  return view === "photos" ? `/rovers/${roverId}` : `/rovers/${roverId}/${view}`;
}
