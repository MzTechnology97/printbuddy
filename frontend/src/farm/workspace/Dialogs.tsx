// Fork (printfarm): the workspace's dialogs: save as a copy (with the list of changes), and the printer or
// filament settings of the project, edited apart from the process as in the slicer clients.
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Save, X } from 'lucide-react';
import { Button } from '../../components/Button';
import type { SettingValue, WorkspaceSetting } from './api';
import { type Draft, formatValue } from './draft';
import type { OrcaLayout } from './orcaLayout';
import { SettingsPanel } from './SettingsPanel';
import '../i18n';

function Dialog({ title, intro, onClose, children }: { title: string; intro?: string; onClose: () => void; children: React.ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div className="w-full max-w-lg max-h-[90vh] flex flex-col rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary">
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
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const inherit = t('farm.workspace.inherit');
  return (
    <Dialog title={t('farm.workspace.save.title')} intro={t('farm.workspace.save.intro')} onClose={onClose}>
      <ul className="p-4 space-y-1 overflow-y-auto text-sm">
        {Object.entries(draft).map(([key, value]) => {
          const s = settings.get(key)!;
          return (
            <li key={key} className={invalid.includes(key) ? 'text-red-400' : ''}>
              <span className="text-white">{s.label}</span>{' '}
              <span className="text-bambu-gray">
                {formatValue(s, s.value, inherit)} → {formatValue(s, value, inherit)}
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
  name: string | null;
  settings: WorkspaceSetting[];
  draft: Draft;
  editable: boolean;
  onChange: (setting: WorkspaceSetting, value: SettingValue) => void;
  onReset: (key: string) => void;
  onClose: () => void;
}

export function PresetDialog({ layout, kind, name, settings, draft, editable, onChange, onReset, onClose }: PresetProps) {
  const { t } = useTranslation();
  const title = t(`farm.workspace.tabs.${kind}`) + (name ? ` · ${name}` : '');
  return (
    <Dialog title={title} intro={t('farm.workspace.presetIntro')} onClose={onClose}>
      <div className="h-[60vh] min-h-0">
        <SettingsPanel
          settings={settings}
          layout={layout}
          section={kind}
          draft={draft}
          editable={editable}
          onChange={onChange}
          onReset={onReset}
          showUnknown={false}
        />
      </div>
    </Dialog>
  );
}
