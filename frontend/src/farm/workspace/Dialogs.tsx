// Fork (printfarm): the workspace's dialogs: save as a copy (with the list of changes), the printer or
// filament settings of the project, edited apart from the process as in the slicer clients (the filament window
// edits one filament, as OrcaSlicer's), and OrcaSlicer's keyboard shortcuts list ("?"). Esc closes them.
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Save, X } from 'lucide-react';
import { Button } from '../../components/Button';
import type { SettingValue, WorkspaceSetting } from './api';
import { type Draft, formatValue } from './draft';
import { type OrcaLayout, type ShortcutRow, keyText, orcaIcon, orcaText } from './orcaLayout';
import { SettingsPanel } from './SettingsPanel';
import '../i18n';

function Dialog({
  title,
  intro,
  wide,
  onClose,
  children,
}: {
  title: string;
  intro?: string;
  wide?: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  useEffect(() => {
    // a list open inside the dialog takes its own Esc first (it stops the event)
    const close = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div className={`w-full ${wide ? 'max-w-2xl' : 'max-w-lg'} max-h-[90vh] flex flex-col rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary`}>
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-start gap-2">
          <div className="flex-1">
            <h2 className="text-lg font-semibold">{title}</h2>
            {intro && <p className="text-sm text-bambu-gray mt-1">{intro}</p>}
          </div>
          <button onClick={onClose} aria-label={t('farm.workspace.close')} className="p-1 rounded text-bambu-gray hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

interface SaveProps {
  filename: string;
  draft: Draft;
  settings: Map<string, WorkspaceSetting>;
  invalid: string[];
  saving: boolean;
  error: unknown;
  onSave: (name: string) => void;
  onClose: () => void;
}

export function SaveCopyDialog({ filename, draft, settings, invalid, saving, error, onSave, onClose }: SaveProps) {
  const { t, i18n } = useTranslation();
  const [name, setName] = useState('');
  const inherit = t('farm.workspace.inherit');
  return (
    <Dialog title={t('farm.workspace.save.title')} intro={t('farm.workspace.save.intro')} onClose={saving ? () => {} : onClose}>
      <ul className="p-4 space-y-1 overflow-y-auto text-sm">
        {Object.entries(draft).map(([key, value]) => {
          const s = settings.get(key)!;
          return (
            <li key={key} className={invalid.includes(key) ? 'text-red-400' : ''}>
              <span className="text-white">{s.label}</span>{' '}
              <span className="text-bambu-gray">
                {formatValue(s, s.value, inherit, i18n.language)} → {formatValue(s, value, inherit, i18n.language)}
              </span>
            </li>
          );
        })}
      </ul>
      <div className="p-4 border-t border-bambu-dark-tertiary space-y-3">
        <label className="block text-sm">
          <span className="text-bambu-gray">{t('farm.workspace.save.name')}</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('farm.workspace.save.namePlaceholder', { name: filename.replace(/\.3mf$/i, '') })}
            className="mt-1 w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1.5 text-white"
          />
        </label>
        {invalid.length > 0 && <p className="text-sm text-red-400">{t('farm.workspace.save.invalid')}</p>}
        {error != null && (
          <p className="text-sm text-red-400" role="alert">
            {error instanceof Error ? error.message : String(error)}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            {t('farm.workspace.close')}
          </Button>
          <Button onClick={() => onSave(name.trim())} disabled={invalid.length > 0 || saving}>
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {saving ? t('farm.workspace.save.saving') : t('farm.workspace.save.confirm')}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

interface PresetProps {
  layout: OrcaLayout;
  kind: 'printer' | 'filament';
  /** The filament the window edits (its element of every per-filament value). */
  filament?: number;
  name: string | null;
  settings: WorkspaceSetting[];
  draft: Draft;
  editable: boolean;
  onChange: (setting: WorkspaceSetting, value: SettingValue) => void;
  onReset: (key: string) => void;
  onClose: () => void;
}

export function PresetDialog({ layout, kind, filament = 0, name, settings, draft, editable, onChange, onReset, onClose }: PresetProps) {
  const { t } = useTranslation();
  const at = (key: string, i: number) => {
    const v = settings.find((s) => s.key === key)?.value;
    return (Array.isArray(v) ? (v[i] ?? v[0]) : v) ?? null;
  };
  const preset = kind === 'filament' ? at('filament_settings_id', filament) : (name ?? at('printer_settings_id', 0));
  const colour = kind === 'filament' ? at('filament_colour', filament) : null;
  // multi-extruder printers (Direct Drive / Bowden, SF / HF...): shown only when the project has variants
  const raw = settings.find((s) => s.key === `${kind}_extruder_variant`)?.value;
  const variants = [...new Set((Array.isArray(raw) ? raw : raw ? [raw] : []).filter(Boolean))];
  return (
    <Dialog
      title={t(kind === 'filament' ? 'farm.workspace.materialSettings' : 'farm.workspace.printerSettings')}
      intro={t('farm.workspace.presetIntro')}
      onClose={onClose}
    >
      <div className="h-[60vh] min-h-0">
        <SettingsPanel
          layout={layout}
          section={kind}
          settings={settings}
          draft={draft}
          editable={editable}
          onChange={onChange}
          onReset={onReset}
          index={kind === 'filament' ? filament : undefined}
          header={
            <>
              <div className="flex items-center gap-2 rounded border border-bambu-dark-tertiary bg-bambu-dark-secondary px-2 h-7 text-[13px] text-white">
                {kind === 'filament' && (
                  <span
                    className="inline-flex items-center justify-center w-5 h-5 rounded-sm border border-white/30 text-[11px] font-semibold text-black"
                    style={{ backgroundColor: colour ?? '#777' }}
                  >
                    {filament + 1}
                  </span>
                )}
                <span className="truncate">{preset ?? '—'}</span>
              </div>
              {variants.length > 1 && (
                <div className="flex flex-wrap gap-1 text-[11px]" aria-label={t('farm.workspace.variant')}>
                  {variants.map((v, i) => (
                    <span key={v} className={`px-2 py-0.5 rounded-full ${i === 0 ? 'bg-bambu-green text-white' : 'text-bambu-gray'}`}>
                      {v}
                    </span>
                  ))}
                </div>
              )}
            </>
          }
        />
      </div>
    </Dialog>
  );
}

interface ShortcutsProps {
  layout: OrcaLayout;
  /** the page shown first (a ShortcutContext: Plater for Prepare, Preview) */
  context: string;
  /** whether the workspace does what a row says (the others come with later phases) */
  works: (row: ShortcutRow) => boolean;
  onClose: () => void;
}

/** OrcaSlicer's "Keyboard Shortcuts" dialog (KBShortcutsDialog.cpp): a tab per page, its note, its rows by section
 * with their keys. It only lists them: the keys can't be changed here. */
export function ShortcutsDialog({ layout, context, works, onClose }: ShortcutsProps) {
  const { t, i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const pages = layout.shortcuts ?? [];
  const [shown, setShown] = useState(() => Math.max(0, pages.findIndex((p) => p.context === context)));
  const page = pages[shown];
  const soon = t('farm.workspace.gizmos.soon');
  return (
    <Dialog title={tr('Keyboard Shortcuts')} wide onClose={onClose}>
      <div className="flex gap-1 px-4 pt-2 border-b border-bambu-dark-tertiary" role="tablist">
        {pages.map((p, i) => (
          <button
            key={p.context}
            role="tab"
            aria-selected={i === shown}
            onClick={() => setShown(i)}
            className={`-mb-px px-3 py-1.5 text-[13px] border-b-2 ${
              i === shown ? 'border-bambu-green text-white font-semibold' : 'border-transparent text-bambu-gray hover:text-white'
            }`}
          >
            {tr(p.title)}
          </button>
        ))}
      </div>
      {page && (
        <div className="overflow-y-auto px-4 pb-4 text-[13px]" role="tabpanel" aria-label={tr(page.title)}>
          <p className="mt-3 flex items-center gap-2 rounded bg-bambu-dark px-3 py-2 text-bambu-gray-light">
            <img src={orcaIcon('help')} alt="" className="w-4 h-4 invert" aria-hidden />
            {tr(page.caption)}
          </p>
          {page.rows.map((row, i) => (
            <div key={i}>
              {(i === 0 || page.rows[i - 1].section !== row.section) && (
                <h3 className="mt-3 mb-1 border-b border-bambu-dark-tertiary pb-1 font-semibold text-white">{tr(row.section)}</h3>
              )}
              <div
                className={`flex items-center gap-3 py-0.5 ${works(row) ? 'text-bambu-gray-light' : 'text-bambu-gray/60'}`}
                title={works(row) ? undefined : soon}
              >
                <span className="flex-1">{tr(row.label)}</span>
                <span className="flex flex-wrap justify-end gap-1">
                  {row.keys.map((k, j) => (
                    <kbd key={j} className="rounded border border-bambu-dark-tertiary bg-bambu-dark px-1.5 font-sans text-[12px] text-white">
                      {keyText(layout, k, i18n.language)}
                    </kbd>
                  ))}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Dialog>
  );
}
