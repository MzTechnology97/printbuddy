// Fork (printfarm): OrcaSlicer's Height Range Modifier (GUI_ObjectList.cpp layers_editing,
// add_layer_range_after_current, edit_layer_range, del_layer_range; GUI_ObjectLayers.cpp's editor): an object's
// height ranges, each with its own layer height and settings. Pure functions, tested apart from the UI.
import type { LayerRange, WorkspaceObject } from './api';
import type { OwnEdits } from './draft';
import { targetOwn } from './objects';

/** The objects' ranges edited: object id -> all its ranges as they are to be (none: it has none any more). */
export type RangeEdits = Record<string, LayerRange[]>;

const EPSILON = 1e-4; // libslic3r's
const RANGE_KEY = (r: LayerRange) => `${r.min_z}|${r.max_z}`;

/** Orca's ranges are a map keyed by (min, max): in that order, one per key. */
const sorted = (ranges: LayerRange[]): LayerRange[] => [...ranges].sort((a, b) => a.min_z - b.min_z || a.max_z - b.max_z);

/** An object's ranges, the unsaved edits made. */
export const rangesOf = (obj: WorkspaceObject, edits?: RangeEdits): LayerRange[] => edits?.[obj.id ?? ''] ?? obj.ranges ?? [];

/** ``edits`` with the object's ranges as ``next`` (dropped when they're as the file has them again). */
export function withRanges(edits: RangeEdits, obj: WorkspaceObject, next: LayerRange[]): RangeEdits {
  const out = { ...edits };
  if (JSON.stringify(next) === JSON.stringify(obj.ranges ?? [])) delete out[obj.id ?? ''];
  else out[obj.id ?? ''] = next;
  return out;
}

/** get_default_layer_config: the object's own layer height, else the process's, and filament 0 (the object's). */
export function defaultOptions(obj: WorkspaceObject, own: OwnEdits | undefined, processLayerHeight: string): Record<string, string> {
  return { layer_height: targetOwn(obj, null, own).layer_height ?? processLayerHeight, extruder: '0' };
}

/** layers_editing on an object without ranges: one from 0 to 2 mm. */
export const firstRanges = (defaults: Record<string, string>): LayerRange[] => [{ min_z: 0, max_z: 2, options: defaults }];

/** add_layer_range_after_current on range ``i``: 2 mm after the last; the next one split in two when it starts
 * where this ends (each part at least its filament's minimum layer height); a gap filled. null: nothing added. */
export function addAfter(
  ranges: LayerRange[],
  i: number,
  defaults: Record<string, string>,
  minLayerHeight: (extruder: number) => number,
): LayerRange[] | null {
  const cur = ranges[i];
  const next = ranges[i + 1];
  if (!cur) return null;
  if (!next) return [...ranges, { min_z: cur.max_z, max_z: cur.max_z + 2, options: defaults }];
  if (cur.max_z > next.min_z) return null;
  if (cur.max_z === next.min_z) {
    const delta = next.max_z - next.min_z;
    const oldMin = minLayerHeight(Number(next.options.extruder ?? 0));
    const newMin = minLayerHeight(0);
    if (delta < oldMin + newMin - EPSILON) return null;
    const middle = newMin > 0.5 * delta ? next.max_z - newMin : next.min_z + Math.max(oldMin, 0.5 * delta);
    return sorted([
      ...ranges.filter((_, k) => k !== i + 1),
      { min_z: cur.max_z, max_z: middle, options: defaults },
      { ...next, min_z: middle },
    ]);
  }
  if (next.min_z - cur.max_z >= minLayerHeight(0) - EPSILON) {
    return sorted([...ranges, { min_z: cur.max_z, max_z: next.min_z, options: defaults }]);
  }
  return null;
}

/** edit_layer_range: range ``i`` moved to ``[min, max]`` with its settings; another range on the same key gives way
 * (Orca's map assignment). */
function moved(ranges: LayerRange[], i: number, min: number, max: number): LayerRange[] {
  const next = { ...ranges[i], min_z: min, max_z: max };
  return sorted([...ranges.filter((r, k) => k !== i && RANGE_KEY(r) !== RANGE_KEY(next)), next]);
}

/** The Z min typed (LayerRangeEditor etMinZ): its end follows when it would be below (half a millimetre after).
 * null: the same value, nothing changes. */
export function editMin(ranges: LayerRange[], i: number, min: number): LayerRange[] | null {
  const r = ranges[i];
  if (!r || Math.abs(min - r.min_z) < EPSILON) return null;
  return moved(ranges, i, min, min < r.max_z ? r.max_z : min + 0.5);
}

/** The Z max typed (etMaxZ): refused below its start. null: nothing changes. */
export function editMax(ranges: LayerRange[], i: number, max: number): LayerRange[] | null {
  const r = ranges[i];
  if (!r || Math.abs(max - r.max_z) < EPSILON || r.min_z > max) return null;
  return moved(ranges, i, r.min_z, max);
}

export const removeRange = (ranges: LayerRange[], i: number): LayerRange[] => ranges.filter((_, k) => k !== i);

/** A range's setting changed (null: no longer its own; a layer height goes back to the default, every range has one). */
export function withOption(ranges: LayerRange[], i: number, key: string, value: string | null, defaults: Record<string, string>): LayerRange[] {
  return ranges.map((r, k) => {
    if (k !== i) return r;
    const options = { ...r.options };
    if (value !== null) options[key] = value;
    else if (key in defaults) options[key] = defaults[key];
    else delete options[key];
    return { ...r, options };
  });
}

/** LayerRangeEditor::get_value: a number of mm, either decimal separator; null for "Invalid numeric." (or below 0). */
export function parseZ(text: string): number | null {
  const s = text.trim().replace(',', '.');
  if (s === '.') return 0;
  const n = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(s) ? Number(s) : NaN;
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** ObjectDataViewModel's label: "Range 0.00-2.00 (mm)" (``range`` and ``mm`` in the UI language). */
export const rangeLabel = (r: LayerRange, range: string, mm: string): string => `${range} ${r.min_z.toFixed(2)}-${r.max_z.toFixed(2)} (${mm})`;

/** The ranges edited, as the copy saves them. */
export const rangesToSave = (edits: RangeEdits) => Object.entries(edits).map(([object_id, ranges]) => ({ object_id, ranges }));
