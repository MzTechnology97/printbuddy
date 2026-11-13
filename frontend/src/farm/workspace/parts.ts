// Fork (printfarm): OrcaSlicer's operations on a part (GUI_Factories.cpp part menu; GUI_ObjectList.cpp
// del_subobject_from_object, set_volume_type; Model.cpp delete_volume, sort_volumes): Delete, Change Type and
// Precise Seam Type, kept as the draft's ordered list the copy applies (backend farm/workspace/parts.py). Pure
// functions, tested apart from the UI.
import type { WorkspaceObject } from './api';

export const PART_TYPES = ['normal_part', 'negative_part', 'modifier_part', 'support_blocker', 'support_enforcer'] as const;
export const SEAM_TYPES = [
  'precise_seam_center',
  'precise_seam_left',
  'precise_seam_right',
  'precise_seam_enforced',
  'precise_seam_blocked',
  'precise_seam_neutral',
] as const;
export type PartType = (typeof PART_TYPES)[number] | (typeof SEAM_TYPES)[number];

/** A part deleted, or of another type; the draft keeps them in order. */
export interface PartOp {
  op: 'delete' | 'type';
  object_id: string;
  part_id: string;
  type?: PartType;
}

/** Change Type's entries (append_menu_item_change_type): "Precise Seam" stands for every seam type. */
export const TYPE_ENTRIES: { type: PartType; label: string }[] = [
  { type: 'normal_part', label: 'Part' },
  { type: 'negative_part', label: 'Negative Part' },
  { type: 'modifier_part', label: 'Modifier' },
  { type: 'support_blocker', label: 'Support Blocker' },
  { type: 'support_enforcer', label: 'Support Enforcer' },
  { type: 'precise_seam_center', label: 'Precise Seam' },
];
/** Precise Seam Type's entries (append_menu_item_precise_seam_submenu). */
export const SEAM_ENTRIES: { type: PartType; label: string }[] = [
  { type: 'precise_seam_center', label: 'Seam Center' },
  { type: 'precise_seam_left', label: 'Seam Left' },
  { type: 'precise_seam_right', label: 'Seam Right' },
  { type: 'precise_seam_enforced', label: 'Seam Enforced' },
  { type: 'precise_seam_blocked', label: 'Seam Blocked' },
  { type: 'precise_seam_neutral', label: 'Seam Neutral' },
];

export const isSeam = (type: string | null | undefined): boolean => !!type && type.startsWith('precise_seam_');
const typeOf = (part: WorkspaceObject['parts'][number]): string => part.type ?? 'normal_part';

/** sort_volumes(true)'s order: the type's place, a seam by its group (strong before weak). */
function group(type: string): number {
  const seam = SEAM_TYPES.indexOf(type as (typeof SEAM_TYPES)[number]);
  if (seam >= 0) return PART_TYPES.length + (seam < 3 ? 0 : 1);
  return Math.max(0, PART_TYPES.indexOf(type as (typeof PART_TYPES)[number]));
}

/** The type Change Type's entry gives: its "Precise Seam" keeps a seam's own subtype (preserve_ps_subtype). */
export const changeTypeTarget = (current: string, entry: PartType): PartType =>
  entry === 'precise_seam_center' && isSeam(current) ? (current as PartType) : entry;

/** Orca's message when it refuses the operation (the last solid part can't go or change), else null. */
export function refusal(obj: WorkspaceObject, op: PartOp): string | null {
  const part = obj.parts.find((p) => p.id === op.part_id);
  if (!part || typeOf(part) !== 'normal_part' || obj.parts.filter((p) => typeOf(p) === 'normal_part').length > 1) return null;
  if (op.op === 'delete') return 'Deleting the last solid part is not allowed.';
  return op.type !== 'normal_part' ? 'The type of the last solid object part cannot be changed.' : null;
}

/** The objects as the part operations leave them: parts gone or retyped and sorted as Orca sorts them; one part left,
 * its own settings and filament the object's; a seam's settings dormant (not listed). */
export function withPartOps(objects: WorkspaceObject[], ops: PartOp[]): WorkspaceObject[] {
  if (!ops.length) return objects;
  return objects.map((obj) => {
    const mine = ops.filter((o) => o.object_id === obj.id);
    if (!mine.length) return obj;
    let parts = [...obj.parts];
    let overrides = [...obj.overrides];
    let extruder = obj.extruder;
    for (const op of mine) {
      const i = parts.findIndex((p) => p.id === op.part_id);
      if (i < 0) continue;
      if (op.op === 'delete') {
        parts.splice(i, 1);
        overrides = overrides.filter((o) => o.part_id !== op.part_id);
        if (parts.length === 1) {
          // del_subobject_from_object: the last part's settings the object's (over its own), its filament too
          const last = parts[0];
          const own = overrides.filter((o) => o.part_id === last.id);
          const keys = new Set(own.map((o) => o.key));
          overrides = [
            ...overrides.filter((o) => o.part_id !== last.id && !(o.part_id == null && keys.has(o.key))),
            ...own.map((o) => ({ ...o, part: null, part_id: null })),
          ];
          if (last.extruder && last.extruder !== '0') extruder = last.extruder;
          parts = [{ ...last, extruder: null }];
        }
        continue;
      }
      const old = typeOf(parts[i]);
      const next = op.type ?? old;
      if (next === old) continue;
      parts[i] = { ...parts[i], type: next };
      // a part changing group goes last of its new one (move_volume_to_end), then the stable sort
      if (isSeam(old) !== isSeam(next) || (isSeam(old) && group(old) !== group(next))) parts.push(...parts.splice(i, 1));
      parts = [...parts].sort((a, b) => group(typeOf(a)) - group(typeOf(b)));
    }
    const seams = new Set(parts.filter((p) => isSeam(typeOf(p))).map((p) => p.id));
    overrides = overrides.filter((o) => !(o.part_id && seams.has(o.part_id)));
    return { ...obj, parts, overrides, extruder };
  });
}

/** The part ids the operations delete, by object. */
export function deletedParts(ops: PartOp[]): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  for (const op of ops) if (op.op === 'delete') out.set(op.object_id, new Set([...(out.get(op.object_id) ?? []), op.part_id]));
  return out;
}

/** PartPlate::get_center_origin of plate ``index`` (from 0) among ``count``, in the slicer's X / Y: its bed's middle,
 * the plates laid as PartPlateList does (columns of round(sqrt(n)), each 1.2 beds from the last, rows to the front). */
export function plateCenter(index: number, count: number, area: string[] | string | undefined): [number, number] | null {
  const corners = (Array.isArray(area) ? area : area ? [area] : []).map((c) => c.split('x').map(Number));
  if (!corners.length || corners.some((c) => c.length !== 2 || c.some((n) => !Number.isFinite(n)))) return null;
  const xs = corners.map((c) => c[0]);
  const ys = corners.map((c) => c[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const root = Math.sqrt(count);
  const cols = Math.max(1, root > Math.round(root) ? Math.round(root) + 1 : Math.round(root));
  return [(index % cols) * x1 * 1.2 + (x0 + x1) / 2, -Math.floor(index / cols) * y1 * 1.2 + (y0 + y1) / 2];
}
