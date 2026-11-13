// Fork (printfarm): 3MF Workspace API, see backend/app/farm/workspace/routes.py.
import { farmRequest } from '../api';

/** A project value as the 3MF stores it: a string, or one string per filament / extruder. */
export type SettingValue = string | string[];

export type SettingType = 'int' | 'float' | 'bool' | 'enum' | 'string' | 'point';

export interface SettingOverride {
  object_id: string | null;
  object: string | null;
  part: string | null;
  value: string;
}

export interface WorkspaceSetting {
  key: string;
  label: string;
  description?: string | null;
  section: 'process' | 'filament' | 'printer' | null;
  type: SettingType;
  /** always: a percentage (``15%``); either: mm or % (``0.4`` / ``90%``); absent: a plain number */
  percent?: 'always' | 'either' | null;
  /** "nil" (the preset's value) may stand for an element */
  nullable?: boolean;
  /** text or G-code over several lines */
  multiline?: boolean;
  /** a list where any other value is fine too (material names...) */
  open?: boolean;
  unit?: string | null;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  options?: { value: string; label: string; icon?: string }[] | null;
  value: SettingValue;
  editable: boolean;
  /** unknown (the slicer doesn't define it), identity (preset / printer identity), slicer (read only in the
   * slicer too), project (project data the slicer keeps itself), format (doesn't read as its type, or only
   * inherited), absent (the slicer shows it but the file doesn't hold it: the system preset's value applies) */
  readonly_reason: 'unknown' | 'identity' | 'hardware' | 'slicer' | 'project' | 'format' | 'absent' | null;
  /** Against the system preset: true = changed, false = the preset's value, null = the file doesn't say. */
  differs_from_system: boolean | null;
  overrides: SettingOverride[];
}

export interface WorkspaceObject {
  id: string | null;
  name: string | null;
  extruder: string | null;
  /** Orca puts the object back on the bed after a move, rotation or scale (model_settings.config's auto_drop) */
  auto_drop?: boolean;
  /** ``extruder``: the part's own filament (0 or none: its object's; a modifier's 0: none changed) */
  parts: { id: string | null; name: string | null; type: string | null; extruder?: string | null }[];
  /** its own settings and its parts' (``part_id``: whose) */
  overrides: { key: string; value: string; part: string | null; part_id?: string | null }[];
  /** its height ranges (Height Range Modifier, layer_config_ranges.xml), in Orca's order */
  ranges?: LayerRange[];
}

/** One of an object's height ranges: from ``min_z`` to ``max_z`` (mm) with its own settings (``layer_height``,
 * ``extruder`` and the region's: TabPrintLayer). */
export interface LayerRange {
  min_z: number;
  max_z: number;
  options: Record<string, string>;
}

export interface WorkspacePlate {
  index: number;
  name: string | null;
  object_ids: string[];
  /** Each placement of an object on the plate: the identity a future 3D editor moves, rotates or duplicates. */
  instances: { object_id: string; instance_id: string | null }[];
  /** The plate's metadata in model_settings.config: its own settings (bed_type, print_sequence...) among them. */
  metadata?: Record<string, string>;
  /** After plates were added, removed or moved (plates.ts): the file's plate this one is (its thumbnail); null: a new
   * plate. */
  source?: number | null;
  /** A duplicated plate (Duplicate Current Plate): the objects it copies (written when the copy is saved). */
  copies?: string[];
  /** A plate added or duplicated before the copy is saved: the plate change (its place in the list) that made it. */
  added?: number;
}

export type WorkspaceKind = 'project' | 'sliced' | 'model' | 'other_slicer';

export interface WorkspaceDocument {
  file: { id: number; filename: string; folder_id: number | null; is_external: boolean; size: number; watched: boolean };
  sha256: string;
  kind: WorkspaceKind;
  slicer: string;
  application: string | null;
  editable: boolean;
  /** why a copy of this file can't hold objects moved (its model is too large to rewrite); null: it can */
  cannot_place?: string | null;
  plates: WorkspacePlate[];
  objects: WorkspaceObject[];
  warnings: string[];
  settings: WorkspaceSetting[];
  copy_of: { copy_of_library_file_id: number; copy_of_filename: string; saved_at: string } | null;
  /** The latest slice of each plate with the project's settings; ``current``: of the version open now. */
  outputs: WorkspaceOutput[];
}

export interface WorkspaceOutput {
  plate: number;
  library_file_id: number;
  filename: string;
  print_time_seconds: number | null;
  filament_used_g: number | null;
  current: boolean;
}

export interface SavedCopy {
  library_file_id: number;
  filename: string;
  folder_id: number | null;
  changes: { key: string; before: SettingValue; after: SettingValue }[];
}

/** A user preset (Printbuddy's local presets) offered by the preset headers and Compare presets. */
export interface PresetEntry {
  id: number;
  name: string;
  inherits: string | null;
}
export interface PresetValues {
  section: 'process' | 'filament' | 'printer';
  name: string;
  inherits: string | null;
  /** A parent up its chain that couldn't be read: only the values below it are known. */
  missing_parent: string | null;
  values: Record<string, SettingValue>;
}

/** A preset to switch a section to (OrcaSlicer's preset combos): a user preset by id, a system one by name. */
/** An instance placed anew: its build item's transform (3MF's 12 numbers), as the copy saves it. */
export interface InstanceTransform {
  object_id: string;
  instance: number;
  matrix: number[];
}

/** An object's (``part_id``: a part's) own setting set, changed or dropped (``value`` null): Orca's object tab. */
export interface ObjectSettingEdit {
  object_id: string;
  part_id: string | null;
  key: string;
  value: string | null;
}

/** One of a plate's own settings set, changed or dropped (``value`` null: same as global): Orca's Plate Settings. */
export interface PlateSettingEdit {
  plate: number;
  key: string;
  value: string | null;
}

/** A part placed anew in its object: its component transform (3MF's 12 numbers), for every instance. */
export interface PartTransform {
  object_id: string;
  part_id: string;
  matrix: number[];
}

export interface PresetRef {
  section: 'process' | 'filament' | 'printer';
  /** the filament slot (from 0) */
  index: number;
  source: 'user' | 'system';
  id?: number;
  name?: string;
}
/** Why a preset isn't offered: a parent not cached, for other printers, only a compatibility condition (not
 * evaluated), another printer model or nozzle. */
export type SwitchReason = 'unresolved' | 'incompatible' | 'condition' | 'other_printer';
export interface OfferedPreset {
  source: 'user' | 'system';
  name: string;
  id?: number;
  inherits?: string | null;
  reason: SwitchReason | null;
}
/** The project's printer, as the offered presets are checked against it. */
export interface PrinterIdentity {
  printer: string;
  printer_parent: string;
  model: string;
  variant: string;
  nozzle: string;
}

export const workspaceApi = {
  presets: (section: 'process' | 'filament' | 'printer') =>
    farmRequest<{ section: string; user: PresetEntry[]; system: string[] }>(`/workspace/presets?section=${section}`),
  /** Save preset's user preset, checked by the server as an edit is; ``id``: the one it overwrites. */
  saveUserPreset: (section: PresetRef['section'], name: string, setting: Record<string, unknown>, id?: number) =>
    farmRequest<{ id: number; name: string }>('/workspace/presets/user', {
      method: 'POST',
      body: JSON.stringify({ section, name, setting, id: id ?? null }),
    }),
  /** A user preset (by id) or a system one (by name), its inheritance resolved. */
  presetValues: (section: 'process' | 'filament' | 'printer', source: 'user' | 'system', ref: number | string) =>
    farmRequest<PresetValues>(
      `/workspace/presets/values?section=${section}&source=${source}&${source === 'user' ? 'id' : 'name'}=${encodeURIComponent(String(ref))}`,
    ),
  /** The presets a section can switch to on this printer, each with why it isn't offered. */
  offered: (section: PresetRef['section'], printer: PrinterIdentity) =>
    farmRequest<{ section: string; presets: OfferedPreset[] }>(
      `/workspace/presets/offered?${new URLSearchParams({ section, ...printer }).toString()}`,
    ),
  /** The project's values after the switches (nothing saved): the values that change. */
  previewSwitch: (settings: Record<string, SettingValue>, presets: PresetRef[]) =>
    farmRequest<{ values: Record<string, SettingValue> }>('/workspace/presets/switch', {
      method: 'POST',
      body: JSON.stringify({ settings, presets }),
    }),
  get: (fileId: number) => farmRequest<WorkspaceDocument>(`/workspace/files/${fileId}`),
  /** OrcaSlicer's own Arrange in the slicer sidecar, on the project as the draft has it (the save's fields); the
   * arranged instances, nothing saved. */
  arrange: (fileId: number, body: Record<string, unknown>) =>
    farmRequest<{ placements: { object_id: string; instance: number; matrix: number[]; plate: number }[]; plates: number }>(
      `/workspace/files/${fileId}/arrange`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  saveCopy: (
    fileId: number,
    baseSha256: string,
    changes: Record<string, SettingValue>,
    filename?: string,
    presets: PresetRef[] = [],
    transforms: InstanceTransform[] = [],
    partTransforms: PartTransform[] = [],
    objectSettings: ObjectSettingEdit[] = [],
    plateSettings: PlateSettingEdit[] = [],
    plateStructure: { op: string; plate?: number; objects?: string[]; instances?: InstanceTransform[] }[] = [],
    instancePlates: { object_id: string; instance: number; plate: number; new_plate?: boolean }[] = [],
    layerRanges: { object_id: string; ranges: LayerRange[] }[] = [],
  ) =>
    farmRequest<SavedCopy>(`/workspace/files/${fileId}/copy`, {
      method: 'POST',
      body: JSON.stringify({
        base_sha256: baseSha256,
        changes,
        filename: filename || null,
        presets,
        transforms,
        part_transforms: partTransforms,
        object_settings: objectSettings,
        plate_settings: plateSettings,
        plate_structure: plateStructure,
        instance_plates: instancePlates,
        layer_ranges: layerRanges,
      }),
    }),
};
