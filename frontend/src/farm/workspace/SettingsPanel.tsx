// Fork (printfarm): the settings of one preset (process, filament or printer) laid out and dense as in OrcaSlicer:
// its pages as underlined text tabs, its option groups (icon, bold title, thin rule) and order, one ~30 px row per
// setting (label, compact input, reset arrow when changed), its labels, tooltips and Simple / Advanced mode
// (orcaLayout.ts). Key, system-preset state, original value and read-only reason are in the row's tooltip.
// What can change, and the checks, come from the backend (WorkspaceSetting.editable, draft.ts).
import { useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Filter, RotateCcw } from 'lucide-react';
import type { WorkspaceSetting } from './api';
import { type Draft, formatValue, valueErrors } from './draft';
import { type OrcaLayout, type Section, orcaIcon, orcaText, placedKeys } from './orcaLayout';
import { SettingControl } from './SettingControl';
import '../i18n';

const UNKNOWN_TAB = '__other__';

interface Props {
  layout: OrcaLayout;
  section: Section;
  settings: WorkspaceSetting[];
  draft: Draft;
  /** The file can be edited at all (a project of a supported slicer). */
  editable: boolean;
  onChange: (setting: WorkspaceSetting, value: WorkspaceSetting['value']) => void;
  onReset: (key: string) => void;
  /** The slicer's Simple mode: only its Simple settings (a changed one stays), and no "Other values" tab. */
  simple?: boolean;
  /** Offer the "Other values" tab: values of this preset the layout places on no page. */
  showUnknown?: boolean;
  /** Above the tabs, left of the search (the preset's name box). */
  header?: ReactNode;
}

interface Row {
  setting: WorkspaceSetting;
  tab: string;
  group: string;
  icon: string | null;
  simple: boolean;
}

function matches(row: Row, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || row.setting.label.toLowerCase().includes(q) || row.setting.key.toLowerCase().includes(q);
}

export function SettingsPanel({
  layout,
  section,
  settings,
  draft,
  editable,
  onChange,
  onReset,
  simple = false,
  showUnknown = true,
  header,
}: Props) {
  const { t, i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const pages = layout.pages[section];
  const withUnknown = showUnknown && !simple;
  // opens on the first page with something to change (the printer's first pages are mostly read only)
  const [chosenTab, setTab] = useState(() => {
    const editableKeys = new Set(settings.filter((s) => s.editable).map((s) => s.key));
    const first = pages.find((p) => p.groups.some((g) => g.keys.some((k) => editableKeys.has(k)))) ?? pages[0];
    return first?.title ?? UNKNOWN_TAB;
  });
  const [query, setQuery] = useState('');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [showReadOnly, setShowReadOnly] = useState(!editable);
  const tabs = [...pages.map((p) => p.title), ...(withUnknown ? [UNKNOWN_TAB] : [])];
  const tab = tabs.includes(chosenTab) ? chosenTab : tabs[0];

  // Every setting of this preset, in OrcaSlicer's order, shown with its label, tooltip and unit.
  const rows = useMemo(() => {
    const byKey = new Map(settings.map((s) => [s.key, s]));
    const lang = i18n.language;
    const show = (s: WorkspaceSetting): WorkspaceSetting => {
      const o = layout.options[s.key];
      if (!o) return s;
      const enumLabel = new Map((o.enum ?? []).map((e) => [e.value, orcaText(layout, e.label, lang)]));
      return {
        ...s,
        label: orcaText(layout, o.label, lang),
        description: o.tooltip ? orcaText(layout, o.tooltip, lang) : s.description,
        unit: s.unit ?? (o.unit ? orcaText(layout, o.unit, lang) : null),
        options: s.options?.map((opt) => ({ ...opt, label: enumLabel.get(opt.value) ?? opt.label })) ?? null,
      };
    };
    const out: Row[] = [];
    for (const page of pages) {
      for (const group of page.groups) {
        for (const key of group.keys) {
          const s = byKey.get(key);
          if (s) {
            const mode = layout.options[key]?.mode;
            out.push({ setting: show(s), tab: page.title, group: group.title, icon: group.icon, simple: mode === 'simple' });
          }
        }
      }
    }
    if (withUnknown) {
      const placed = placedKeys(layout);
      for (const s of settings) {
        // in its own preset: unknown keys (no section) with the process
        if (!placed.has(s.key) && (s.section ?? 'process') === section) {
          out.push({ setting: s, tab: UNKNOWN_TAB, group: '', icon: null, simple: false });
        }
      }
    }
    return out;
  }, [settings, layout, pages, section, withUnknown, i18n.language]);

  const visible = rows
    .filter((r) => (query.trim() ? true : r.tab === tab))
    .filter((r) => matches(r, query))
    .filter((r) => !simple || r.simple || r.setting.key in draft)
    .filter((r) => !onlyChanged || r.setting.key in draft)
    .filter((r) => r.setting.editable || showReadOnly || r.tab === UNKNOWN_TAB);
  const changedPerTab: Record<string, number> = {};
  for (const r of rows) if (r.setting.key in draft) changedPerTab[r.tab] = (changedPerTab[r.tab] ?? 0) + 1;
  const tabLabel = (title: string) => (title === UNKNOWN_TAB ? t('farm.workspace.tabs.unknown') : tr(title));
  const inherit = t('farm.workspace.inherit');

  /** Everything about a row that isn't its label and value, for its tooltip (as OrcaSlicer's hover). */
  const details = (s: WorkspaceSetting, changed: boolean) =>
    [
      s.description,
      s.key,
      changed && t('farm.workspace.original', { value: formatValue(s, s.value, inherit) }),
      s.differs_from_system !== null && s.section
        ? s.differs_from_system
          ? t('farm.workspace.system.modified')
          : t('farm.workspace.system.same')
        : null,
      s.overrides.length > 0 &&
        t('farm.workspace.overrides', {
          list: s.overrides.map((o) => `${o.part && o.part !== o.object ? `${o.object} › ${o.part}` : o.object} = ${o.value}`).join(', '),
        }),
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
                {t('farm.workspace.onlyChanged')}
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
            const active = tab === key && !query.trim();
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
        {tab === UNKNOWN_TAB && !query.trim() && <p className="text-xs text-bambu-gray my-2">{t('farm.workspace.unknownIntro')}</p>}
        {visible.length === 0 && <p className="text-sm text-bambu-gray mt-2">{t('farm.workspace.noResults')}</p>}
        {visible.map(({ setting: s, group, icon }) => {
          const groupIcon = orcaIcon(icon);
          const header =
            group !== lastGroup && group ? (
              <h4 className="flex items-center gap-2 pt-2.5 pb-0.5 text-[13px] font-semibold text-white">
                {groupIcon && <img src={groupIcon} alt="" className="w-4 h-4" aria-hidden />}
                {tr(group)}
                <span className="flex-1 h-px bg-bambu-dark-tertiary" aria-hidden />
              </h4>
            ) : null;
          lastGroup = group;
          const changed = s.key in draft;
          const value = changed ? draft[s.key] : s.value;
          const settingEditable = editable && s.editable;
          return (
            <div key={s.key}>
              {header}
              <div
                className={`flex items-center gap-2 min-h-[30px] py-0.5 pl-4 ${settingEditable ? '' : 'opacity-60'}`}
                title={details(s, changed)}
                data-testid={`setting-${s.key}`}
              >
                <span className={`flex-1 min-w-0 text-[13px] leading-tight ${changed ? 'text-[#F1754E]' : 'text-bambu-gray-light'}`}>
                  {s.label}
                  {s.overrides.length > 0 && <span className="ml-1 text-amber-400" aria-hidden>●</span>}
                </span>
                <SettingControl
                  setting={settingEditable ? s : { ...s, editable: false }}
                  value={value}
                  errors={valueErrors(s, value)}
                  onChange={(v) => onChange(s, v)}
                />
                {changed ? (
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
