// Fork (printfarm): the input of one project setting, in the format the 3MF stores it (one value or one per
// filament / extruder). Read-only settings show their value as text.
import { useTranslation } from 'react-i18next';
import type { SettingValue, WorkspaceSetting } from './api';
import { NIL, ON, formatValue, type ValueError } from './draft';
import '../i18n';

interface Props {
  setting: WorkspaceSetting;
  value: SettingValue;
  errors: (ValueError | null)[];
  onChange: (value: SettingValue) => void;
}

export function SettingControl({ setting, value, errors, onChange }: Props) {
  const { t } = useTranslation();
  const values = Array.isArray(value) ? value : [value];
  const originals = Array.isArray(setting.value) ? setting.value : [setting.value];
  const many = Array.isArray(setting.value);

  if (!setting.editable) {
    return (
      <span className="text-sm text-bambu-gray-light break-all">
        {formatValue(setting, setting.value, t('farm.workspace.inherit'))}
      </span>
    );
  }

  const change = (i: number, next: string) => {
    const copy = [...values];
    copy[i] = next;
    onChange(many ? copy : copy[0]);
  };
  const label = (i: number) =>
    setting.section === 'filament' ? t('farm.workspace.filament', { n: i + 1 }) : t('farm.workspace.extruder', { n: i + 1 });

  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      {values.map((v, i) => {
        const error = errors[i];
        const id = `ws-${setting.key}-${i}`;
        const aria = many ? `${setting.label} — ${label(i)}` : setting.label;
        let input;
        if (setting.type === 'bool') {
          const on = ON.has(v);
          const style = originals[i] === 'true' || originals[i] === 'false' ? ['true', 'false'] : ['1', '0'];
          input = (
            <input
              id={id}
              type="checkbox"
              aria-label={aria}
              className="w-4 h-4 accent-bambu-green"
              checked={on}
              onChange={(e) => change(i, e.target.checked ? style[0] : style[1])}
            />
          );
        } else if (setting.type === 'enum') {
          input = (
            <select
              id={id}
              aria-label={aria}
              value={v}
              onChange={(e) => change(i, e.target.value)}
              className="w-full min-w-0 bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1 text-sm text-white"
            >
              {v === NIL && <option value={NIL}>{t('farm.workspace.inherit')}</option>}
              {(setting.options ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          );
        } else {
          input = (
            <input
              id={id}
              type="text"
              inputMode={setting.type === 'int' ? 'numeric' : 'decimal'}
              aria-label={aria}
              aria-invalid={error ? true : undefined}
              value={v === NIL ? '' : v}
              placeholder={v === NIL ? t('farm.workspace.inherit') : undefined}
              onChange={(e) => change(i, e.target.value === '' && originals[i] === NIL ? NIL : e.target.value)}
              className={`w-24 bg-bambu-dark border rounded px-2 py-1 text-sm text-white ${
                error ? 'border-red-500' : 'border-bambu-dark-tertiary'
              }`}
            />
          );
        }
        return (
          <div key={i} className="flex items-center gap-2 flex-wrap">
            {many && <span className="text-xs text-bambu-gray w-20 flex-shrink-0">{label(i)}</span>}
            {input}
            {setting.unit && setting.type !== 'bool' && <span className="text-xs text-bambu-gray">{setting.unit}</span>}
            {error && (
              <span className="text-xs text-red-400" role="alert">
                {t(`farm.workspace.errors.${error}`, { min: setting.min, max: setting.max })}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
