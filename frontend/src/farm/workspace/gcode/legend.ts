// Fork (printfarm): the Preview legend's view types, colours and number formats, as OrcaSlicer v2.5.0 has them
// (slic3r/GUI/GCodeViewer.cpp, libvgcode/src/ViewerImpl.cpp and include/ColorRange.hpp, libslic3r/Utils.hpp).
import { MOVE, NO_PA, ROLE, ROLES, SCALE, type Toolpaths } from './parse';

type RGB = [number, number, number];

/** DEFAULT_EXTRUSION_ROLES_COLORS, in ROLES order. */
export const ROLE_COLORS: RGB[] = [
  [230, 179, 179], [255, 230, 77], [255, 125, 56], [31, 31, 255], [176, 48, 41], [150, 84, 204], [240, 64, 64],
  [255, 140, 105], [77, 128, 186], [255, 255, 255], [0, 135, 110], [0, 255, 0], [0, 128, 0], [179, 227, 171],
  [94, 209, 148], [102, 92, 199], [77, 128, 186], [0, 59, 110], [0, 64, 0], [128, 128, 128],
];

/** The options shown in the legend (GCodeViewer::update_by_mode), with DEFAULT_OPTIONS_COLORS. */
export const OPTIONS = [
  { key: 'travel', move: MOVE.travel, label: 'Travel', color: [56, 72, 155] as RGB },
  { key: 'retract', move: MOVE.retract, label: 'Retract', color: [205, 34, 214] as RGB },
  { key: 'unretract', move: MOVE.unretract, label: 'Unretract', color: [73, 173, 207] as RGB },
  { key: 'wipe', move: MOVE.wipe, label: 'Wipe', color: [255, 255, 0] as RGB },
  { key: 'seam', move: MOVE.seam, label: 'Seams', color: [230, 230, 230] as RGB },
] as const;
export type OptionKey = (typeof OPTIONS)[number]['key'];

/** DEFAULT_RANGES_COLORS, from blue (low) to red (high). */
export const RANGE_COLORS: RGB[] = [
  [11, 44, 122], [19, 89, 133], [28, 136, 145], [4, 214, 15], [170, 242, 0], [252, 249, 3], [245, 206, 10],
  [227, 136, 32], [209, 104, 48], [194, 82, 60], [148, 38, 22],
];

/** The view type combo, in GCodeViewer::update_by_mode's order; ``phase`` marks the ones not available yet. */
export const VIEW_TYPES = [
  { key: 'summary', label: 'Summary' },
  { key: 'feature', label: 'Line Type' },
  { key: 'filament', label: 'Filament' },
  { key: 'speed', label: 'Speed', title: 'Speed (mm/s)', decimals: 0 },
  { key: 'actualSpeed', label: 'Actual Speed', phase: 4 },
  { key: 'acceleration', label: 'Acceleration', title: 'Acceleration (mm/s²)', decimals: 0 },
  { key: 'jerk', label: 'Jerk', title: 'Jerk (mm/s)', decimals: 1 },
  { key: 'height', label: 'Layer Height', title: 'Layer height (mm)', decimals: 2 },
  { key: 'width', label: 'Line Width', title: 'Line width (mm)', decimals: 2 },
  { key: 'flow', label: 'Flow', title: 'Volumetric flow rate (mm³/s)', decimals: 2 },
  { key: 'actualFlow', label: 'Actual Flow', phase: 4 },
  { key: 'layerTime', label: 'Layer Time', title: 'Layer Time', decimals: 1 },
  { key: 'layerTimeLog', label: 'Layer Time (log)', title: 'Layer Time (log)', decimals: 1 },
  { key: 'fan', label: 'Fan Speed', title: 'Fan speed (%)', decimals: 0 },
  { key: 'temperature', label: 'Temperature', title: 'Temperature (℃)', decimals: 0 },
  { key: 'pressureAdvance', label: 'Pressure Advance', title: 'Pressure Advance', decimals: 3 },
] as const;
export type ViewType = (typeof VIEW_TYPES)[number]['key'];
export type RangeView = Extract<(typeof VIEW_TYPES)[number], { title: string }>['key'];

/** Speed-like views colour travels and wipes by value too (ViewerImpl::get_vertex_color). */
export const MOTION_VIEWS: ViewType[] = ['speed', 'acceleration', 'jerk'];

/** Orca's automatic default: Filament for a multi-material plate, else Line Type (apply_default_view_type). */
export function defaultViewType(tp: Toolpaths): ViewType {
  return tp.stats.usedExtruders.length > 1 ? 'filament' : 'feature';
}

/** libvgcode's round_to_bin: two significant digits for small values, so ranges don't list float noise. */
export function roundToBin(value: number): number {
  const scale = [100, 1000, 10000, 100000, 1000000];
  const threshold = [0.095, 0.0095, 0.00095, 0.000095, 0.0000095];
  let i = 0;
  while (i < 4 && value < threshold[i]) i++;
  return Math.round(value * scale[i]) / scale[i];
}

/** libvgcode's ColorRange: the min and max of a value, a palette, and how many distinct values it saw. */
export class ColorRange {
  min = Infinity;
  max = -Infinity;
  count = 0;
  readonly log: boolean;
  constructor(log = false) {
    this.log = log;
  }
  update(v: number) {
    if (v !== this.min && v !== this.max) this.count++;
    this.min = Math.min(this.min, v);
    this.max = Math.max(this.max, v);
  }
  private step(): number {
    const n = RANGE_COLORS.length - 1;
    if (this.log) return this.min > 0 ? (Math.log(this.max) - Math.log(this.min)) / n : 0;
    return (this.max - this.min) / n;
  }
  color(value: number): RGB {
    const v = Math.min(Math.max(value, this.min), this.max);
    const step = this.step();
    let t = 0;
    if (step > 0) t = this.log ? (this.min !== 0 ? Math.log(v / this.min) / step : 0) : (v - this.min) / step;
    const last = RANGE_COLORS.length - 1;
    const lo = Math.min(Math.max(Math.floor(t), 0), last);
    const hi = Math.min(lo + 1, last);
    const k = t - lo;
    const a = RANGE_COLORS[lo], b = RANGE_COLORS[hi];
    return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
  }
  /** The legend's rows, top (highest) first, as GCodeViewer's append_range lists them. */
  rows(): { color: RGB; value: number }[] {
    if (this.count === 0) return [];
    if (this.count === 1) return [{ color: RANGE_COLORS[0], value: this.min }];
    if (this.count === 2)
      return [
        { color: RANGE_COLORS[RANGE_COLORS.length - 1], value: this.max },
        { color: RANGE_COLORS[0], value: this.min },
      ];
    const step = this.step();
    return RANGE_COLORS.map((color, i) => ({
      color,
      value: this.log ? Math.exp(Math.log(this.min) + i * step) : this.min + i * step,
    })).reverse();
  }
}

/** The value a range view colours move ``i`` (on layer ``layer``) by. */
export function valueOf(tp: Toolpaths, view: RangeView, i: number, layer: number): number {
  switch (view) {
    case 'speed': return tp.feedrate[i] / SCALE.feedrate;
    case 'acceleration': return tp.acceleration[i];
    case 'jerk': return tp.jerk[i] / SCALE.jerk;
    case 'height': return roundToBin(tp.height[i] / SCALE.height);
    case 'width': return roundToBin(tp.width[i] / SCALE.width);
    case 'flow': return roundToBin((tp.feedrate[i] / SCALE.feedrate) * (tp.flow[i] / SCALE.flow));
    case 'layerTime':
    case 'layerTimeLog': return tp.layerTime[layer];
    case 'fan': return roundToBin((tp.fan[i] * 100) / 255);
    case 'temperature': return roundToBin(tp.temperature[i] / SCALE.temperature);
    case 'pressureAdvance': return tp.pressureAdvance[i] / SCALE.pressureAdvance;
  }
}

/** The range of a view over the moves it colours (ViewerImpl::update_color_ranges). */
export function rangeOf(tp: Toolpaths, view: RangeView, visibleRoles: boolean[], options: Record<OptionKey, boolean>): ColorRange {
  const range = new ColorRange(view === 'layerTimeLog');
  if (view === 'layerTime' || view === 'layerTimeLog') {
    for (const t of tp.layerTime) range.update(t);
    return range;
  }
  const motion = MOTION_VIEWS.includes(view);
  for (let i = 0; i < tp.count; i++) {
    const type = tp.type[i];
    if (type === MOVE.extrude) {
      if ((view === 'width' || view === 'flow') && tp.role[i] === ROLE.Custom && !visibleRoles[ROLE.Custom]) continue;
      if (view === 'pressureAdvance' && tp.pressureAdvance[i] === NO_PA) continue;
      range.update(valueOf(tp, view, i, 0));
    } else if (motion && ((type === MOVE.travel && options.travel) || (type === MOVE.wipe && options.wipe))) {
      range.update(valueOf(tp, view, i, 0));
    }
  }
  return range;
}

// ---- number formats ----

/** Utils.hpp get_time_dhms + short_time: "1d2h3m", "2h5m", "4m25s", "12s", "<1s". */
export function shortTime(seconds: number): string {
  let s = seconds;
  const d = Math.floor(s / 86400);
  s -= d * 86400;
  let h = Math.floor(s / 3600);
  s -= h * 3600;
  let m = Math.floor(s / 60);
  s -= m * 60;
  let days = d;
  const secs = Math.floor(s);
  if (days + h > 0 && secs >= 30 && ++m === 60) {
    m = 0;
    if (++h === 24) {
      h = 0;
      days++;
    }
  }
  if (days > 0) return `${days}d${h}h${m}m`;
  if (h > 0) return `${h}h${m}m`;
  if (m > 0) return `${m}m${secs}s`;
  if (secs >= 1) return `${secs}s`;
  return seconds > 0 ? '<1s' : '0s';
}

/** GCodeViewer.cpp format_compact_weight: "12.34g", "1.20kg". */
export function compactWeight(grams: number): string {
  const abs = Math.abs(grams);
  const [v, unit] = abs >= 1e6 ? [abs / 1e6, 't'] : abs >= 1000 ? [abs / 1000, 'kg'] : [abs, 'g'];
  return `${grams < 0 ? '-' : ''}${v.toFixed(2)}${unit}`;
}

/** format_distance: "850mm", "12.34m". */
export function distance(mm: number): string {
  return Math.abs(mm) < 1000 ? `${mm.toFixed(0)}mm` : `${(mm / 1000).toFixed(2)}m`;
}

/** format_compact_count: 950, "1.2K", "3M". */
export function compactCount(n: number): string {
  if (n < 1000) return String(n);
  const suffix = ['', 'K', 'M', 'B', 'T'];
  let i = 0;
  let div = 1;
  while (i + 1 < suffix.length && n / div >= 1000) {
    div *= 1000;
    i++;
  }
  const whole = Math.floor(n / div);
  const tenths = Math.floor(((n % div) * 10) / div);
  return `${whole}${tenths ? `.${tenths}` : ''}${suffix[i]}`;
}

/** format_percent: "0", "<0.1", "12.3". */
export function percent(fraction: number): string {
  if (fraction === 0) return '0';
  return fraction > 0.001 ? (fraction * 100).toFixed(1) : '<0.1';
}

export const rgb = (c: RGB) => `rgb(${Math.round(c[0])}, ${Math.round(c[1])}, ${Math.round(c[2])})`;
export const hexToRgb = (hex: string): RGB => {
  const h = hex.replace('#', '');
  const n = Number.parseInt(h.length >= 6 ? h.slice(0, 6) : 'cccccc', 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

/** Filament length (m) and weight (g) of a volume, as get_used_filament_from_volume. */
export function filamentOf(tp: Toolpaths, mm3: number, slot: number): { m: number; g: number } {
  const d = tp.stats.diameters[slot] ?? tp.stats.diameters[0] ?? 1.75;
  const density = tp.stats.densities[slot] ?? tp.stats.densities[0] ?? 1.24;
  return { m: mm3 / (Math.PI * (d / 2) ** 2) / 1000, g: (mm3 / 1000) * density };
}

/** Roles present in the G-code, in the legend's order. */
export function presentRoles(tp: Toolpaths): number[] {
  const seen = new Set<number>();
  for (let i = 0; i < tp.count; i++) if (tp.type[i] === MOVE.extrude) seen.add(tp.role[i]);
  return ROLES.map((_, i) => i).filter((i) => seen.has(i));
}

/** The colour of each move for a view type (ViewerImpl::get_vertex_color), written into ``out`` (RGBA per move). */
export function paint(tp: Toolpaths, view: ViewType, range: ColorRange | null, colours: string[], out: Uint8Array) {
  const travel = OPTIONS[0].color, wipe = OPTIONS[3].color;
  const filament = colours.map(hexToRgb);
  const grey: RGB = [128, 128, 128];
  const motion = MOTION_VIEWS.includes(view);
  let layer = 0;
  for (let i = 0; i < tp.count; i++) {
    while (i >= tp.layerStart[layer + 1]) layer++;
    const type = tp.type[i];
    let c: RGB;
    if (!motion && type === MOVE.wipe) c = wipe;
    else if (!motion && type === MOVE.travel) c = travel;
    else if (view === 'feature') c = ROLE_COLORS[tp.role[i]];
    else if (view === 'filament' || view === 'summary') c = filament[tp.extruder[i]] ?? grey;
    else c = range ? range.color(valueOf(tp, view as RangeView, i, layer)) : grey;
    out[4 * i] = c[0];
    out[4 * i + 1] = c[1];
    out[4 * i + 2] = c[2];
    out[4 * i + 3] = 255;
  }
}
