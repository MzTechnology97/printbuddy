// Fork (printfarm): the workspace's left sidebar, laid out as in the slicer clients: printer, filaments,
// objects (with the settings they slice with on their own, read only), then the process settings by tab.
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Boxes, ChevronDown, ChevronRight, Printer, SlidersHorizontal, Spline } from 'lucide-react';
import type { SettingValue, WorkspaceDocument, WorkspaceObject, WorkspaceSetting } from './api';
import type { Draft } from './draft';
import { SettingsPanel } from './SettingsPanel';
import '../i18n';

const PROCESS_TABS = ['quality', 'strength', 'speed', 'support', 'multimaterial', 'others'];

function values(doc: WorkspaceDocument, key: string): string[] {
  const value = doc.settings.find((s) => s.key === key)?.value;
  return Array.isArray(value) ? value : value != null ? [value] : [];
}

function Section({
  title,
  icon,
  action,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-bambu-dark-tertiary px-3 py-2">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-bambu-gray">
        {icon}
        <h3 className="flex-1">{title}</h3>
        {action}
      </div>
      <div className="mt-1.5">{children}</div>
    </section>
  );
}

function EditButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className="p-1 rounded text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary normal-case"
    >
      <SlidersHorizontal className="w-3.5 h-3.5" />
    </button>
  );
}

function Swatch({ colour, n }: { colour: string | undefined; n: number }) {
  return (
    <span
      className="inline-flex items-center justify-center w-5 h-5 rounded text-[10px] font-semibold text-black border border-white/30 flex-shrink-0"
      style={{ backgroundColor: colour ?? '#777' }}
    >
      {n}
    </span>
  );
}

function ObjectRow({ obj, colours }: { obj: WorkspaceObject; colours: string[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const n = Number(obj.extruder);
  const own = obj.overrides.length;
  const more = own > 0 || obj.parts.length > 1;
  return (
    <li>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={more ? open : undefined}
        disabled={!more}
        className="w-full flex items-center gap-2 py-1 text-left text-sm text-white disabled:cursor-default"
      >
        {more ? (
          open ? (
            <ChevronDown className="w-3.5 h-3.5 text-bambu-gray" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-bambu-gray" />
          )
        ) : (
          <span className="w-3.5" />
        )}
        <span className="truncate flex-1">{obj.name ?? `#${obj.id}`}</span>
        {own > 0 && <span className="text-[11px] text-amber-400">{t('farm.workspace.ownSettings', { count: own })}</span>}
        {Number.isFinite(n) && n > 0 && <Swatch colour={colours[n - 1]} n={n} />}
      </button>
      {open && (
        <ul className="ml-6 mb-1 text-[11px] text-bambu-gray space-y-0.5">
          {obj.parts.length > 1 && obj.parts.map((p) => <li key={p.id ?? p.name}>{p.name}</li>)}
          {obj.overrides.map((o) => (
            <li key={`${o.part}-${o.key}`} className="font-mono break-all">
              {o.part && o.part !== obj.name ? `${o.part} › ` : ''}
              {o.key} = {o.value}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

interface Props {
  doc: WorkspaceDocument;
  presets: { printer: string | null; process: string | null };
  selectedPlate: number | null;
  draft: Draft;
  onChange: (setting: WorkspaceSetting, value: SettingValue) => void;
  onReset: (key: string) => void;
  onEditPreset: (kind: 'printer' | 'filament') => void;
}

export function Sidebar({ doc, presets, selectedPlate, draft, onChange, onReset, onEditPreset }: Props) {
  const { t } = useTranslation();
  const [simple, setSimple] = useState(true);
  const colours = values(doc, 'filament_colour');
  const types = values(doc, 'filament_type');
  const names = values(doc, 'filament_settings_id');
  const bed = values(doc, 'curr_bed_type')[0];
  const nozzle = values(doc, 'nozzle_diameter')[0];
  const plate = doc.plates.find((p) => p.index === selectedPlate);
  const byId = new Map(doc.objects.map((o) => [o.id, o]));
  const objects = plate
    ? plate.object_ids.map((id) => byId.get(id)).filter((o): o is WorkspaceObject => !!o)
    : doc.objects;
  const hasSettings = doc.settings.length > 0;

  return (
    <div className="flex flex-col h-full min-h-0 overflow-y-auto lg:overflow-visible">
      <Section
        title={t('farm.workspace.printer')}
        icon={<Printer className="w-3.5 h-3.5" />}
        action={
          hasSettings && (
            <EditButton
              label={t('farm.workspace.editPreset', { name: t('farm.workspace.printer') })}
              onClick={() => onEditPreset('printer')}
            />
          )
        }
      >
        <div className="text-sm text-white truncate" title={presets.printer ?? undefined}>
          {presets.printer ?? '—'}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-bambu-gray">
          {nozzle && (
            <span>
              {t('farm.workspace.nozzle')}: {nozzle} mm
            </span>
          )}
          {bed && (
            <span>
              {t('farm.workspace.bed')}: {bed}
            </span>
          )}
        </div>
      </Section>

      <Section
        title={t('farm.workspace.filaments')}
        icon={<Spline className="w-3.5 h-3.5" />}
        action={
          hasSettings && (
            <EditButton
              label={t('farm.workspace.editPreset', { name: t('farm.workspace.filaments') })}
              onClick={() => onEditPreset('filament')}
            />
          )
        }
      >
        {colours.length === 0 && <p className="text-sm text-bambu-gray">—</p>}
        <ul className="space-y-1">
          {colours.map((c, i) => (
            <li key={i} className="flex items-center gap-2 text-sm min-w-0">
              <Swatch colour={c} n={i + 1} />
              <span className="text-white">{types[i] ?? ''}</span>
              <span className="text-bambu-gray truncate">{names[i] ?? ''}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title={t('farm.workspace.objects')} icon={<Boxes className="w-3.5 h-3.5" />}>
        {objects.length === 0 ? (
          <p className="text-sm text-bambu-gray">—</p>
        ) : (
          <ul className="max-h-40 overflow-y-auto">
            {objects.map((o, i) => (
              <ObjectRow key={`${o.id}-${i}`} obj={o} colours={colours} />
            ))}
          </ul>
        )}
      </Section>

      {hasSettings && (
        <>
          <div className="px-3 pt-2 flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="text-xs font-semibold uppercase tracking-wide text-bambu-gray">
                {t('farm.workspace.process')}
              </div>
              <div className="text-sm text-white truncate" title={presets.process ?? undefined}>
                {presets.process ?? '—'}
              </div>
            </div>
            <div className="flex rounded-md border border-bambu-dark-tertiary text-xs" role="group" aria-label={t('farm.workspace.mode')}>
              {[true, false].map((value) => (
                <button
                  key={String(value)}
                  onClick={() => setSimple(value)}
                  aria-pressed={simple === value}
                  className={`px-2 py-1 ${value ? 'rounded-l-md' : 'rounded-r-md'} ${
                    simple === value ? 'bg-bambu-green text-white' : 'text-bambu-gray-light hover:text-white'
                  }`}
                >
                  {value ? t('farm.workspace.simple') : t('farm.workspace.advanced')}
                </button>
              ))}
            </div>
          </div>
          {/* narrow: the sidebar scrolls as a whole and the list keeps a usable height */}
          <div className="flex-1 min-h-[32rem] lg:min-h-0">
            <SettingsPanel
              settings={doc.settings}
              tabs={PROCESS_TABS}
              draft={draft}
              editable={doc.editable}
              onChange={onChange}
              onReset={onReset}
              simple={simple}
            />
          </div>
        </>
      )}
    </div>
  );
}
