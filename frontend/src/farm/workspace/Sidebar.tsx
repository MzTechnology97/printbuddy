// Fork (printfarm): the workspace's left sidebar, laid out as OrcaSlicer's: Stampante (preset box, nozzle
// "Variante"), Filamento (a numbered colour chip per filament, each with its own settings window), Processo
// (Globale: the process settings by tab; Oggetti: the plates and objects tree with their own settings, read only).
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SettingValue, WorkspaceDocument, WorkspaceSetting } from './api';
import type { Draft } from './draft';
import type { Selection } from './objects';
import { ObjectsPanel } from './ObjectsPanel';
import { type OrcaLayout, orcaIcon } from './orcaLayout';
import { SettingsPanel } from './SettingsPanel';
import type { ObjectStats } from './viewTools';
import '../i18n';

function values(doc: WorkspaceDocument, key: string): string[] {
  const value = doc.settings.find((s) => s.key === key)?.value;
  return Array.isArray(value) ? value : value != null ? [value] : [];
}

function Header({ icon, title, children }: { icon: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 px-3 h-8 bg-bambu-dark text-[13px] text-white">
      <img src={orcaIcon(icon)} alt="" className="w-4 h-4" aria-hidden />
      <h3 className="font-medium">{title}</h3>
      <div className="flex-1 flex items-center justify-end gap-1">{children}</div>
    </div>
  );
}

function EditButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} title={label} aria-label={label} className="p-1 rounded hover:bg-bambu-dark-tertiary">
      <img src={orcaIcon('edit')} alt="" className="w-3.5 h-3.5" aria-hidden />
    </button>
  );
}

function Chip({ colour, n }: { colour: string | undefined; n: number }) {
  return (
    <span
      className="inline-flex items-center justify-center w-5 h-5 rounded-sm text-[11px] font-semibold text-black border border-white/30 flex-shrink-0"
      style={{ backgroundColor: colour ?? '#777' }}
    >
      {n}
    </span>
  );
}

interface Props {
  layout: OrcaLayout;
  doc: WorkspaceDocument;
  presets: { printer: string | null; process: string | null };
  draft: Draft;
  onChange: (setting: WorkspaceSetting, value: SettingValue) => void;
  onReset: (key: string) => void;
  /** ``filament``: which filament's window (from 0). */
  onEditPreset: (kind: 'printer' | 'filament', filament?: number) => void;
  /** The Process box shows the objects (Oggetti) instead of the global settings (Globale). */
  perObject: boolean;
  onPerObject: (on: boolean) => void;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  onMenu: (at: { x: number; y: number }, selection: Exclude<Selection, null>) => void;
  stats: Map<string, ObjectStats>;
}

export function Sidebar({
  layout,
  doc,
  presets,
  draft,
  onChange,
  onReset,
  onEditPreset,
  perObject,
  onPerObject,
  selection,
  onSelect,
  onMenu,
  stats,
}: Props) {
  const { t } = useTranslation();
  const [simple, setSimple] = useState(true);
  const colours = values(doc, 'filament_colour');
  const names = values(doc, 'filament_settings_id');
  const types = values(doc, 'filament_type');
  const nozzle = values(doc, 'nozzle_diameter')[0];
  const hasSettings = doc.settings.length > 0;
  const box = 'rounded border border-bambu-dark-tertiary bg-bambu-dark-secondary';

  return (
    <div className="flex flex-col h-full min-h-0 overflow-y-auto lg:overflow-visible">
      <Header icon="printer" title={t('farm.workspace.printer')}>
        {hasSettings && (
          <EditButton label={t('farm.workspace.editPreset', { name: t('farm.workspace.printer') })} onClick={() => onEditPreset('printer')} />
        )}
      </Header>
      <div className="flex gap-1.5 px-3 py-1.5">
        <div className={`${box} flex-1 min-w-0 flex items-center gap-2 px-2 h-10`} title={presets.printer ?? undefined}>
          <img src={orcaIcon('printer')} alt="" className="w-5 h-5 opacity-80" aria-hidden />
          <span className="text-[13px] text-white truncate">{presets.printer ?? '—'}</span>
        </div>
        <div className={`${box} w-16 flex flex-col justify-center px-2 h-10`}>
          <span className="text-[10px] text-bambu-gray">{t('farm.workspace.variant')}</span>
          <span className="text-[13px] text-white">{nozzle ?? '—'}</span>
        </div>
      </div>

      <Header icon="filament" title={t('farm.workspace.filaments')} />
      <ul className="px-3 py-1 space-y-1">
        {colours.length === 0 && <li className="text-[13px] text-bambu-gray">—</li>}
        {colours.map((c, i) => (
          <li key={i} className="flex items-center gap-1.5">
            <Chip colour={c} n={i + 1} />
            <span className={`${box} flex-1 min-w-0 flex items-center gap-2 px-2 h-7 text-[13px] text-white`}>
              <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0 border border-white/30" style={{ backgroundColor: c }} />
              <span className="truncate">{names[i] || types[i] || '—'}</span>
            </span>
            {hasSettings && (
              <EditButton
                label={t('farm.workspace.editFilament', { n: i + 1 })}
                onClick={() => onEditPreset('filament', i)}
              />
            )}
          </li>
        ))}
      </ul>

      <Header icon="custom-gcode_quality" title={t('farm.workspace.process')}>
        <span className="mr-auto ml-1 flex rounded-full bg-bambu-dark-tertiary p-0.5 text-[11px]" role="group">
          {[false, true].map((v) => (
            <button
              key={String(v)}
              onClick={() => onPerObject(v)}
              aria-pressed={perObject === v}
              className={`px-2 rounded-full ${perObject === v ? 'bg-bambu-green text-white' : 'text-bambu-gray hover:text-white'}`}
            >
              {v ? t('farm.workspace.perObject') : t('farm.workspace.global')}
            </button>
          ))}
        </span>
        {hasSettings && !perObject && (
          <label className="flex items-center gap-1 text-[11px] text-bambu-gray cursor-pointer" title={t('farm.workspace.mode')}>
            <input type="checkbox" checked={!simple} onChange={(e) => setSimple(!e.target.checked)} aria-label={t('farm.workspace.advanced')} />
            {t('farm.workspace.advanced')}
          </label>
        )}
      </Header>
      {perObject ? (
        <div className="flex-1 min-h-[32rem] lg:min-h-0">
          <ObjectsPanel
            layout={layout}
            doc={doc}
            draft={draft}
            colours={colours}
            stats={stats}
            selection={selection}
            onSelect={onSelect}
            onMenu={onMenu}
          />
        </div>
      ) : (
        hasSettings && (
          // narrow: the sidebar scrolls as a whole and the list keeps a usable height
          <div className="flex-1 min-h-[32rem] lg:min-h-0">
            <SettingsPanel
              layout={layout}
              section="process"
              settings={doc.settings}
              draft={draft}
              editable={doc.editable}
              onChange={onChange}
              onReset={onReset}
              simple={simple}
              header={
                <div className={`${box} flex items-center px-2 h-7 text-[13px] text-white`} title={presets.process ?? undefined}>
                  <span className="truncate">{presets.process ?? '—'}</span>
                </div>
              }
            />
          </div>
        )
      )}
    </div>
  );
}
