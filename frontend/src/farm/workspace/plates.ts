// Fork (printfarm): a plate's own settings as OrcaSlicer's Plate Settings dialog and tab set them
// (PlateSettingsDialog.cpp, TabPrintPlate, PartPlate.cpp), as pure functions. They live in the draft with the
// objects' own settings (draft.ts OwnEdits) under ``plate:N``, by their metadata key in model_settings.config, and
// the copy writes them there (farm/workspace/plate_settings.py). A key the plate doesn't hold is "Same as Global".
import type { PlateSettingEdit, WorkspaceDocument, WorkspacePlate, WorkspaceSetting } from './api';
import { type Draft, ON, type OwnEdits } from './draft';
import { type TargetEdits, ownEdit, projectValue, sameSetting } from './objectGrid';
import { instancesOf, ownSettings } from './objects';

export const PLATE_KEYS = [
  'plater_name',
  'locked',
  'bed_type',
  'print_sequence',
  'first_layer_print_sequence',
  'other_layers_print_sequence',
  'spiral_mode',
] as const;
export type PlateKey = (typeof PLATE_KEYS)[number];
/** The dialog's first layer of a range (MIN_LAYER_VALUE) and its "End" (MAX_LAYER_VALUE, INT_MAX - 1). */
export const LAYER_MIN = 2;
export const LAYER_END = 2147483646;

export const plateTarget = (plate: number): string => `plate:${plate}`;
export const isPlateTarget = (target: string): boolean => target.startsWith('plate:');

/** A plate's metadata value as the file has it; null when it holds none (a name: always, maybe empty). */
function savedMeta(plate: WorkspacePlate, key: string): string | null {
  const v = plate.metadata?.[key] ?? (key === 'plater_name' ? (plate.name ?? '') : undefined);
  return v == null || (v === '' && key !== 'plater_name') ? null : v;
}

/** The plate's own settings (metadata key -> value) as the file has them, the unsaved edits made. */
export function plateMeta(plate: WorkspacePlate, edits?: OwnEdits): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of PLATE_KEYS) {
    const value = savedMeta(plate, key);
    if (value != null) out[key] = value;
  }
  for (const [key, value] of Object.entries(edits?.[plateTarget(plate.index)] ?? {})) {
    if (value === null) delete out[key];
    else out[key] = value;
  }
  return out;
}

/** Draft edits giving the plate ``values`` (null: same as global); a value the file already holds is no edit. */
export function plateEdits(plate: WorkspacePlate, values: Partial<Record<PlateKey, string | null>>): TargetEdits {
  return {
    [plateTarget(plate.index)]: Object.fromEntries(Object.entries(values).map(([k, v]) => [k, savedMeta(plate, k) === v ? undefined : v])),
  };
}

/** The plates' edits as the copy saves them. */
export function plateToSave(edits: OwnEdits): PlateSettingEdit[] {
  return Object.entries(edits)
    .filter(([target]) => isPlateTarget(target))
    .flatMap(([target, keys]) => Object.entries(keys).map(([key, value]) => ({ plate: Number(target.slice(6)), key, value })));
}

/** A range of layers and the filaments' order on them (Orca's ``LayerPrintSequence``). */
export interface LayerRange {
  from: number;
  to: number;
  order: number[];
}

/** ``other_layers_print_sequence``: each range's first and last layer and its order, one after the other. */
export function parseRanges(text: string | undefined, count: number | undefined, filaments: number): LayerRange[] {
  const n = (text ?? '').split(/\s+/).filter(Boolean).map(Number);
  const width = count ? n.length / count : filaments + 2;
  if (!n.length || !Number.isInteger(width) || width < 3) return [];
  const out: LayerRange[] = [];
  for (let i = 0; i < n.length; i += width) out.push({ from: n[i], to: n[i + 1], order: n.slice(i + 2, i + width) });
  return out;
}

/** The ranges as the file keeps them, in Orca's order (``LayerSeqInfo::operator<``: by first layer, then last). */
export function formatRanges(ranges: LayerRange[]): string {
  return [...ranges]
    .sort((a, b) => a.from - b.from || a.to - b.to)
    .flatMap((r) => [r.from, r.to, ...r.order])
    .join(' ');
}

/** A filament order from the file, or 1..n (the dialog's start). */
export function orderOf(text: string | undefined, filaments: number): number[] {
  const n = (text ?? '').split(/\s+/).filter(Boolean).map(Number);
  return n.length === filaments ? n : Array.from({ length: filaments }, (_, i) => i + 1);
}

/** OrcaSlicer's Add plate, Remove the current plate (if not last one), Move plate to the front and Duplicate Current
 * Plate, the plates numbered as they are when the op comes. */
/** A duplicate holds the objects the plate held then and where their instances were placed by then (the copy takes
 * the others where the file has them), so a later move leaves the copies where they were, as in Orca. */
export type PlateOp =
  | { op: 'add' }
  | { op: 'remove' | 'front'; plate: number }
  | { op: 'duplicate'; plate: number; objects: string[]; instances: { object_id: string; instance: number; matrix: number[] }[] };
export const MAX_PLATES = 36; // PartPlate.hpp MAX_PLATE_COUNT

/** The plates after ``ops``, numbered again (``source``: the file's plate each one is, null a new one). A duplicate
 * shows its plate as the file has it until the copy is saved, which writes the objects it held (``copies``) as
 * objects of their own; it lists none, so they can't be edited apart before (``duplicate_plate`` copies no plate
 * setting either). */
export function applyPlateOps(plates: WorkspacePlate[], ops: PlateOp[]): WorkspacePlate[] {
  const list = plates.map((p) => ({ ...p, source: p.source === undefined ? p.index : p.source }));
  for (const o of ops) {
    if (o.op === 'add' || o.op === 'duplicate') {
      const source = o.op === 'duplicate' ? (list[o.plate - 1]?.source ?? null) : null;
      const copies = o.op === 'duplicate' && source !== null ? o.objects : undefined;
      list.push({ index: 0, name: null, object_ids: [], instances: [], metadata: { plater_name: '', locked: 'false' }, source, copies });
    } else {
      const [p] = list.splice(o.plate - 1, 1);
      if (o.op === 'front' && p) list.unshift(p);
    }
  }
  return list.map((p, i) => ({ ...p, index: i + 1, metadata: { ...p.metadata, plater_id: String(i + 1) } }));
}

/** The instances moved onto another plate (``objectId:instance`` -> the file's plate it lies on, 0 none) listed there. */
export function withMoved(plates: WorkspacePlate[], onPlate: Record<string, number>): WorkspacePlate[] {
  const keys = Object.keys(onPlate);
  if (!keys.length) return plates;
  const key = (i: { object_id: string; instance_id: string | null }) => `${i.object_id}:${Number(i.instance_id ?? 0)}`;
  return plates.map((p) => {
    const instances = [
      ...p.instances.filter((i) => onPlate[key(i)] === undefined),
      ...keys.filter((k) => onPlate[k] === p.index).map((k) => ({ object_id: k.split(':')[0], instance_id: k.split(':')[1] })),
    ];
    return { ...p, instances, object_ids: [...new Set(instances.map((i) => i.object_id))] };
  });
}

/** OrcaSlicer's notify_instance_update: a moved instance stays on its plate while its box touches it, else goes on
 * the first plate it touches (0: none). Plates as the file lays ``count`` of them out (compute_origin), numbered
 * from 1; the box in the slicer's x / y. Shortcut: the bed from (0, 0) and every height; a bed with its origin
 * elsewhere would need its printable area here. */
export function plateAfterMove(
  box: { min: { x: number; y: number }; max: { x: number; y: number } },
  current: number,
  count: number,
  bed: { x: number; y: number },
): number {
  const root = Math.sqrt(count);
  const cols = root > Math.round(root) ? Math.round(root) + 1 : Math.round(root);
  const EPS = 1e-4; // BuildVolume::SceneEpsilon
  const touches = (n: number) => {
    const x = ((n - 1) % cols) * bed.x * 1.2, y = -Math.floor((n - 1) / cols) * bed.y * 1.2;
    return box.max.x >= x - EPS && box.min.x <= x + bed.x + EPS && box.max.y >= y - EPS && box.min.y <= y + bed.y + EPS;
  };
  if (current > 0 && touches(current)) return current;
  for (let n = 1; n <= count; n++) if (touches(n)) return n;
  return 0;
}

/** The plates' own settings edited (``plate:N``), numbered as the plates are after ``op``; a removed plate's go. */
export function remapPlateEdits(edits: OwnEdits, op: PlateOp): OwnEdits {
  if (op.op === 'add' || op.op === 'duplicate') return edits;
  const out: OwnEdits = {};
  for (const [target, keys] of Object.entries(edits)) {
    if (!isPlateTarget(target)) {
      out[target] = keys;
      continue;
    }
    const n = Number(target.slice(6));
    if (op.op === 'remove') {
      if (n !== op.plate) out[plateTarget(n > op.plate ? n - 1 : n)] = keys;
    } else out[plateTarget(n === op.plate ? 1 : n < op.plate ? n + 1 : n)] = keys;
  }
  return out;
}

// PartPlate::set_vase_mode_related_object_config (timelapse_type is a printer-wide setting: not an object's own)
const VASE: Record<string, string> = {
  wall_loops: '1',
  top_shell_layers: '0',
  sparse_infill_density: '0%',
  enable_support: '0',
  enforce_support_layers: '0',
  detect_thin_wall: '0',
  overhang_reverse: '0',
};

const same = (setting: WorkspaceSetting, a: string, b: string) =>
  setting.type === 'bool' ? ON.has(a.trim()) === ON.has(b.trim()) : sameSetting(a, b);

/** Spiral vase turned on for a plate: each of its objects gets the settings spiral mode needs, where the project's
 * value or its own differs (``set_vase_mode_related_object_config``). */
export function spiralEdits(doc: WorkspaceDocument, plate: number, draft: Draft, edits?: OwnEdits): TargetEdits {
  const where = instancesOf(doc.plates);
  const out: TargetEdits = {};
  for (const obj of doc.objects) {
    if (!where.get(obj.id ?? '')?.some((i) => i.plate === plate)) continue;
    const own = ownSettings(obj, null, edits);
    for (const [key, value] of Object.entries(VASE)) {
      const setting = doc.settings.find((s) => s.key === key);
      if (!setting) continue; // shortcut: a key the project doesn't hold is left to the slicer's default
      if (same(setting, projectValue(setting, draft), value) && (own[key] === undefined || same(setting, own[key], value))) continue;
      for (const [target, keys] of Object.entries(ownEdit({ obj, partId: null, type: null }, key, value, edits))) {
        out[target] = { ...out[target], ...keys };
      }
    }
  }
  return out;
}
