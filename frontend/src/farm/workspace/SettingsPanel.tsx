// Fork (printfarm): the workspace's right column: the project's slicing settings by slicer tab, search,
// original and changed values, per-setting restore.
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCcw, Search } from 'lucide-react';
import type { WorkspaceSetting } from './api';
import { type Draft, formatValue, valueErrors } from './draft';
import { SettingControl } from './SettingControl';
import '../i18n';

const UNKNOWN_TAB = 'unknown';

interface Props {
  settings: WorkspaceSetting[];
  tabs: string[];
  draft: Draft;
  /** The file can be edited at all (a project of a supported slicer). */
  editable: boolean;
  onChange: (setting: WorkspaceSetting, value: WorkspaceSetting['value']) => void;
  onReset: (key: string) => void;
}

const tabOf = (s: WorkspaceSetting) => s.tab ?? UNKNOWN_TAB;

function matches(setting: WorkspaceSetting, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || setting.label.toLowerCase().includes(q) || setting.key.toLowerCase().includes(q);
}

export function SettingsPanel({ settings, tabs, draft, editable, onChange, onReset }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState(tabs[0] ?? UNKNOWN_TAB);
  const [query, setQuery] = useState('');
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [showReadOnly, setShowReadOnly] = useState(!editable);
  const allTabs = [...tabs, UNKNOWN_TAB];

  const visible = useMemo(
    () =>
      settings
        .filter((s) => (query.trim() ? true : tabOf(s) === tab))
        .filter((s) => matches(s, query))
        .filter((s) => !onlyChanged || s.key in draft)
        .filter((s) => s.editable || showReadOnly || tabOf(s) === UNKNOWN_TAB)
        .sort((a, b) => (a.group ?? '').localeCompare(b.group ?? '') || a.label.localeCompare(b.label)),
    [settings, tab, query, onlyChanged, showReadOnly, draft],
  );
  const changedPerTab = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of settings) if (s.key in draft) counts[tabOf(s)] = (counts[tabOf(s)] ?? 0) + 1;
    return counts;
  }, [settings, draft]);

  let lastGroup: string | null | undefined;
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="p-3 space-y-2 border-b border-bambu-dark-tertiary">
        <label className="relative block">
          <Search className="w-4 h-4 absolute left-2 top-2 text-bambu-gray" aria-hidden />
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
          {allTabs.map((key) => (
            <button
              key={key}
              role="tab"
              aria-selected={tab === key && !query.trim()}
              onClick={() => {
                setTab(key);
                setQuery('');
              }}
              className={`px-2 py-1 rounded text-xs ${
                tab === key && !query.trim()
                  ? 'bg-bambu-green text-white'
                  : 'bg-bambu-dark-tertiary text-bambu-gray-light hover:text-white'
              }`}
            >
              {t(`farm.workspace.tabs.${key}`, key)}
              {changedPerTab[key] ? ` · ${changedPerTab[key]}` : ''}
            </button>
          ))}
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
        {visible.map((s) => {
          const header =
            s.group !== lastGroup && s.group ? (
              <h4 className="pt-3 pb-1 text-xs font-semibold uppercase tracking-wide text-bambu-gray">
                {t(`farm.workspace.groups.${s.group}`, s.group)}
              </h4>
            ) : null;
          lastGroup = s.group;
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
                      {changed && <span className="ml-2 text-xs text-bambu-green">● {t('farm.workspace.changed')}</span>}
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
                        list: s.overrides.map((o) => `${o.part && o.part !== o.object ? `${o.object} › ${o.part}` : o.object} = ${o.value}`).join(', '),
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
