// Fork (printfarm): what the workspace selects (a plate, an object or one of its parts) and the settings each
// has of its own, from the document (model_settings.config objects, parts and plate metadata).
import type { ObjectSettingEdit, WorkspaceObject, WorkspacePlate } from './api';
import type { OwnEdits } from './draft';
import type { OrcaLayout } from './orcaLayout';

export type Selection =
  | { kind: 'plate'; plate: number }
  /** an object; ``instance``: one of its instances (its number from 0); ``part`` / ``partId``: one of its parts */
  | { kind: 'object'; id: string; plate: number | null; part?: string | null; partId?: string | null; instance?: number | null }
  | null;

/** Where each object's instances are: plate and instance number (from 0), in the instances' order. A plate
 * without an instance list counts its objects in order. */
export function instancesOf(plates: WorkspacePlate[]): Map<string, { plate: number; index: number }[]> {
  const out = new Map<string, { plate: number; index: number }[]>();
  for (const p of plates) {
    const list = p.instances.length ? p.instances : p.object_ids.map((object_id) => ({ object_id, instance_id: null }));
    for (const i of list) {
      const mine = out.get(i.object_id) ?? [];
      const n = Number(i.instance_id);
      mine.push({ plate: p.index, index: i.instance_id != null && i.instance_id !== '' && Number.isFinite(n) ? n : mine.length });
      out.set(i.object_id, mine);
    }
  }
  for (const mine of out.values()) mine.sort((a, b) => a.index - b.index);
  return out;
}

/** Where an object's (``partId``: a part's) own settings are kept in the draft (draft.ts ``OwnEdits``). */
export const ownTarget = (objectId: string, partId?: string | null): string => (partId ? `${objectId}/${partId}` : objectId);

/** The part Orca's list doesn't show: an object's one model part, named as the object (its settings show with the
 * object's). */
export function hiddenPart(obj: WorkspaceObject): string | null {
  const [only, ...more] = obj.parts;
  return only && !more.length && (only.type ?? 'normal_part') === 'normal_part' && only.name === obj.name ? only.id : null;
}

/** One target's own settings as the file has them (``partId``: a part's), the unsaved ``edits`` made on them. */
export function targetOwn(obj: WorkspaceObject, partId: string | null | undefined, edits?: OwnEdits): Record<string, string> {
  const saved = obj.overrides.filter((o) => (partId ? o.part_id === partId : !o.part_id)).map((o) => [o.key, o.value]);
  const out: Record<string, string> = Object.fromEntries(saved);
  for (const [key, value] of Object.entries(edits?.[ownTarget(obj.id ?? '', partId)] ?? {})) {
    if (key === FILAMENT_KEY) continue; // the Fila. column's, not a tab's (filamentOf)
    if (value === null) delete out[key];
    else out[key] = value;
  }
  return out;
}

/** An object's (``partId``: a part's) own settings: key -> value, the unsaved edits made. An object's view holds
 * the hidden part's too (``hiddenPart``; ``editTarget`` says whose a key is). */
export function ownSettings(obj: WorkspaceObject, partId?: string | null, edits?: OwnEdits): Record<string, string> {
  if (partId) return targetOwn(obj, partId, edits);
  const hidden = hiddenPart(obj);
  const mine = targetOwn(obj, null, edits);
  const part = hidden ? targetOwn(obj, hidden, edits) : {};
  return { ...mine, ...part }; // the part's value is the one it slices with
}

/** The target an edit of ``key`` goes to in an object's view: the hidden part when it holds the key (its value
 * wins over the object's), else the object. */
export function editTarget(obj: WorkspaceObject, partId: string | null | undefined, key: string, edits?: OwnEdits): string {
  const id = obj.id ?? '';
  if (partId) return ownTarget(id, partId);
  const hidden = hiddenPart(obj);
  return hidden && key in targetOwn(obj, hidden, edits) ? ownTarget(id, hidden) : ownTarget(id);
}

/** An object's (part's) filament in model_settings.config: the list's Fila. column and keys 0-9. */
export const FILAMENT_KEY = 'extruder';
/** The parts that have a filament of their own (ObjectList ``set_extruder_for_selected_items``). */
export const FILAMENT_PARTS = new Set(['normal_part', 'modifier_part']);

/** The filament an object (``partId``: a part) is set to, the unsaved edits made: an object's number, 1 when the
 * file says none; a part's own, 0 when it has none (it prints with its object's; a modifier's 0 is "default"). */
export function filamentOf(obj: WorkspaceObject, partId?: string | null, edits?: OwnEdits): number {
  const edited = edits?.[ownTarget(obj.id ?? '', partId)]?.[FILAMENT_KEY];
  const saved = partId ? obj.parts.find((p) => p.id === partId)?.extruder : obj.extruder;
  const n = Number(edited === undefined ? saved : edited);
  return Number.isInteger(n) && n > 0 ? n : partId ? 0 : 1;
}

/** The filament a part prints with: its own, else its object's. */
export const printedFilament = (obj: WorkspaceObject, partId: string, edits?: OwnEdits): number =>
  filamentOf(obj, partId, edits) || filamentOf(obj, null, edits);

/** OrcaSlicer's filament change on an object or one of its parts, as draft edits (target -> extruder): the list's
 * Fila. column (``update_filament_in_config``: an object's choice drops its parts' own), keys 0-9
 * (``set_extruder_for_selected_items``: the model parts' only; 0 is filament 1 on an object, the object's on a
 * part) or the object table (``update_volume_values_from_object``: only the parts' own equal to the object's new
 * one). A value the file already holds goes back to it (undefined).
 * shortcut: a negative part's or a blocker's own filament (which prints nothing) stays; Orca's column drops it too. */
export function filamentEdits(
  obj: WorkspaceObject,
  partId: string | null,
  n: number,
  how: 'column' | 'keys' | 'table',
  edits?: OwnEdits,
): Record<string, Record<string, string | null | undefined>> {
  const id = obj.id ?? '';
  const part = partId ? obj.parts.find((p) => p.id === partId) : undefined;
  if (partId && (!part || !FILAMENT_PARTS.has(part.type ?? 'normal_part'))) return {};
  let value = n;
  if (how === 'keys' && n === 0) value = part ? ((part.type ?? 'normal_part') === 'normal_part' ? filamentOf(obj, null, edits) : 0) : 1;
  const saved = (part ? part.extruder : obj.extruder) ?? null;
  const out: Record<string, Record<string, string | null | undefined>> = {
    [ownTarget(id, partId)]: { [FILAMENT_KEY]: saved !== null && saved !== '' && Number(saved) === value ? undefined : String(value) },
  };
  if (!part) {
    for (const p of obj.parts) {
      const type = p.type ?? 'normal_part';
      if (!p.id || !FILAMENT_PARTS.has(type) || (how === 'keys' && type !== 'normal_part')) continue;
      if (how === 'table' && filamentOf(obj, p.id, edits) !== value) continue;
      const own = p.extruder != null && p.extruder !== '';
      if (own || edits?.[ownTarget(id, p.id)]?.[FILAMENT_KEY] !== undefined) {
        out[ownTarget(id, p.id)] = { [FILAMENT_KEY]: own ? null : undefined };
      }
    }
  }
  return out;
}

/** The own settings edited, as the copy saves them (null: no longer its own). */
export function ownToSave(edits: OwnEdits): ObjectSettingEdit[] {
  return Object.entries(edits).flatMap(([target, keys]) => {
    const [object_id, part_id = null] = target.split('/');
    return Object.entries(keys).map(([key, value]) => ({ object_id, part_id, key, value }));
  });
}

/** The plate's own settings (the keys of ``layout.plate_keys`` it sets). */
export function plateOwn(layout: OrcaLayout, plate: WorkspacePlate): Record<string, string> {
  const meta = plate.metadata ?? {};
  return Object.fromEntries(Object.entries(layout.plate_keys ?? {}).filter(([m]) => meta[m] != null && meta[m] !== '').map(([m, opt]) => [opt, meta[m]]));
}
