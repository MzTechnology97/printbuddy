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
  readonly_reason: 'unknown' | 'identity' | 'slicer' | 'project' | 'format' | 'absent' | null;
  /** Against the system preset: true = changed, false = the preset's value, null = the file doesn't say. */
  differs_from_system: boolean | null;
  overrides: SettingOverride[];
}

export interface WorkspaceObject {
  id: string | null;
  name: string | null;
  extruder: string | null;
  parts: { id: string | null; name: string | null; type: string | null }[];
  overrides: { key: string; value: string; part: string | null }[];
}

export interface WorkspacePlate {
  index: number;
  name: string | null;
  object_ids: string[];
  /** Each placement of an object on the plate: the identity a future 3D editor moves, rotates or duplicates. */
  instances: { object_id: string; instance_id: string | null }[];
  /** The plate's metadata in model_settings.config: its own settings (bed_type, print_sequence...) among them. */
  metadata?: Record<string, string>;
}

export type WorkspaceKind = 'project' | 'sliced' | 'model' | 'other_slicer';

export interface WorkspaceDocument {
  file: { id: number; filename: string; folder_id: number | null; is_external: boolean; size: number; watched: boolean };
  sha256: string;
  kind: WorkspaceKind;
  slicer: string;
  application: string | null;
  editable: boolean;
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

export const workspaceApi = {
  get: (fileId: number) => farmRequest<WorkspaceDocument>(`/workspace/files/${fileId}`),
  saveCopy: (fileId: number, baseSha256: string, changes: Record<string, SettingValue>, filename?: string) =>
    farmRequest<SavedCopy>(`/workspace/files/${fileId}/copy`, {
      method: 'POST',
      body: JSON.stringify({ base_sha256: baseSha256, changes, filename: filename || null }),
    }),
};
