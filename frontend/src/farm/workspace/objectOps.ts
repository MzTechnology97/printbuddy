// Fork (printfarm): OrcaSlicer's operations on objects and instances (Selection::erase, ObjectList
// toggle_printable_state, update_name_in_model, the plate's Delete All): Delete, Delete All, Printable, Auto Drop and
// Rename, kept as the draft's ordered list the copy applies (backend farm/workspace/objects.py). Instance numbers are
// the file's. Pure functions, tested apart from the UI.
import type { WorkspaceObject, WorkspacePlate } from './api';

export interface ObjectOp {
  op: 'delete' | 'printable' | 'auto_drop' | 'rename';
  object_id: string;
  /** an instance (delete, printable); none: the whole object */
  instance?: number | null;
  /** a part (rename) */
  part_id?: string | null;
  value?: boolean | string | null;
}

/** Plater::has_illegal_filename_characters */
export const ILLEGAL = '<>:/\\|?*"';
export const NAME_REFUSED = 'Invalid name, the following characters are not allowed:';

/** Orca's message for a name it refuses, or null. */
export const nameRefusal = (name: string): string | null => ([...name].some((c) => ILLEGAL.includes(c)) ? NAME_REFUSED : null);

/** The instances the operations delete, by object (``all``: the whole object). */
export function deleted(ops: ObjectOp[]): Map<string, { all: boolean; instances: Set<number> }> {
  const out = new Map<string, { all: boolean; instances: Set<number> }>();
  for (const o of ops) {
    if (o.op !== 'delete') continue;
    const mine = out.get(o.object_id) ?? { all: false, instances: new Set<number>() };
    if (o.instance == null) mine.all = true;
    else mine.instances.add(o.instance);
    out.set(o.object_id, mine);
  }
  return out;
}

/** Whether instance ``instance`` of the object is deleted. */
export const isDeleted = (gone: ReturnType<typeof deleted>, objectId: string, instance: number): boolean => {
  const mine = gone.get(objectId);
  return !!mine && (mine.all || mine.instances.has(instance));
};

/** An instance's printable flag as the operations leave it (undefined: as the file has it). */
export function printableOf(ops: ObjectOp[], objectId: string, instance: number): boolean | undefined {
  let out: boolean | undefined;
  for (const o of ops) {
    if (o.op === 'printable' && o.object_id === objectId && (o.instance == null || o.instance === instance)) out = o.value === true;
  }
  return out;
}

/** The objects and plates as the operations leave them: names and auto drop changed, deleted instances off their
 * plates, an object with no instance left gone (its instances: those the plates list). */
export function withObjectOps(
  objects: WorkspaceObject[],
  plates: WorkspacePlate[],
  ops: ObjectOp[],
): { objects: WorkspaceObject[]; plates: WorkspacePlate[] } {
  if (!ops.length) return { objects, plates };
  const gone = deleted(ops);
  const listed = new Map<string, number[]>();
  for (const p of plates) for (const i of p.instances) listed.set(i.object_id, [...(listed.get(i.object_id) ?? []), Number(i.instance_id ?? 0)]);
  const isGone = (id: string) => {
    const mine = gone.get(id);
    if (!mine) return false;
    const all = listed.get(id) ?? [];
    return mine.all || (all.length > 0 && all.every((k) => mine.instances.has(k)));
  };
  const nextPlates = plates.map((p) => {
    const instances = p.instances.filter((i) => !isDeleted(gone, i.object_id, Number(i.instance_id ?? 0)));
    const objectIds = p.instances.length ? [...new Set(instances.map((i) => i.object_id))] : p.object_ids.filter((id) => !isGone(id));
    return instances.length === p.instances.length && objectIds.length === p.object_ids.length ? p : { ...p, instances, object_ids: objectIds };
  });
  const nextObjects = objects
    .filter((o) => !isGone(o.id ?? ''))
    .map((o) => {
      let out = o;
      for (const op of ops) {
        if (op.object_id !== o.id) continue;
        if (op.op === 'auto_drop') out = { ...out, auto_drop: op.value === true };
        if (op.op === 'rename' && typeof op.value === 'string') {
          const name = op.value;
          if (op.part_id) out = { ...out, parts: out.parts.map((p) => (p.id === op.part_id ? { ...p, name } : p)) };
          // update_name_in_model: an object of one part renames the part too
          else out = { ...out, name, parts: out.parts.length === 1 ? [{ ...out.parts[0], name }] : out.parts };
        }
      }
      return out;
    });
  return { objects: nextObjects, plates: nextPlates };
}

/** The deletions the plate's Delete All makes: each instance on the plate (Selection::remove_curr_plate). */
export function plateDeletions(plate: WorkspacePlate): ObjectOp[] {
  if (!plate.instances.length) return plate.object_ids.map((object_id) => ({ op: 'delete', object_id }));
  return plate.instances.map((i) => ({ op: 'delete', object_id: i.object_id, instance: Number(i.instance_id ?? 0) }));
}
