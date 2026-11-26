import type { WeatherFlag } from "../api/types";
import { flagClass, flagShort } from "../lib/weather";

export function FlagPill({ f }: { f: WeatherFlag }) {
  return <span class={`flag flag-${flagClass(f)}`}>{flagShort(f)}</span>;
}
