// Fork (printfarm): the settings of one preset (process, filament or printer) laid out as OrcaSlicer does: its
// pages as tabs, its option groups and order, its labels, tooltips and Simple / Advanced mode (orcaLayout.ts),
// with search, original and changed values and per-setting restore. What can change, and the checks, come
// from the backend (WorkspaceSetting.editable, draft.ts).
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCcw } from 'lucide-react';
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
  /** Offer the "Other values" tab: values of the project the layout places on no page. */
  showUnknown?: boolean;
}

interface Row {
  setting: WorkspaceSetting;
  tab: string;
  group: string;
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
          if (s) out.push({ setting: show(s), tab: page.title, group: group.title, simple: layout.options[key]?.mode === 'simple' });
        }
      }
    }
    if (withUnknown) {
      const placed = placedKeys(layout);
      for (const s of settings) {
        if (!placed.has(s.key) && (section === 'process' ? true : s.section === section)) {
          out.push({ setting: s, tab: UNKNOWN_TAB, group: '', simple: false });
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

  let lastGroup: string | undefined;
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="p-3 space-y-2 border-b border-bambu-dark-tertiary">
        <label className="relative block">
          <img src={orcaIcon('search')} alt="" className="w-4 h-4 absolute left-2 top-2 opacity-70" aria-hidden />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('farm.workspace.search')}
            aria-label={t('farm.workspace.search')}
            className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded pl-8 pr-2 py-1.5 text-sm text-white"
          />
        </label>
        <div className="flex flex-wrap gap-1" role="tablist">
          {tabs.map((key) => {
            const page = pages.find((p) => p.title === key);
            const icon = orcaIcon(page?.icon);
            return (
              <button
                key={key}
                role="tab"
                aria-selected={tab === key && !query.trim()}
                onClick={() => {
                  setTab(key);
                  setQuery('');
                }}
                className={`flex items-center gap-1 px-2 py-1 rounded text-xs ${
                  tab === key && !query.trim()
                    ? 'bg-bambu-green text-white'
                    : 'bg-bambu-dark-tertiary text-bambu-gray-light hover:text-white'
                }`}
              >
                {icon && <img src={icon} alt="" className="w-3.5 h-3.5" aria-hidden />}
                {tabLabel(key)}
                {changedPerTab[key] ? ` · ${changedPerTab[key]}` : ''}
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap gap-4 text-xs text-bambu-gray-light">
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
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-1">
        {tab === UNKNOWN_TAB && !query.trim() && <p className="text-xs text-bambu-gray mb-2">{t('farm.workspace.unknownIntro')}</p>}
        {visible.length === 0 && <p className="text-sm text-bambu-gray">{t('farm.workspace.noResults')}</p>}
        {visible.map(({ setting: s, group }) => {
          const header =
            group !== lastGroup && group ? (
              <h4 className="pt-3 pb-1 text-xs font-semibold uppercase tracking-wide text-bambu-gray">{tr(group)}</h4>
            ) : null;
          lastGroup = group;
          const changed = s.key in draft;
          const value = changed ? draft[s.key] : s.value;
          const settingEditable = editable && s.editable;
          return (
            <div key={s.key}>
              {header}
              <div
                className={`rounded-md px-2 py-2 ${changed ? 'bg-bambu-green/10 ring-1 ring-bambu-green/40' : 'hover:bg-bambu-dark/40'}`}
                data-testid={`setting-${s.key}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm text-white" title={s.description ?? undefined}>
                      {s.label}
                      {changed && <span className="ml-2 text-xs text-[#F1754E]">● {t('farm.workspace.changed')}</span>}
                    </div>
                    <div className="text-[11px] font-mono text-bambu-gray break-all">{s.key}</div>
                  </div>
                  {changed && (
                    <button
                      onClick={() => onReset(s.key)}
                      title={t('farm.workspace.reset')}
                      aria-label={`${t('farm.workspace.reset')}: ${s.label}`}
                      className="p-1 rounded text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary flex-shrink-0"
                    >
                      <RotateCcw className="w-4 h-4" />
                    </button>
                  )}
                </div>
                <div className="mt-1.5">
                  <SettingControl
                    setting={settingEditable ? s : { ...s, editable: false }}
                    value={value}
                    errors={valueErrors(s, value)}
                    onChange={(v) => onChange(s, v)}
                  />
                </div>
                <div className="mt-1 space-y-0.5 text-[11px] text-bambu-gray">
                  {changed && <div>{t('farm.workspace.original', { value: formatValue(s, s.value, t('farm.workspace.inherit')) })}</div>}
                  {s.differs_from_system !== null && s.section && (
                    <div>{s.differs_from_system ? t('farm.workspace.system.modified') : t('farm.workspace.system.same')}</div>
                  )}
                  {s.overrides.length > 0 && (
                    <div className="text-amber-400">
                      {t('farm.workspace.overrides', {
                        list: s.overrides
                          .map((o) => `${o.part && o.part !== o.object ? `${o.object} › ${o.part}` : o.object} = ${o.value}`)
                          .join(', '),
                      })}
                    </div>
                  )}
                  {!s.editable && s.readonly_reason && editable && <div>{t(`farm.workspace.readonly.${s.readonly_reason}`)}</div>}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
