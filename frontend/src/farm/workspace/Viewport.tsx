// Fork (printfarm): around the 3D view, as in the slicer clients: the object tools on top (shown, not active
// yet: geometry edits come with a verified 3MF writer, see docs/plans/3mf-workspace.md) and the numbered
// plate bar below.
import { useTranslation } from 'react-i18next';
import { Move, RotateCw, Scaling, Scissors, Paintbrush } from 'lucide-react';
import { api } from '../../api/client';
import type { WorkspacePlate } from './api';
import '../i18n';

const TOOLS = [
  { key: 'move', icon: Move },
  { key: 'rotate', icon: RotateCw },
  { key: 'scale', icon: Scaling },
  { key: 'cut', icon: Scissors },
  { key: 'paint', icon: Paintbrush },
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
      {TOOLS.map(({ key, icon: Icon }) => {
        const label = `${t(`farm.workspace.gizmos.${key}`)}: ${soon}`;
        return (
          // a disabled button gets no hover: the wrapper carries the tooltip
          <span key={key} title={label}>
            <button disabled aria-label={label} className="p-2 rounded text-bambu-gray opacity-50 cursor-not-allowed">
              <Icon className="w-4 h-4" />
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
