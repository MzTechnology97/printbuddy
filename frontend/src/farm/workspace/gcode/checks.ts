// Fork (printfarm): OrcaSlicer's checks of a slice against the printer's bed (GLCanvas3D::_set_warning_notification
// _if_needed, BuildVolume::all_paths_inside; v2.5.0): a path above the max print height, a path beyond the plate's
// edges (only when the height is fine, as Orca does), and the prime tower beyond the plate. On a printer with more
// than one nozzle (H2D, X2D), each filament's paths against its nozzle's area and height
// (GCodeProcessor::check_multi_extruder_gcode_valid). Texts are Orca's msgids.
import type { NozzlePath, Toolpaths } from './parse';

/** One line of a notification: an Orca format string (``%1%`` the filaments, ``%2%`` the nozzle). */
export interface WarningLine {
  text: string;
  filaments?: string;
  /** A msgid: "left nozzle", "right nozzle", or "Tool %d" with ``tool``. */
  nozzle?: string;
  tool?: number;
}

export interface SliceWarning {
  key: 'height' | 'outside' | 'tower' | 'nozzleArea' | 'nozzleHeight';
  /** OrcaSlicer's text (its msgid); with ``lines``, the first line's. */
  text: string;
  lines?: WarningLine[];
  /** A slicing error (red) rather than a warning. */
  error: boolean;
}

/** A warning in the UI language (``o``: an OrcaSlicer string translated). */
export function warningText(w: SliceWarning, o: (text: string) => string): string {
  if (!w.lines) return o(w.text);
  const nozzle = (l: WarningLine) => (l.nozzle ? o(l.nozzle).replace('%d', String(l.tool ?? '')) : '');
  return w.lines.map((l) => o(l.text).replaceAll('%1%', l.filaments ?? '').replaceAll('%2%', nozzle(l))).join('\n');
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

type Rect = [number, number, number, number];

/** ``extruder_printable_area`` per nozzle ("0x0,325x0,325x320,0x320#25x0,350x0,350x320,25x320"). */
function nozzleAreas(tp: Toolpaths): [number, number][][] {
  return (tp.config.extruder_printable_area ?? '')
    .split('#')
    .map((a) => a.split(',').map((p) => p.trim().split('x').map(Number)).filter((p): p is [number, number] => p.length === 2 && p.every(Number.isFinite)));
}

const numbers = (v: string | undefined) => (v ?? '').split(',').map((x) => Number.parseFloat(x)).filter(Number.isFinite);

/** Whether a path's box reaches the part of the bed its nozzle can't print (the bed less the nozzle's area). The
 * box is grown by SCALED_EPSILON so touching that part counts, as Orca. */
function reachesUnprintable(bed: Rect, area: Rect, box: Rect): boolean {
  const e = 1e-4;
  const x0 = Math.max(box[0] - e, bed[0]), y0 = Math.max(box[1] - e, bed[1]);
  const x1 = Math.min(box[2] + e, bed[2]), y1 = Math.min(box[3] + e, bed[3]);
  if (x0 >= x1 || y0 >= y1) return false; // off the bed: that is the plate check's
  return x0 < area[0] || y0 < area[1] || x1 > area[2] || y1 > area[3];
}

const NOZZLE_NAMES = ['left nozzle', 'right nozzle'];
const AREA = [
  'Filament %1% is placed in the %2%, but the generated G-code path exceeds the printable range of the %2%.',
  'Filaments %1% are placed in the %2%, but the generated G-code path exceeds the printable range of the %2%.',
];
const HEIGHT = [
  'Filament %1% is placed in the %2%, but the generated G-code path exceeds the printable height of the %2%.',
  'Filaments %1% are placed in the %2%, but the generated G-code path exceeds the printable height of the %2%.',
];

/** GCodeProcessor::check_multi_extruder_gcode_valid and its two notifications (GLCanvas3D, v2.5.0). */
export function nozzleWarnings(tp: Toolpaths): SliceWarning[] {
  if (numbers(tp.config.nozzle_diameter).length < 2) return [];
  const map = numbers(tp.config.filament_map);
  const bed = bedOutline(tp);
  const bedRect = bedRectangle(bed);
  // the plate's box with 2 mm of tolerance; its errors join the nozzle's list, as Orca's print_area_error_infos
  const xs = bed.map((p) => p[0]), ys = bed.map((p) => p[1]);
  const plate: Rect | null = bed.length >= 3 ? [Math.min(...xs) - 2, Math.min(...ys) - 2, Math.max(...xs) + 2, Math.max(...ys) + 2] : null;
  // shortcut: a nozzle's area is checked only when it and the bed are rectangles (every v2.5.0 profile's); another
  // shape would need polygon clipping (Orca's diff and intersection), so no error there rather than a false one
  const areas = nozzleAreas(tp).map(bedRectangle);
  const heights = numbers(tp.config.extruder_printable_height);
  const height = Number.parseFloat(tp.config.printable_height ?? '') || 0;
  const area = new Map<number, number[]>(), high = new Map<number, number[]>();
  const add = (m: Map<number, number[]>, e: number, p: NozzlePath) => m.set(e, [...(m.get(e) ?? []), p.filament]);
  let areaError = false, heightError = false;
  for (const p of tp.stats.nozzlePaths) {
    const e = (map[p.filament] ?? 1) - 1;
    if (e < 0) continue; // a filament map entry of 0 names no nozzle
    const b = p.box;
    if (plate && (b[0] < plate[0] || b[1] < plate[1] || b[2] > plate[2] || b[3] > plate[3])) add(area, e, p);
    if (height > 0 && p.top > height) add(high, e, p);
    const nozzle = areas[e];
    if (bedRect && nozzle && reachesUnprintable(bedRect, nozzle, b)) {
      areaError = true;
      add(area, e, p);
    }
    if (e < heights.length && p.top > heights[e]) {
      heightError = true;
      add(high, e, p);
    }
  }
  // shortcut: Orca names the nozzles left / right for a BBL printer (its preset's vendor), "Tool N" otherwise; the
  // G-code has no vendor, so a "Bambu Lab" printer model stands for it
  const bbl = (tp.config.printer_model ?? '').startsWith('Bambu Lab');
  const sorted = (m: Map<number, number[]>) => [...m.entries()].sort((a, b) => a[0] - b[0]);
  const out: SliceWarning[] = [];
  if (areaError) {
    const lines = sorted(area).map(([e, fs]): WarningLine => ({
      text: AREA[fs.length === 1 ? 0 : 1],
      filaments: fs.map((f) => f + 1).join(', '),
      nozzle: bbl ? (NOZZLE_NAMES[e] ?? 'Tool %d') : 'Tool %d',
      tool: e + 1,
    }));
    out.push({ key: 'nozzleArea', text: lines[0].text, lines, error: true });
  }
  if (heightError) {
    const lines = sorted(high).map(([e, fs]): WarningLine => {
      const ids = [...new Set(fs)].sort((a, b) => a - b);
      return { text: HEIGHT[ids.length === 1 ? 0 : 1], filaments: ids.map((f) => f + 1).join(', '), nozzle: NOZZLE_NAMES[e] ?? 'Tool %d', tool: e + 1 };
    });
    lines.push({ text: 'Open wiki for more information.' });
    out.push({ key: 'nozzleHeight', text: lines[0].text, lines, error: true });
  }
  return out;
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
  return [...out, ...nozzleWarnings(tp)];
}
