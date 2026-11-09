// Fork (printfarm): the Process box's "Objects" view, as OrcaSlicer's object list: a search, the tree Plate N ->
// objects -> parts and modifiers (printable box, filament chip, a cog on every node with settings of its own),
// and under it the settings of what is selected: a plate's "Plate Settings" (model_settings.config plate
// metadata) or an object's (part's) settings by tab, its own values first. Read only in this version: the
// copy writer rewrites project_settings.config only; per-object editing comes with phase 2.
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { WorkspaceDocument, WorkspaceObject, WorkspacePlate, WorkspaceSetting } from './api';
import type { Draft } from './draft';
import { type OrcaLayout, orcaIcon, orcaText } from './orcaLayout';
import { SettingControl } from './SettingControl';
import { SettingsPanel } from './SettingsPanel';
import { type Selection, ownSettings, plateOwn } from './objects';
import type { ObjectStats } from './viewTools';
import '../i18n';

const PART_ICONS: Record<string, string> = {
  normal_part: 'menu_add_part',
  negative_part: 'menu_add_negative',
  modifier_part: 'menu_add_modifier',
  support_blocker: 'menu_support_blocker',
  support_enforcer: 'menu_support_enforcer',
};

function Chip({ colour, n }: { colour: string | undefined; n: number }) {
  return (
    <span
      className="inline-flex items-center justify-center w-4 h-4 rounded-sm text-[10px] font-semibold text-black border border-white/30 flex-shrink-0"
      style={{ backgroundColor: colour ?? '#777' }}
    >
      {n}
    </span>
  );
}

function Cog({ title }: { title: string }) {
  return <img src={orcaIcon('cog')} alt={title} title={title} className="w-3.5 h-3.5 flex-shrink-0 opacity-90" />;
}

interface Props {
  layout: OrcaLayout;
  doc: WorkspaceDocument;
  draft: Draft;
  colours: string[];
  stats: Map<string, ObjectStats>;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  onMenu: (at: { x: number; y: number }, selection: Exclude<Selection, null>) => void;
}

export function ObjectsPanel({ layout, doc, draft, colours, stats, selection, onSelect, onMenu }: Props) {
  const { t, i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const [query, setQuery] = useState('');
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const byId = useMemo(() => new Map(doc.objects.map((o) => [o.id ?? '', o])), [doc.objects]);
  const byKey = useMemo(() => new Map(doc.settings.map((s) => [s.key, s])), [doc.settings]);
  const q = query.trim().toLowerCase();
  const hit = (text: string | null | undefined) => !q || (text ?? '').toLowerCase().includes(q);
  const ownCount = t('farm.workspace.ownSettingsTitle');

  // plates -> their objects (once per plate, however many instances); a project without plates: one list
  const groups: { plate: WorkspacePlate | null; objects: WorkspaceObject[] }[] = doc.plates.length
    ? doc.plates.map((p) => ({
        plate: p,
        objects: [...new Set(p.object_ids)].map((id) => byId.get(id)).filter((o): o is WorkspaceObject => !!o),
      }))
    : [{ plate: null, objects: doc.objects }];

  const toggle = (key: string) =>
    setClosed((c) => {
      const next = new Set(c);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const open = (key: string) => !!q || !closed.has(key);
  const menu = (e: React.MouseEvent, sel: Exclude<Selection, null>) => {
    e.preventDefault();
    onSelect(sel);
    onMenu({ x: e.clientX, y: e.clientY }, sel);
  };
  const row = (selected: boolean) =>
    `group flex items-center gap-1.5 h-[22px] pr-2 text-[12px] cursor-pointer select-none ${
      selected ? 'bg-bambu-green/25 text-white' : 'text-bambu-gray-light hover:bg-bambu-dark-tertiary/60'
    }`;
  const caret = (key: string, has: boolean) =>
    has ? (
      <button
        onClick={(e) => {
          e.stopPropagation();
          toggle(key);
        }}
        aria-label={open(key) ? t('farm.workspace.collapse') : t('farm.workspace.expand')}
        className="w-3.5 h-3.5 inline-flex items-center justify-center text-bambu-gray"
      >
        {open(key) ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
      </button>
    ) : (
      <span className="w-3.5" />
    );

  const selected = selection;
  const selectedObject = selected?.kind === 'object' ? byId.get(selected.id) : undefined;
  const selectedPlate = selected?.kind === 'plate' ? doc.plates.find((p) => p.index === selected.plate) : undefined;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-3 pt-1.5 pb-1">
        <label className="relative block">
          <img src={orcaIcon('search')} alt="" className="w-3.5 h-3.5 absolute left-2 top-1.5 opacity-70" aria-hidden />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={tr('Search plate, object and part.')}
            aria-label={tr('Search plate, object and part.')}
            className="w-full h-7 bg-bambu-dark border border-bambu-dark-tertiary rounded pl-7 pr-2 text-[12px] text-white"
          />
        </label>
      </div>
      <ul role="tree" aria-label={tr('Objects')} className="max-h-[40%] min-h-[6rem] overflow-y-auto border-b border-bambu-dark-tertiary pb-1">
        {groups.map(({ plate, objects }) => {
          const plateKey = `p${plate?.index ?? 0}`;
          const plateName = plate ? `${tr('Plate')} ${plate.index}${plate.name ? ` · ${plate.name}` : ''}` : tr('Objects');
          const shownObjects = objects.filter((o) => hit(plateName) || hit(o.name) || o.parts.some((p) => hit(p.name)));
          if (q && !shownObjects.length && !hit(plateName)) return null;
          const plateSelected = !!plate && selected?.kind === 'plate' && selected.plate === plate.index;
          const plateHasOwn = !!plate && Object.keys(plateOwn(layout, plate)).length > 0;
          return (
            <li key={plateKey} role="treeitem" aria-expanded={open(plateKey)} aria-selected={plateSelected} aria-level={1}>
              <div
                className={`${row(plateSelected)} pl-1`}
                onClick={() => plate && onSelect({ kind: 'plate', plate: plate.index })}
                onContextMenu={(e) => plate && menu(e, { kind: 'plate', plate: plate.index })}
              >
                {caret(plateKey, objects.length > 0)}
                <span className="flex-1 truncate">{plateName}</span>
                {plateHasOwn && <Cog title={ownCount} />}
              </div>
              {open(plateKey) && (
                <ul role="group">
                  {shownObjects.map((obj) => {
                    const id = obj.id ?? '';
                    const objKey = `${plateKey}-o${id}`;
                    const own = Object.keys(ownSettings(obj)).length > 0;
                    const showParts = obj.parts.length > 1 || obj.parts.some((p) => p.type && p.type !== 'normal_part') || obj.overrides.some((o) => o.part && o.part !== obj.name);
                    const n = Number(obj.extruder);
                    const objSelected = selected?.kind === 'object' && selected.id === id && !selected.part;
                    const sel = { kind: 'object' as const, id, plate: plate?.index ?? null };
                    return (
                      <li key={objKey} role="treeitem" aria-expanded={showParts ? open(objKey) : undefined} aria-selected={objSelected} aria-level={2}>
                        <div className={`${row(objSelected)} pl-4`} onClick={() => onSelect(sel)} onContextMenu={(e) => menu(e, sel)}>
                          {caret(objKey, showParts)}
                          <span className="flex-1 truncate">{obj.name ?? `#${id}`}</span>
                          {own && <Cog title={ownCount} />}
                          <input
                            type="checkbox"
                            readOnly
                            disabled
                            checked={stats.get(id)?.printable ?? true}
                            aria-label={tr('Printable')}
                            title={tr('Printable')}
                            className="w-3.5 h-3.5 accent-bambu-green"
                          />
                          {Number.isFinite(n) && n > 0 && <Chip colour={colours[n - 1]} n={n} />}
                        </div>
                        {showParts && open(objKey) && (
                          <ul role="group">
                            {obj.parts
                              .filter((p) => hit(p.name) || hit(obj.name) || hit(plateName))
                              .map((p) => {
                                const partSelected = selected?.kind === 'object' && selected.id === id && selected.part === p.name;
                                const partOwn = obj.overrides.some((o) => o.part === p.name && p.name !== obj.name);
                                const partSel = { ...sel, part: p.name };
                                return (
                                  <li key={`${objKey}-${p.id ?? p.name}`} role="treeitem" aria-selected={partSelected} aria-level={3}>
                                    <div className={`${row(partSelected)} pl-9`} onClick={() => onSelect(partSel)} onContextMenu={(e) => menu(e, sel)}>
                                      <img src={orcaIcon(PART_ICONS[p.type ?? 'normal_part'] ?? 'menu_add_part')} alt="" className="w-3.5 h-3.5" aria-hidden />
                                      <span className="flex-1 truncate">{p.name ?? `#${p.id}`}</span>
                                      {partOwn && <Cog title={ownCount} />}
                                    </div>
                                  </li>
                                );
                              })}
                          </ul>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      <div className="flex-1 min-h-[16rem]">
        {selectedPlate ? (
          <PlateSettings key={selectedPlate.index} layout={layout} plate={selectedPlate} byKey={byKey} draft={draft} />
        ) : selectedObject && selected?.kind === 'object' ? (
          <SettingsPanel
            key={`${selected.id}-${selected.part ?? ''}`}
            layout={layout}
            section="process"
            settings={doc.settings}
            draft={draft}
            editable={false}
            onChange={() => {}}
            onReset={() => {}}
            object={{ own: ownSettings(selectedObject, selected.part) }}
            header={
              <p className="text-[12px] text-bambu-gray truncate">
                {selectedObject.name}
                {selected.part && selected.part !== selectedObject.name ? ` › ${selected.part}` : ''} · {t('farm.workspace.readOnlyPhase2')}
              </p>
            }
          />
        ) : (
          <p className="px-3 py-3 text-[12px] text-bambu-gray">{t('farm.workspace.selectHint')}</p>
        )}
      </div>
    </div>
  );
}

/** OrcaSlicer's "Plate Settings" tab: the plate's own value of each, else the project's (greyed). */
function PlateSettings({
  layout,
  plate,
  byKey,
  draft,
}: {
  layout: OrcaLayout;
  plate: WorkspacePlate;
  byKey: Map<string, WorkspaceSetting>;
  draft: Draft;
}) {
  const { t, i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const own = plateOwn(layout, plate);
  return (
    <div className="px-3 pt-1">
      <div className="flex border-b border-bambu-dark-tertiary" role="tablist">
        <span role="tab" aria-selected className="-mb-px py-1.5 text-[13px] border-b-2 border-bambu-green text-white font-semibold">
          {tr('Plate Settings')}
        </span>
      </div>
      <p className="text-[11px] text-bambu-gray mt-1">{t('farm.workspace.readOnlyPhase2')}</p>
      {Object.values(layout.plate_keys ?? {}).map((key) => {
        const option = layout.options[key];
        const global = byKey.get(key);
        const label = option ? orcaText(layout, option.label, i18n.language) : key;
        const value = own[key] ?? (global ? (key in draft ? draft[key] : global.value) : undefined);
        const enumLabel = (value: string, fallback: string) =>
          orcaText(layout, option?.enum?.find((e) => e.value === value)?.label ?? fallback, i18n.language);
        // the project's setting, else (a key the project doesn't hold) the slicer's own definition of it
        const base: WorkspaceSetting | undefined =
          global ??
          (option && {
            key,
            label,
            section: 'process',
            tab: null,
            type: ({ coBool: 'bool', coEnum: 'enum', coInt: 'int', coFloat: 'float' } as const)[option.type] ?? 'string',
            unit: option.unit ?? null,
            options: option.enum?.map((e) => ({ value: e.value, label: e.label })) ?? null,
            value: '',
            editable: false,
            readonly_reason: null,
            differs_from_system: null,
            overrides: [],
          });
        const setting: WorkspaceSetting | undefined = base && {
          ...base,
          label,
          editable: false,
          options: base.options?.map((o) => ({ ...o, label: enumLabel(o.value, o.label) })) ?? null,
        };
        return (
          <div key={key} className={`flex items-center gap-2 min-h-[30px] py-0.5 pl-4 ${own[key] != null ? '' : 'opacity-60'}`} data-testid={`plate-${key}`}>
            <span className={`flex-1 min-w-0 text-[13px] leading-tight ${own[key] != null ? 'text-[#F1754E]' : 'text-bambu-gray-light'}`}>
              {label}
              {own[key] == null && <span className="block text-[11px] text-bambu-gray">{t('farm.workspace.fromProject')}</span>}
            </span>
            {setting && value != null ? (
              <SettingControl setting={setting} value={value} errors={[]} onChange={() => {}} />
            ) : (
              <span className="w-[120px] text-[13px] text-bambu-gray truncate">{own[key] ?? '—'}</span>
            )}
            <span className="w-5" aria-hidden />
          </div>
        );
      })}
    </div>
  );
}
