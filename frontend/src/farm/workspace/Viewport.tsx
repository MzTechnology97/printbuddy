// Fork (printfarm): around the 3D view, as in the slicer clients: the object tools on top (shown, not active
// yet: geometry edits come with a verified 3MF writer, see docs/plans/3mf-workspace.md) and the numbered
// plate bar below.
import { useTranslation } from 'react-i18next';
import { api } from '../../api/client';
import type { WorkspacePlate } from './api';
import { orcaIcon } from './orcaLayout';
import '../i18n';

// OrcaSlicer's object tools, with its own icons (orca-assets/icons)
const TOOLS = [
  { key: 'move', icon: 'toolbar_move_dark' },
  { key: 'rotate', icon: 'toolbar_rotate_dark' },
  { key: 'scale', icon: 'toolbar_scale_dark' },
  { key: 'cut', icon: 'toolbar_cut_dark' },
  { key: 'support', icon: 'toolbar_support_dark' },
  { key: 'seam', icon: 'toolbar_seam_dark' },
] as const;

export function GizmoToolbar() {
  const { t } = useTranslation();
  const soon = t('farm.workspace.gizmos.soon');
  return (
    <div
      className="absolute top-2 left-1/2 -translate-x-1/2 flex gap-1 rounded-lg bg-bambu-dark-secondary/90 p-1 border border-bambu-dark-tertiary"
      role="toolbar"
      aria-label={t('farm.workspace.gizmos.title')}
    >
      {TOOLS.map(({ key, icon }) => {
        const label = `${t(`farm.workspace.gizmos.${key}`)}: ${soon}`;
        return (
          // a disabled button gets no hover: the wrapper carries the tooltip
          <span key={key} title={label}>
            <button disabled aria-label={label} className="p-1.5 rounded opacity-70 cursor-not-allowed">
              <img src={orcaIcon(icon)} alt="" className="w-6 h-6" aria-hidden />
            </button>
          </span>
        );
      })}
    </div>
  );
}

interface PlateBarProps {
  fileId: number;
  plates: WorkspacePlate[];
  selected: number | null;
  onSelect: (plate: number | null) => void;
  /** Plates with a slice (Preview): the others are marked. */
  sliced?: Set<number>;
}

export function PlateBar({ fileId, plates, selected, onSelect, sliced }: PlateBarProps) {
  const { t } = useTranslation();
  if (plates.length === 0) return null;
  return (
    <div
      className={`absolute ${sliced ? 'bottom-10' : 'bottom-2'} left-2 right-12 flex justify-center gap-2 overflow-x-auto pointer-events-none`}
      role="tablist"
      aria-label={t('farm.workspace.plates')}
    >
      {plates.map((p) => {
        const active = selected === p.index;
        const name = t('farm.workspace.plate', { index: p.index }) + (p.name ? ` · ${p.name}` : '');
        return (
          <button
            key={p.index}
            role="tab"
            aria-selected={active}
            title={name}
            onClick={() => onSelect(active && !sliced ? null : p.index)}
            className={`pointer-events-auto relative flex-shrink-0 w-14 h-14 rounded-md border bg-bambu-dark-secondary/90 overflow-hidden ${
              active ? 'border-bambu-green ring-1 ring-bambu-green' : 'border-bambu-dark-tertiary hover:border-bambu-gray'
            } ${sliced && !sliced.has(p.index) ? 'opacity-60' : ''}`}
          >
            <img
              src={api.getLibraryFilePlateThumbnail(fileId, p.index)}
              alt=""
              className="absolute inset-0 w-full h-full object-contain"
              onError={(e) => ((e.target as HTMLImageElement).style.visibility = 'hidden')}
            />
            <span className="absolute top-0.5 left-0.5 min-w-[1.1rem] rounded bg-black/70 px-1 text-[11px] font-semibold text-white">
              {p.index}
            </span>
          </button>
        );
      })}
    </div>
  );
}
