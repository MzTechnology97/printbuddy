// Fork (printfarm): OrcaSlicer's operations on objects and instances (Selection::erase, ObjectList
// toggle_printable_state, update_name_in_model, the plate's Delete All): Delete, Delete All, Printable, Auto Drop and
// Rename, kept as the draft's ordered list the copy applies (backend farm/workspace/objects.py). Instance numbers are
// the file's. Pure functions, tested apart from the UI.
import type { WorkspaceObject, WorkspacePlate } from './api';

export interface ObjectOp {
  op: 'delete' | 'printable' | 'auto_drop' | 'rename' | 'add_instance' | 'copy';
  object_id: string;
  /** an instance (delete, printable); none: the whole object */
  instance?: number | null;
  /** a part (rename) */
  part_id?: string | null;
  value?: boolean | string | null;
  /** add_instance: the new instance's transform */
  matrix?: number[];
  /** copy: the new object's id (from the document's next_object_id on, never used twice) and its instances' */
  new_id?: string;
  matrices?: number[][];
}

/** One instance the operations add: its key (``objectId:instance``), the object it shows (``source``: whose meshes)
 * and its transform. Instances after the file's are numbered on from them, in the operations' order (the copy's
 * numbering: additions are made first, deletions last). */
export interface AddedItem {
  key: string;
  objectId: string;
  instance: number;
  source: string;
  matrix: number[];
}

/** Each instance the operations add (``fileCount``: an object's instances in the file). */
export function addedItems(ops: ObjectOp[], fileCount: (objectId: string) => number): AddedItem[] {
  const counts = new Map<string, number>();
  const count = (id: string) => counts.get(id) ?? fileCount(id);
  const sourceOf = new Map<string, string>(); // a copy shows its source's meshes (a copy of a copy, the first's)
  const out: AddedItem[] = [];
  for (const o of ops) {
    if (o.op === 'add_instance' && o.matrix) {
      const k = count(o.object_id);
      counts.set(o.object_id, k + 1);
      out.push({ key: `${o.object_id}:${k}`, objectId: o.object_id, instance: k, source: sourceOf.get(o.object_id) ?? o.object_id, matrix: o.matrix });
    } else if (o.op === 'copy' && o.new_id && o.matrices) {
      const source = sourceOf.get(o.object_id) ?? o.object_id;
      sourceOf.set(o.new_id, source);
      counts.set(o.new_id, o.matrices.length);
      o.matrices.forEach((matrix, k) => out.push({ key: `${o.new_id}:${k}`, objectId: o.new_id!, instance: k, source, matrix }));
    }
  }
  return out;
}

/** The next id a new object takes: from the document's ``next_object_id``, past every one the operations used. */
export function nextNewId(ops: ObjectOp[], first: number): number {
  return ops.reduce((n, o) => (o.op === 'copy' && o.new_id ? Math.max(n, Number(o.new_id) + 1) : n), first);
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
  // in the operations' order: a copy is its object as the operations before it left it, under its new id
  // (Model::add_object), and later ones change it apart
  const all = [...objects];
  for (const op of ops) {
    const i = all.findIndex((o) => o.id === op.object_id);
    if (i < 0) continue;
    const o = all[i];
    if (op.op === 'copy' && op.new_id) all.push({ ...o, id: op.new_id });
    else if (op.op === 'auto_drop') all[i] = { ...o, auto_drop: op.value === true };
    else if (op.op === 'rename' && typeof op.value === 'string') {
      const name = op.value;
      if (op.part_id) all[i] = { ...o, parts: o.parts.map((p) => (p.id === op.part_id ? { ...p, name } : p)) };
      // update_name_in_model: an object of one part renames the part too
      else all[i] = { ...o, name, parts: o.parts.length === 1 ? [{ ...o.parts[0], name }] : o.parts };
    }
  }
  return { objects: all.filter((o) => !isGone(o.id ?? '')), plates: nextPlates };
}

/** The deletions the plate's Delete All makes: each instance on the plate (Selection::remove_curr_plate). */
export function plateDeletions(plate: WorkspacePlate): ObjectOp[] {
  if (!plate.instances.length) return plate.object_ids.map((object_id) => ({ op: 'delete', object_id }));
  return plate.instances.map((i) => ({ op: 'delete', object_id: i.object_id, instance: Number(i.instance_id ?? 0) }));
}
