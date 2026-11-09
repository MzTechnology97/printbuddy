// Fork (printfarm): 3MF Workspace API, see backend/app/farm/workspace/routes.py.
import { farmRequest } from '../api';

/** A project value as the 3MF stores it: a string, or one string per filament / extruder. */
export type SettingValue = string | string[];

export type SettingType = 'int' | 'float' | 'bool' | 'enum' | 'string';

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
  group?: string | null;
  tab: string | null;
  type: SettingType;
  unit?: string | null;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  options?: { value: string; label: string }[] | null;
  /** OrcaSlicer's mode for the key (Simple shows fewer settings); absent for keys no schema knows. */
  mode?: 'simple' | 'advanced';
  value: SettingValue;
  editable: boolean;
  /** unknown (no schema), text, category (could break printer compatibility), format (value doesn't read as its type) */
  readonly_reason: 'unknown' | 'text' | 'category' | 'format' | null;
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
  tabs: string[];
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
