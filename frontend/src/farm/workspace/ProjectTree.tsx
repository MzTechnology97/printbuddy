// Fork (printfarm): the workspace's left column: the project's plates (pick the active one) and the objects on
// each, with their filament and the settings they slice with on their own. Read only: the 3MF's structure.
import { useTranslation } from 'react-i18next';
import { api } from '../../api/client';
import type { WorkspaceObject, WorkspacePlate } from './api';
import '../i18n';

interface Props {
  fileId: number;
  plates: WorkspacePlate[];
  objects: WorkspaceObject[];
  filamentColours: string[];
  selectedPlate: number | null;
  onSelectPlate: (plate: number | null) => void;
}

function ObjectRow({ obj, colours }: { obj: WorkspaceObject; colours: string[] }) {
  const { t } = useTranslation();
  const n = Number(obj.extruder);
  return (
    <li className="py-1">
      <div className="flex items-center gap-2 text-sm text-white min-w-0">
        {Number.isFinite(n) && n > 0 && (
          <span
            className="inline-block w-3 h-3 rounded-full border border-white/30 flex-shrink-0"
            style={{ backgroundColor: colours[n - 1] ?? 'transparent' }}
            title={t('farm.workspace.filament', { n })}
            aria-label={t('farm.workspace.filament', { n })}
          />
        )}
        <span className="truncate">{obj.name ?? `#${obj.id}`}</span>
      </div>
      {obj.overrides.length > 0 && (
        <div className="ml-5 text-[11px] text-amber-400" title={obj.overrides.map((o) => `${o.key} = ${o.value}`).join('\n')}>
          {t('farm.workspace.ownSettings', { count: obj.overrides.length })}
        </div>
      )}
      {obj.parts.length > 1 && (
        <ul className="ml-5 text-[11px] text-bambu-gray">
          {obj.parts.map((p) => (
            <li key={p.id ?? p.name}>
              {p.name} {p.type && p.type !== 'normal_part' ? `(${p.type})` : ''}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function ProjectTree({ fileId, plates, objects, filamentColours, selectedPlate, onSelectPlate }: Props) {
  const { t } = useTranslation();
  const byId = new Map(objects.map((o) => [o.id, o]));
  const placed = new Set(plates.flatMap((p) => p.object_ids));
  const loose = objects.filter((o) => !placed.has(o.id ?? ''));
  return (
    <div className="p-3 space-y-3 overflow-y-auto h-full">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-bambu-gray">{t('farm.workspace.plates')}</h3>
      {plates.length === 0 && <p className="text-sm text-bambu-gray">{t('farm.workspace.noPlates')}</p>}
      <ul className="space-y-2">
        {plates.map((plate) => {
          const active = selectedPlate === plate.index;
          return (
            <li key={plate.index}>
              <button
                onClick={() => onSelectPlate(active ? null : plate.index)}
                aria-pressed={active}
                className={`w-full flex items-center gap-2 rounded-md p-1.5 text-left ${
                  active ? 'bg-bambu-green/15 ring-1 ring-bambu-green' : 'hover:bg-bambu-dark-tertiary'
                }`}
              >
                <img
                  src={api.getLibraryFilePlateThumbnail(fileId, plate.index)}
                  alt=""
                  className="w-12 h-12 rounded bg-bambu-dark object-contain flex-shrink-0"
                  onError={(e) => ((e.target as HTMLImageElement).style.visibility = 'hidden')}
                />
                <span className="min-w-0">
                  <span className="block text-sm text-white truncate">
                    {t('farm.workspace.plate', { index: plate.index })}
                    {plate.name ? ` · ${plate.name}` : ''}
                  </span>
                  <span className="block text-xs text-bambu-gray">
                    {plate.object_ids.length} {t('farm.workspace.objects').toLowerCase()}
                  </span>
                </span>
              </button>
              {active && (
                <ul className="ml-3 mt-1 border-l border-bambu-dark-tertiary pl-3">
                  {plate.object_ids.map((id, i) => {
                    const obj = byId.get(id);
                    return obj ? <ObjectRow key={`${id}-${i}`} obj={obj} colours={filamentColours} /> : null;
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {loose.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-bambu-gray">{t('farm.workspace.notOnPlate')}</h3>
          <ul>
            {loose.map((o, i) => (
              <ObjectRow key={`${o.id}-${i}`} obj={o} colours={filamentColours} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
