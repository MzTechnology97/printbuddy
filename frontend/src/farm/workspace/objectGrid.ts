// Fork (printfarm): OrcaSlicer's object table ("Object/Part Settings", GUI_ObjectTable.cpp, v2.5.0) as pure
// functions: its rows (every object, then its parts when it has more than one), columns, sorting, the value a
// cell shows, the one it inherits (an object's: the project's; a part's: its object's) and the edit a new value
// makes. As Orca's ``update_value_to_config``, a value equal to the inherited one is not kept as the row's own.
import type { WorkspaceDocument, WorkspaceObject, WorkspaceSetting } from './api';
import type { Draft, OwnEdits } from './draft';
import { FILAMENT_PARTS, editTarget, filamentEdits, filamentOf, instancesOf, ownSettings, ownTarget, printedFilament, targetOwn } from './objects';
import type { OrcaLayout } from './orcaLayout';

/** ``All``: Orca's category_all (the side panel lists the row's own settings of every category). */
export const ALL = 'All';

export interface TableColumn {
  id: string;
  /** the header, as OrcaSlicer writes it */
  label: string;
  category: string;
  /** a part row shows its object's value, read only (``b_for_object``) */
  objectOnly?: boolean;
  /** the header sorts the rows (Plate ↑↓, Name ↑↓, Filament ↑↓) */
  sort?: boolean;
  /** a process setting the row may hold of its own, with its reset icon */
  setting?: boolean;
}

/** GUI_ObjectTable.cpp ``init_cols``, in its order. */
export const TABLE_COLUMNS: TableColumn[] = [
  { id: 'printable', label: 'Printable', category: ALL, objectOnly: true },
  { id: 'plate', label: 'Plate', category: ALL, objectOnly: true, sort: true },
  { id: 'name', label: 'Name', category: ALL, sort: true },
  { id: 'extruder', label: 'Filament', category: ALL, sort: true },
  { id: 'layer_height', label: 'Layer height', category: 'Quality', objectOnly: true, setting: true },
  { id: 'wall_loops', label: 'Wall loops', category: 'Strength', setting: true },
  { id: 'sparse_infill_density', label: 'Infill density(%)', category: 'Strength', setting: true },
  { id: 'enable_support', label: 'Support', category: 'Support', objectOnly: true, setting: true },
  { id: 'brim_type', label: 'Brim', category: 'Support', objectOnly: true, setting: true },
  { id: 'outer_wall_speed', label: 'Outer wall speed', category: 'Speed', setting: true },
];

/** The Brim column's choices (``init_cols``: the first seven brim types). */
export const TABLE_BRIMS = ['auto_brim', 'brim_ears', 'painted', 'outer_only', 'inner_only', 'outer_and_inner', 'no_brim'];

export interface TableRow {
  obj: WorkspaceObject;
  /** null: the object's row */
  partId: string | null;
  /** the part's kind (normal_part, modifier_part, negative_part...); the object's row: null */
  type: string | null;
}

/** ``construct_object_configs``: each object, and its parts under it when it has more than one. */
export function tableRows(doc: WorkspaceDocument): TableRow[] {
  return doc.objects.flatMap((obj) => [
    { obj, partId: null, type: null },
    ...(obj.parts.length > 1 ? obj.parts.filter((p) => p.id).map((p) => ({ obj, partId: p.id, type: p.type ?? 'normal_part' })) : []),
  ]);
}

/** The plate of each object's first instance (``find_instance_belongs(i, 0)``): its number, null outside every plate. */
export function platesOf(doc: WorkspaceDocument): Map<string, number | null> {
  const where = instancesOf(doc.plates);
  return new Map(doc.objects.map((o) => [o.id ?? '', where.get(o.id ?? '')?.[0]?.plate ?? null]));
}

const byteOrder = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
const OUTSIDE = 100; // as Orca sorts a plate "Outside"

/** ``sort_by_default`` (``col`` null) and ``sort_by_col``: the objects in order, each keeping its parts under it;
 * the plate's order sorts equal plates by name. The filament compares as text, as Orca's ``std::to_string``. */
export function sortRows(
  rows: TableRow[],
  plates: Map<string, number | null>,
  col: string | null,
  desc: boolean,
  edits?: OwnEdits,
): TableRow[] {
  const blocks: TableRow[][] = [];
  for (const r of rows) {
    if (r.partId == null) blocks.push([r]);
    else blocks[blocks.length - 1]?.push(r);
  }
  const plate = (o: WorkspaceObject) => plates.get(o.id ?? '') ?? OUTSIDE;
  const name = (o: WorkspaceObject) => o.name ?? '';
  const compare = (a: WorkspaceObject, b: WorkspaceObject): number => {
    if (col === 'name') return byteOrder(name(a), name(b));
    if (col === 'extruder') return byteOrder(String(filamentOf(a, null, edits)), String(filamentOf(b, null, edits)));
    return plate(a) - plate(b) || byteOrder(name(a), name(b)); // the default order, and the Plate column's
  };
  return blocks.sort((a, b) => (desc ? -1 : 1) * compare(a[0].obj, b[0].obj)).flat();
}

/** A value as one string (a list per extruder comma separated, as the 3MF writes it). */
const flat = (v: string | string[]): string => (Array.isArray(v) ? v.join(',') : v);

/** The project's value of a setting, the unsaved change made. */
export const projectValue = (setting: WorkspaceSetting, draft: Draft): string => flat(setting.key in draft ? draft[setting.key] : setting.value);

/** What the row slices ``setting`` with: its own, else what it inherits (``get_object_config_value``,
 * ``get_volume_config_value``). */
export function rowValue(row: TableRow, setting: WorkspaceSetting, draft: Draft, edits?: OwnEdits): string {
  const own = row.partId ? targetOwn(row.obj, row.partId, edits) : ownSettings(row.obj, null, edits);
  return own[setting.key] ?? inherited(row, setting, draft, edits);
}

/** What the row inherits (Orca's ``ori_`` value, which the reset icon puts back): an object the project's value, a
 * part its object's. */
export function inherited(row: TableRow, setting: WorkspaceSetting, draft: Draft, edits?: OwnEdits): string {
  return row.partId ? rowValue({ ...row, partId: null, type: null }, setting, draft, edits) : projectValue(setting, draft);
}

/** Two values the slicer reads the same: element by element, numbers by value (``0.2`` = ``0.20``, ``15%`` = ``15.0%``). */
export function sameSetting(a: string, b: string): boolean {
  const x = a.split(',');
  const y = b.split(',');
  return (
    x.length === y.length &&
    x.every((e, i) => {
      const [p, q] = [e.trim(), y[i].trim()];
      const num = /^-?(\d+\.?\d*|\.\d+)%?$/;
      return num.test(p) && num.test(q) && p.endsWith('%') === q.endsWith('%') ? Number(p.replace('%', '')) === Number(q.replace('%', '')) : p === q;
    })
  );
}

export type TargetEdits = Record<string, Record<string, string | null | undefined>>;

/** The edit of a row's own ``key``: ``value`` (null: not its own any more) for the target that holds it (an object's
 * hidden part, objects.ts ``editTarget``); undefined when that is what the file says. */
export function ownEdit(row: TableRow, key: string, value: string | null, edits?: OwnEdits): TargetEdits {
  const id = row.obj.id ?? '';
  const target = row.partId ? ownTarget(id, row.partId) : editTarget(row.obj, null, key, edits);
  const [, partId = null] = target.split('/');
  const saved = targetOwn(row.obj, partId)[key] ?? null;
  return { [target]: { [key]: value === saved ? undefined : value } };
}

/** ``SetValue`` of a setting cell: the new value as the row's own, or none when it's the inherited one. */
export function cellEdit(row: TableRow, setting: WorkspaceSetting, value: string, draft: Draft, edits?: OwnEdits): TargetEdits {
  return ownEdit(row, setting.key, sameSetting(value, inherited(row, setting, draft, edits)) ? null : value, edits);
}

/** The filament a row shows: an object's, a part's own or else its object's. */
export const rowFilament = (row: TableRow, edits?: OwnEdits): number =>
  row.partId ? printedFilament(row.obj, row.partId, edits) : filamentOf(row.obj, null, edits);

/** ``SetValue`` of the Filament column (``update_filament_to_config``, ``update_volume_values_from_object``). */
export const filamentCellEdit = (row: TableRow, n: number, edits?: OwnEdits): TargetEdits => filamentEdits(row.obj, row.partId, n, 'table', edits);

/** Whether a cell can change: a setting of an object, or of a part or modifier that has settings (Orca also lists
 * the other kinds' cells, which slice nothing); the filament of an object, part or modifier. */
export function cellEditable(row: TableRow, col: TableColumn, setting: WorkspaceSetting | undefined): boolean {
  const part = row.type == null || FILAMENT_PARTS.has(row.type);
  if (col.id === 'extruder') return part;
  return !!col.setting && !!setting?.editable && part && !(row.partId && col.objectOnly);
}

/** The side panel's settings (``ObjectTableSettings::update_settings_list``) for a cell of ``category``: the part's
 * list, and for an object the object's list too, by priority; ``All``: only the row's own, by category. */
export function sideSettings(
  layout: OrcaLayout,
  category: string,
  part: boolean,
  own: Record<string, string>,
): { category: string; keys: { key: string; label?: string }[] }[] {
  const lists = layout.table_settings;
  if (!lists) return [];
  const allowed = new Set(part ? (layout.region_keys ?? []) : layout.object_keys);
  const of = (cat: string) =>
    [...(lists.part[cat] ?? []), ...(part ? [] : (lists.object[cat] ?? []))]
      .map((s, i) => ({ ...s, i }))
      .sort((a, b) => a.priority - b.priority || a.i - b.i)
      .filter((s) => allowed.has(s.key) && layout.options[s.key])
      .map(({ key, label }) => ({ key, ...(label ? { label } : {}) }));
  if (category !== ALL) return [{ category, keys: of(category) }];
  const cats = [...new Set([...Object.keys(lists.part), ...(part ? [] : Object.keys(lists.object))])].sort();
  return cats.map((c) => ({ category: c, keys: of(c).filter((s) => s.key in own) })).filter((g) => g.keys.length);
}
