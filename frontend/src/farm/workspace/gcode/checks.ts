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

/** The bed's rectangle [x0, y0, x1, y1] when ``printable_area`` is one (BuildVolume_Type::Rectangle), else null. */
export function bedRectangle(poly: [number, number][]): [number, number, number, number] | null {
  const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const rect = poly.length === 4 && x1 > x0 && y1 > y0 && poly.every(([x, y]) => (x === x0 || x === x1) && (y === y0 || y === y1));
  return rect ? [x0, y0, x1, y1] : null;
}

/** Whether the paths' box is on a rectangular bed, as BuildVolume::all_paths_inside's bounding-box test. */
function onBed(rect: [number, number, number, number], box: number[]): boolean {
  return box[0] >= rect[0] - EPSILON && box[1] >= rect[1] - EPSILON && box[3] <= rect[2] + EPSILON && box[4] <= rect[3] + EPSILON;
}

export function sliceWarnings(tp: Toolpaths): SliceWarning[] {
  const out: SliceWarning[] = [];
  const poly = bedOutline(tp);
  const height = Number.parseFloat(tp.config.printable_height ?? '') || 0;
  const top = Math.max(0, ...tp.layerZ);
  const tooHigh = height > 0 && top - height >= 1e-6;
  if (tooHigh) out.push({ key: 'height', text: 'A G-code path goes beyond the max print height.', error: true });
  // shortcut: only a rectangular bed is checked; a round or custom one would need every path point tested
  // (Orca's Circle / Convex cases), so no edge warning there rather than a false one
  const rect = bedRectangle(poly);
  const box = tp.stats.pathBox;
  if (!tooHigh && rect && box && !onBed(rect, box))
    out.push({ key: 'outside', text: 'A G-code path goes beyond plate boundaries.', error: true });
  const tower = tp.stats.towerBox;
  if (rect && tower && !onBed(rect, tower))
    out.push({ key: 'tower', text: 'The prime tower extends beyond the plate boundary.', error: false });
  return out;
}
