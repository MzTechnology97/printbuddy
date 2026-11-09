// Fork (printfarm): what the workspace selects (a plate, an object or one of its parts) and the settings each
// has of its own, from the document (model_settings.config objects, parts and plate metadata).
import type { WorkspaceObject, WorkspacePlate } from './api';
import type { OrcaLayout } from './orcaLayout';

export type Selection =
  | { kind: 'plate'; plate: number }
  | { kind: 'object'; id: string; plate: number | null; part?: string | null }
  | null;

/** An object's (part's) own settings: key -> value. */
export function ownSettings(obj: WorkspaceObject, part?: string | null): Record<string, string> {
  return Object.fromEntries(obj.overrides.filter((o) => (part ? o.part === part : !o.part || o.part === obj.name)).map((o) => [o.key, o.value]));
}

/** The plate's own settings (the keys of ``layout.plate_keys`` it sets). */
export function plateOwn(layout: OrcaLayout, plate: WorkspacePlate): Record<string, string> {
  const meta = plate.metadata ?? {};
  return Object.fromEntries(Object.entries(layout.plate_keys ?? {}).filter(([m]) => meta[m] != null && meta[m] !== '').map(([m, opt]) => [opt, meta[m]]));
}
