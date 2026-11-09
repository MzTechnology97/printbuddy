// Fork (printfarm): what the workspace selects (a plate, an object or one of its parts) and the settings each
// has of its own, from the document (model_settings.config objects, parts and plate metadata).
import type { WorkspaceObject, WorkspacePlate } from './api';
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

/** An object's (part's) own settings: key -> value. */
export function ownSettings(obj: WorkspaceObject, part?: string | null): Record<string, string> {
  return Object.fromEntries(obj.overrides.filter((o) => (part ? o.part === part : !o.part || o.part === obj.name)).map((o) => [o.key, o.value]));
}

/** The plate's own settings (the keys of ``layout.plate_keys`` it sets). */
export function plateOwn(layout: OrcaLayout, plate: WorkspacePlate): Record<string, string> {
  const meta = plate.metadata ?? {};
  return Object.fromEntries(Object.entries(layout.plate_keys ?? {}).filter(([m]) => meta[m] != null && meta[m] !== '').map(([m, opt]) => [opt, meta[m]]));
}
