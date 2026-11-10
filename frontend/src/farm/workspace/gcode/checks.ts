// Fork (printfarm): OrcaSlicer's checks of a slice against the printer's bed (GLCanvas3D::_set_warning_notification
// _if_needed, BuildVolume::all_paths_inside; v2.5.0): a path above the max print height, a path beyond the plate's
// edges (only when the height is fine, as Orca does), and the prime tower beyond the plate. Texts are Orca's msgids.
import type { Toolpaths } from './parse';

export interface SliceWarning {
  key: 'height' | 'outside' | 'tower';
  /** OrcaSlicer's text (its msgid). */
  text: string;
  /** A slicing error (red) rather than a warning. */
  error: boolean;
}

/** BuildVolume::BedEpsilon */
const EPSILON = 3e-4;

/** The bed's outline from ``printable_area`` ("0x0,256x0,256x256,0x256"). */
export function bedOutline(tp: Toolpaths): [number, number][] {
  return (tp.config.printable_area ?? '')
    .split(',')
    .map((p) => p.trim().split('x').map(Number))
    .filter((p): p is [number, number] => p.length === 2 && p.every(Number.isFinite));
}

function inside(poly: [number, number][], x: number, y: number): boolean {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

/** Whether the box's footprint is on the bed: the outline's bounds for a rectangle (as Orca), else its corners. */
function onBed(poly: [number, number][], box: number[]): boolean {
  const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const rect = poly.length === 4 && poly.every(([x, y]) => (x === x0 || x === x1) && (y === y0 || y === y1));
  if (rect) return box[0] >= x0 - EPSILON && box[1] >= y0 - EPSILON && box[3] <= x1 + EPSILON && box[4] <= y1 + EPSILON;
  // shortcut: a custom bed is checked at the paths' bounding-box corners, not path by path; enough for convex beds
  return [[box[0], box[1]], [box[3], box[1]], [box[3], box[4]], [box[0], box[4]]].every(([x, y]) => inside(poly, x, y));
}

export function sliceWarnings(tp: Toolpaths): SliceWarning[] {
  const out: SliceWarning[] = [];
  const poly = bedOutline(tp);
  const height = Number.parseFloat(tp.config.printable_height ?? '') || 0;
  const top = Math.max(0, ...tp.layerZ);
  const tooHigh = height > 0 && top - height >= 1e-6;
  if (tooHigh) out.push({ key: 'height', text: 'A G-code path goes beyond the max print height.', error: true });
  const box = tp.stats.pathBox;
  if (!tooHigh && poly.length >= 3 && box && !onBed(poly, box))
    out.push({ key: 'outside', text: 'A G-code path goes beyond plate boundaries.', error: true });
  const tower = tp.stats.towerBox;
  if (poly.length >= 3 && tower && !onBed(poly, tower))
    out.push({ key: 'tower', text: 'The prime tower extends beyond the plate boundary.', error: false });
  return out;
}
