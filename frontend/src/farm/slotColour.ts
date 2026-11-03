// Printfarm (fork): which loaded slot is the component's colour, by the farm's rule (as dispatch maps it).
import type { LoadedFilament } from '../hooks/useFilamentMapping';
import { filamentTypesCompatible, normalizeColorForCompare } from '../utils/amsHelpers';

/** RGB distance between two colours in any form ('#F75403', 'F75403FF', 'f75403'), as the farm measures it. */
export function colourDistance(a: string, b: string): number {
  const [x, y] = [normalizeColorForCompare(a), normalizeColorForCompare(b)];
  if (x.length < 6 || y.length < 6) return Infinity;
  const rgb = (h: string) => [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [p, q] = [rgb(x), rgb(y)];
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
}

/** The loaded slot of the same material type closest to the component's colour, within the farm's tolerance. */
export function componentSlot(
  type: string | undefined, loaded: LoadedFilament[], colorHex: string, tolerance: number,
): LoadedFilament | null {
  const fits = loaded
    .filter((f) => !type || filamentTypesCompatible(f.type, type))
    .map((f) => ({ f, d: colourDistance(f.color, colorHex) }))
    .filter(({ d }) => d <= tolerance)
    .sort((a, b) => a.d - b.d);
  return fits[0]?.f ?? null;
}
