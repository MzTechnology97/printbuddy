// Fork (printfarm): project-settings slicing API and the preset pre-picks that follow the 3MF.
import type { PresetRef, UnifiedPreset, UnifiedPresetsResponse } from '../api/client';
import { farmRequest } from './api';

export interface ProjectFilament {
  index: number;
  preset: string | null;
  type: string | null;
  color: string | null;
}

export interface ProjectSettings {
  slicer: string;
  sliceable: boolean;
  sidecar_url: string | null;
  printer_model: string | null;
  printer_model_raw: string | null;
  printer_preset: string | null;
  process_preset: string | null;
  nozzle_diameter: string | null;
  filaments: ProjectFilament[];
}

export const farmProjectApi = {
  get: (fileId: number) => farmRequest<ProjectSettings>(`/library/files/${fileId}/project`),
  slice: (fileId: number, plate: number | null) =>
    farmRequest<{ job_id: number; status: string; status_url: string }>(`/library/files/${fileId}/slice`, {
      method: 'POST',
      body: JSON.stringify({ plate }),
    }),
};

/** Unsliced 3MF projects carry their own settings; sliced outputs and other formats don't. */
export function isProjectFile(filename: string): boolean {
  const name = filename.toLowerCase();
  return name.endsWith('.3mf') && !name.endsWith('.gcode.3mf');
}

const TIERS = ['local', 'orca_cloud', 'cloud', 'standard'] as const;

function presetsOf(by: UnifiedPresetsResponse, slot: 'printer' | 'filament'): UnifiedPreset[] {
  return TIERS.flatMap((tier) => by[tier]?.[slot] ?? []);
}

/** Printer preset for the 3MF's machine when its exact preset name isn't available: same model, same nozzle. */
export function pickProjectPrinter(by: UnifiedPresetsResponse, info: ProjectSettings | undefined): PresetRef | null {
  const model = (info?.printer_model_raw ?? '').trim().toLowerCase();
  if (!model) return null;
  const candidates = presetsOf(by, 'printer').filter((p) => p.name.toLowerCase().includes(model));
  const nozzle = info?.nozzle_diameter ? `${info.nozzle_diameter} nozzle` : null;
  const best = candidates.find((p) => nozzle != null && p.name.toLowerCase().includes(nozzle)) ?? candidates[0];
  return best ? { source: best.source, id: best.id } : null;
}

/** The filament preset the 3MF uses in this slot (1-based), as saved in the project. */
export function projectFilamentName(info: ProjectSettings | undefined, slotId: number): string | null {
  return info?.filaments.find((f) => f.index === slotId)?.preset ?? null;
}
