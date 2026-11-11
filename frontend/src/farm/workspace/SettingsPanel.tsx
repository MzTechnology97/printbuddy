// Fork (printfarm): the settings of one preset (process, filament or printer) laid out and dense as in OrcaSlicer:
// its pages as underlined text tabs, its option groups (icon, bold title, thin rule) and order, one ~30 px row per
// setting (label, compact input, reset arrow when changed; G-code and notes on the full width), its labels,
// tooltips and modes (Simple / Advanced / Expert, Develop with DEV; orcaLayout.ts). An option the slicer shows
// but the file doesn't hold is listed read only: the system preset's value applies, and it is never guessed.
// Key, system-preset state, original value and read-only reason are in the row's tooltip.
// Settings whose parent switch is off are greyed (dependencies.ts). What can change, and the checks, come from
// the backend (WorkspaceSetting.editable, draft.ts).
// With ``object``, the same pages show what one object (or part) slices with, as OrcaSlicer's object and part tabs
// (TabPrintObject, TabPrintPart): a "Frequent" tab first, only the settings an object (a part) can have, its own
// values first with the global value under them. A value changed there becomes its own (``onChange``); the reset
// arrow drops it (``onReset``), and the project's value applies again.
import { useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Filter, RotateCcw } from 'lucide-react';
import type { SettingValue, WorkspaceSetting } from './api';
import { dependencies } from './dependencies';
import { consistencyWarnings } from './warnings';
import { type Draft, formatValue, toLocaleDecimal, valueErrors } from './draft';
import { type Mode, type OrcaLayout, type Section, orcaIcon, orcaText, placedKeys, shownInMode } from './orcaLayout';
import { SettingControl } from './SettingControl';
import '../i18n';

const UNKNOWN_TAB = '__other__';
const FREQUENT_TAB = 'Frequent';
const OWN_GROUP = '__own__';
// OrcaSlicer's frequent object settings (GUI_Factories.cpp FREQ_SETTINGS_BUNDLE_FFF, first of each group)
const FREQUENT_KEYS = ['layer_height', 'sparse_infill_density', 'wall_loops', 'enable_support'];

/** One object's (or part's) own settings: key -> the value it slices with (a list per extruder comma separated). */
export interface ObjectView {
  own: Record<string, string>;
  /** a part or modifier: the part keys only (TabPrintPart) */
  part?: boolean;
}

interface Props {
  layout: OrcaLayout;
  section: Section;
  settings: WorkspaceSetting[];
  draft: Draft;
  /** The file can be edited at all (a project of a supported slicer). */
  editable: boolean;
  onChange: (setting: WorkspaceSetting, value: WorkspaceSetting['value']) => void;
  onReset: (key: string) => void;
  /** The slicer's mode: Simple shows its Simple settings only (a changed one stays) and no "Other values" tab. */
  mode?: Mode;
  /** OrcaSlicer's DEV switch: its develop options too. */
  develop?: boolean;
  /** Offer the "Other values" tab: values of this preset the layout places on no page. */
  showUnknown?: boolean;
  /** Above the tabs, left of the search (the preset's name box). */
  header?: ReactNode;
  /** Only this element of per-filament values (the filament window of one filament). */
  index?: number;
  /** Show one object's (part's) settings; ``onChange`` / ``onReset`` then edit its own. */
  object?: ObjectView;
}

interface Row {
  setting: WorkspaceSetting;
  tab: string;
  group: string;
  icon: string | null;
  mode: string | undefined;
}

function matches(row: Row, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || row.setting.label.toLowerCase().includes(q) || row.setting.key.toLowerCase().includes(q);
}

/** The global value a key slices with: the unsaved change, else the saved one. */
const globalValue = (draft: Draft, s: WorkspaceSetting): SettingValue => (s.key in draft ? draft[s.key] : s.value);

export function SettingsPanel({
  layout,
  section,
  settings,
  draft,
  editable: fileEditable,
  onChange,
  onReset,
  mode = 'expert',
  develop = false,
  showUnknown = true,
  header,
  index,
  object,
}: Props) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const tr = (text: string) => orcaText(layout, text, lang);
  const editable = fileEditable;
  const forPart = !!object?.part;
  const pages = useMemo(() => {
    const all = layout.pages[section];
    if (!object) return all;
    // an object: OrcaSlicer's object pages hold only what an object (a part) can have of its own
    const allowed = new Set((forPart ? layout.region_keys : null) ?? layout.object_keys ?? []);
    const own = all
      .map((p) => ({
        ...p,
        groups: p.groups.map((g) => ({ ...g, keys: g.keys.filter((k) => allowed.has(k)) })).filter((g) => g.keys.length),
      }))
      .filter((p) => p.groups.length);
    const frequent = FREQUENT_KEYS.filter((k) => allowed.has(k)); // a part: density and walls (TabPrintModel::build)
    return [{ title: FREQUENT_TAB, icon: '', groups: [{ title: '', icon: null, keys: frequent }] }, ...own];
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the pages depend on whether there is an object, not its values
  }, [layout, section, !!object, forPart]);
  const simple = mode === 'simple';
  const withUnknown = showUnknown && !simple && !object;
  // opens on the first page with something to change (the printer's first pages are mostly read only)
  const [chosenTab, setTab] = useState(() => {
    if (object) return FREQUENT_TAB;
    const editableKeys = new Set(settings.filter((s) => s.editable).map((s) => s.key));
    const first = pages.find((p) => p.groups.some((g) => g.keys.some((k) => editableKeys.has(k)))) ?? pages[0];
    return first?.title ?? UNKNOWN_TAB;
  });
  const [query, setQuery] = useState('');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [showReadOnlyChoice, setShowReadOnly] = useState(true); // as the slicer: everything it shows
  const showReadOnly = showReadOnlyChoice || !!object;
  const tabs = [...pages.map((p) => p.title), ...(withUnknown ? [UNKNOWN_TAB] : [])];
  const tab = tabs.includes(chosenTab) ? chosenTab : tabs[0];
  const byKey = useMemo(() => new Map(settings.map((s) => [s.key, s])), [settings]);

  // what a key slices with now (the object's own value, else the global one): which settings are greyed or hidden
  const { disabled: greyed, hidden } = dependencies(
    (key) => object?.own[key] ?? (key in draft ? draft[key] : byKey.get(key)?.value),
    !object,
  );
  const isOwn = (key: string) => !!object && key in object.own;
  // the slicer's consistency warnings for this preset (the global settings only)
  const warnings = object
    ? []
    : consistencyWarnings((key) => (key in draft ? draft[key] : byKey.get(key)?.value)).filter((w) => w.section === section);
  const warned = new Set(warnings.flatMap((w) => w.keys));

  // Every setting of this preset, in OrcaSlicer's order, shown with its label, tooltip, unit and icons.
  const rows = useMemo(() => {
    const show = (s: WorkspaceSetting): WorkspaceSetting => {
      const o = layout.options[s.key];
      if (!o) return s;
      const enums = new Map((o.enum ?? []).map((e) => [e.value, e]));
      return {
        ...s,
        label: orcaText(layout, o.label, lang),
        description: o.tooltip ? orcaText(layout, o.tooltip, lang) : s.description,
        unit: s.unit ?? (o.unit ? orcaText(layout, o.unit, lang) : null),
        options:
          s.options?.map((opt) => {
            const e = enums.get(opt.value);
            return { ...opt, label: e ? orcaText(layout, e.label, lang) : opt.label, icon: e?.icon };
          }) ?? null,
      };
    };
    // an option the slicer shows but the file doesn't hold: the system preset's value applies (not in the file)
    const absent = (key: string): WorkspaceSetting => {
      const o = layout.options[key];
      return {
        key,
        label: key,
        section,
        type: o?.type === 'coBool' || o?.type === 'coBools' ? 'bool' : 'string',
        multiline: o?.multiline,
        value: '',
        editable: false,
        readonly_reason: 'absent',
        differs_from_system: null,
        overrides: [],
      };
    };
    const out: Row[] = [];
    for (const page of pages) {
      const own: Row[] = [];
      const rest: Row[] = [];
      for (const group of page.groups) {
        for (const key of group.keys) {
          const s = byKey.get(key) ?? absent(key);
          const optionMode = layout.options[key]?.mode;
          if (object && key in object.own) own.push({ setting: show(s), tab: page.title, group: OWN_GROUP, icon: 'cog', mode: optionMode });
          else rest.push({ setting: show(s), tab: page.title, group: group.title, icon: group.icon, mode: optionMode });
        }
      }
      out.push(...own, ...rest); // an object's own values first on every page, under one heading
    }
    if (withUnknown) {
      const placed = placedKeys(layout);
      for (const s of settings) {
        // in its own preset: unknown keys (no section) with the process
        if (!placed.has(s.key) && (s.section ?? 'process') === section) {
          out.push({ setting: s, tab: UNKNOWN_TAB, group: '', icon: null, mode: undefined });
        }
      }
    }
    return out;
  }, [settings, byKey, layout, pages, section, withUnknown, lang, object]);

  const searching = !!query.trim();
  const visible = rows
    .filter((r) => (searching ? true : r.tab === tab))
    .filter((r) => matches(r, query))
    .filter((r) => r.tab === UNKNOWN_TAB || shownInMode(r.mode, mode, develop) || r.setting.key in draft)
    // the slicer hides a line whose parent switch is off (a pending change stays in sight)
    .filter((r) => !hidden.has(r.setting.key) || r.setting.key in draft)
    .filter((r) => !onlyChanged || r.setting.key in draft || isOwn(r.setting.key))
    .filter((r) => r.setting.editable || showReadOnly || r.tab === UNKNOWN_TAB)
    // a search lists a key once (an object's Frequent tab repeats four of them)
    .filter((r, i, all) => !searching || all.findIndex((o) => o.setting.key === r.setting.key) === i);
  const changedPerTab: Record<string, number> = {};
  for (const r of rows) {
    if (r.setting.key in draft || isOwn(r.setting.key)) changedPerTab[r.tab] = (changedPerTab[r.tab] ?? 0) + 1;
  }
  const tabLabel = (title: string) => (title === UNKNOWN_TAB ? t('farm.workspace.tabs.unknown') : tr(title));
  const inherit = t('farm.workspace.inherit');
  const format = (s: WorkspaceSetting, v: SettingValue) => formatValue(s, v, inherit, lang, index);

  /** Everything about a row that isn't its label and value, for its tooltip (as OrcaSlicer's hover). */
  const details = (s: WorkspaceSetting, changed: boolean, isGreyed: boolean) =>
    [
      s.description,
      s.key,
      changed && t('farm.workspace.original', { value: format(s, s.value) }),
      s.differs_from_system !== null && s.section && !object
        ? s.differs_from_system
          ? t('farm.workspace.system.modified')
          : t('farm.workspace.system.same')
        : null,
      !object &&
        s.overrides.length > 0 &&
        t('farm.workspace.overrides', {
          list: s.overrides.map((o) => `${o.part && o.part !== o.object ? `${o.object} › ${o.part}` : o.object} = ${o.value}`).join(', '),
        }),
      isGreyed && t('farm.workspace.dependsOff'),
      !s.editable && s.readonly_reason && editable && t(`farm.workspace.readonly.${s.readonly_reason}`),
    ]
      .filter(Boolean)
      .join('\n');

  let lastGroup: string | undefined;
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="px-3 pt-2 space-y-1.5">
        {header}
        <div className="relative flex items-center gap-1.5">
          <label className="relative flex-1">
            <img src={orcaIcon('search')} alt="" className="w-3.5 h-3.5 absolute left-2 top-1.5 opacity-70" aria-hidden />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('farm.workspace.search')}
              aria-label={t('farm.workspace.search')}
              className="w-full h-7 bg-bambu-dark border border-bambu-dark-tertiary rounded pl-7 pr-2 text-[13px] text-white"
            />
          </label>
          <button
            onClick={() => setFiltersOpen(!filtersOpen)}
            aria-expanded={filtersOpen}
            aria-label={t('farm.workspace.filters')}
            title={t('farm.workspace.filters')}
            className={`h-7 w-7 inline-flex items-center justify-center rounded border border-bambu-dark-tertiary ${
              onlyChanged || (editable && showReadOnly) ? 'text-bambu-green' : 'text-bambu-gray hover:text-white'
            }`}
          >
            <Filter className="w-3.5 h-3.5" />
          </button>
          {filtersOpen && (
            <div className="absolute right-0 top-8 z-20 space-y-1.5 rounded-md border border-bambu-dark-tertiary bg-bambu-dark-secondary p-2 text-xs text-bambu-gray-light shadow-lg">
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} />
                {t(object ? 'farm.workspace.onlyOwn' : 'farm.workspace.onlyChanged')}
              </label>
              {editable && (
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input type="checkbox" checked={showReadOnly} onChange={(e) => setShowReadOnly(e.target.checked)} />
                  {t('farm.workspace.showReadOnly')}
                </label>
              )}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-x-4 border-b border-bambu-dark-tertiary" role="tablist">
          {tabs.map((key) => {
            const active = tab === key && !searching;
            return (
              <button
                key={key}
                role="tab"
                aria-selected={active}
                onClick={() => {
                  setTab(key);
                  setQuery('');
                }}
                className={`-mb-px py-1.5 text-[13px] border-b-2 ${
                  active ? 'border-bambu-green text-white font-semibold' : 'border-transparent text-bambu-gray hover:text-white'
                }`}
              >
                {tabLabel(key)}
                {changedPerTab[key] ? <span className="ml-1 text-[#F1754E]">{changedPerTab[key]}</span> : null}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {warnings.length > 0 && (
          <div className="my-2 rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1.5 text-[12px] text-amber-200" role="status">
            <p className="font-semibold">{t('farm.workspace.consistency.title')}</p>
            <ul className="list-disc pl-4">
              {warnings.map((w, i) => (
                <li key={`${w.id}-${i}`}>
                  {t(
                    `farm.workspace.consistency.${w.id}`,
                    Object.fromEntries(Object.entries(w.values ?? {}).map(([k, v]) => [k, toLocaleDecimal(String(v), lang)])),
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {tab === UNKNOWN_TAB && !searching && <p className="text-xs text-bambu-gray my-2">{t('farm.workspace.unknownIntro')}</p>}
        {visible.length === 0 && <p className="text-sm text-bambu-gray mt-2">{t('farm.workspace.noResults')}</p>}
        {visible.map(({ setting: s, group, icon, tab: rowTab }) => {
          const groupIcon = orcaIcon(icon);
          const header =
            group !== lastGroup && group ? (
              <h4 className="flex items-center gap-2 pt-2.5 pb-0.5 text-[13px] font-semibold text-white">
                {groupIcon && <img src={groupIcon} alt="" className="w-4 h-4" aria-hidden />}
                {group === OWN_GROUP ? t('farm.workspace.ownGroup') : tr(group)}
                <span className="flex-1 h-px bg-bambu-dark-tertiary" aria-hidden />
              </h4>
            ) : null;
          lastGroup = group;
          const ownText = object?.own[s.key];
          const global = globalValue(draft, s);
          // an object's value of a list (one per extruder) is written comma separated
          const own = ownText != null && Array.isArray(global) ? ownText.split(',') : ownText;
          const changed = s.key in draft;
          const value = own ?? global;
          const isGreyed = greyed.has(s.key);
          const settingEditable = editable && s.editable && !isGreyed;
          const isAbsent = s.readonly_reason === 'absent';
          return (
            <div key={`${rowTab}-${s.key}`}>
              {header}
              <div
                className={`flex ${s.multiline ? 'flex-wrap' : ''} items-center gap-2 min-h-[30px] py-0.5 pl-4 ${
                  settingEditable || (object && !isGreyed) ? '' : 'opacity-50'
                }`}
                title={details(s, changed, isGreyed)}
                data-testid={`setting-${s.key}`}
                data-greyed={isGreyed || undefined}
              >
                <span
                  className={`flex-1 min-w-0 text-[13px] leading-tight ${changed || own != null ? 'text-[#F1754E]' : 'text-bambu-gray-light'}`}
                >
                  {s.label}
                  {!object && s.overrides.length > 0 && <span className="ml-1 text-amber-400" aria-hidden>●</span>}
                  {warned.has(s.key) && (
                    <span className="ml-1 text-amber-400" title={t('farm.workspace.consistency.title')} aria-label={t('farm.workspace.consistency.title')}>
                      ⚠
                    </span>
                  )}
                  {own != null && (
                    <span className="block text-[11px] text-bambu-gray">
                      {t('farm.workspace.globalValue', { value: format(s, globalValue(draft, s)) })}
                    </span>
                  )}
                  {isAbsent && own == null && <span className="block text-[11px] text-bambu-gray">{t('farm.workspace.notInFile')}</span>}
                </span>
                {s.multiline && <span className="basis-full h-0" aria-hidden />}
                <SettingControl
                  setting={settingEditable ? s : { ...s, editable: false }}
                  value={value}
                  errors={valueErrors(s, value)}
                  onChange={(v) => onChange(s, v)}
                  index={index}
                  disabled={isGreyed && editable && s.editable}
                />
                {(object ? own != null : changed) ? (
                  <button
                    onClick={() => onReset(s.key)}
                    title={t('farm.workspace.reset')}
                    aria-label={`${t('farm.workspace.reset')}: ${s.label}`}
                    className="w-5 h-5 inline-flex items-center justify-center rounded text-[#F1754E] hover:bg-bambu-dark-tertiary"
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                  </button>
                ) : (
                  <span className="w-5" aria-hidden />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
