// Fork (printfarm): the input of one project setting, compact as in OrcaSlicer: about 120 px wide with the unit
// inside on the right; one per filament / extruder when the project stores a list. Read-only settings show their
// value greyed in place.
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

const BOX = 'h-7 w-[120px] rounded border bg-bambu-dark text-[13px] text-white';

export function SettingControl({ setting, value, errors, onChange }: Props) {
  const { t } = useTranslation();
  const values = Array.isArray(value) ? value : [value];
  const originals = Array.isArray(setting.value) ? setting.value : [setting.value];
  const many = Array.isArray(setting.value);
  const inherit = t('farm.workspace.inherit');

  if (!setting.editable) {
    return (
      <span className={`${BOX} inline-flex items-center px-2 border-bambu-dark-tertiary text-bambu-gray truncate`}>
        <span className="truncate">{formatValue(setting, setting.value, inherit)}</span>
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
    <div className="flex flex-col gap-1">
      {values.map((v, i) => {
        const error = errors[i];
        const message = error ? t(`farm.workspace.errors.${error}`, { min: setting.min, max: setting.max }) : undefined;
        const aria = many ? `${setting.label} — ${label(i)}` : setting.label;
        const border = error ? 'border-red-500' : 'border-bambu-dark-tertiary focus-within:border-bambu-green';
        let input;
        if (setting.type === 'bool') {
          const style = originals[i] === 'true' || originals[i] === 'false' ? ['true', 'false'] : ['1', '0'];
          input = (
            <input
              type="checkbox"
              aria-label={aria}
              className="w-4 h-4 accent-bambu-green"
              checked={ON.has(v)}
              onChange={(e) => change(i, e.target.checked ? style[0] : style[1])}
            />
          );
        } else if (setting.type === 'enum') {
          input = (
            <select aria-label={aria} value={v} onChange={(e) => change(i, e.target.value)} className={`${BOX} ${border} px-1`}>
              {v === NIL && <option value={NIL}>{inherit}</option>}
              {(setting.options ?? []).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          );
        } else {
          input = (
            <span className={`${BOX} ${border} relative inline-flex items-center`}>
              <input
                type="text"
                inputMode={setting.type === 'int' ? 'numeric' : 'decimal'}
                aria-label={aria}
                aria-invalid={error ? true : undefined}
                title={message}
                value={v === NIL ? '' : v}
                placeholder={v === NIL ? inherit : undefined}
                onChange={(e) => change(i, e.target.value === '' && originals[i] === NIL ? NIL : e.target.value)}
                className="h-full w-full min-w-0 bg-transparent pl-2 pr-12 outline-none"
              />
              {setting.unit && (
                <span className="pointer-events-none absolute right-1.5 text-[11px] text-bambu-gray">{setting.unit}</span>
              )}
            </span>
          );
        }
        return (
          <div key={i} className="flex items-center gap-1.5">
            {many && (
              <span className="w-4 text-center text-[11px] text-bambu-gray" title={label(i)}>
                {i + 1}
              </span>
            )}
            {input}
            {message && (
              <span className="sr-only" role="alert">
                {message}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
