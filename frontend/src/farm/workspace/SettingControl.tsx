// Fork (printfarm): the input of one project setting, compact as in OrcaSlicer: about 120 px wide with the unit
// inside on the right; a list shows each value's OrcaSlicer icon (patterns) in a listbox of its own, as the
// slicer's lists do. One input per filament / extruder when the project stores a list, or only the one of
// ``index`` (the filament window edits one filament). Read-only and greyed settings show their value in place.
// Numbers are written with the UI language's decimal separator; the 3MF keeps the point.
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown } from 'lucide-react';
import type { SettingValue, WorkspaceSetting } from './api';
import { NIL, ON, formatValue, fromLocaleDecimal, toLocaleDecimal, type ValueError } from './draft';
import { orcaIcon } from './orcaLayout';
import '../i18n';

interface Props {
  setting: WorkspaceSetting;
  value: SettingValue;
  errors: (ValueError | null)[];
  onChange: (value: SettingValue) => void;
  /** Only this element of a per-filament / per-extruder value. */
  index?: number;
  /** Greyed and not editable: the setting it depends on is off (as OrcaSlicer). */
  disabled?: boolean;
}

const BOX = 'h-7 w-[120px] rounded border bg-bambu-dark text-[13px] text-white';

type Option = { value: string; label: string; icon?: string };

function OptionIcon({ icon }: { icon?: string }) {
  const src = orcaIcon(icon);
  return src ? <img src={src} alt="" className="w-4 h-4 flex-shrink-0" aria-hidden /> : null;
}

/** A select-only combobox (WAI-ARIA): arrows, Home / End, Enter or Space, Esc, type-ahead; the list floats
 * over the panel so a scrolling settings list doesn't clip it. */
export function EnumSelect({
  label,
  value,
  options,
  invalid,
  onChange,
}: {
  label: string;
  value: string;
  options: Option[];
  invalid?: boolean;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const box = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const typed = useRef({ text: '', at: 0 });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [place, setPlace] = useState<React.CSSProperties>({});
  const selected = Math.max(0, options.findIndex((o) => o.value === value));
  const current = options.find((o) => o.value === value);

  const show = () => {
    const r = box.current?.getBoundingClientRect();
    if (r) {
      const below = window.innerHeight - r.bottom;
      const height = Math.min(288, options.length * 28 + 8);
      setPlace(
        below >= height || below >= r.top
          ? { left: r.left, top: r.bottom + 2, minWidth: r.width, maxHeight: Math.max(below - 8, 120) }
          : { left: r.left, bottom: window.innerHeight - r.top + 2, minWidth: r.width, maxHeight: r.top - 8 },
      );
    }
    setActive(selected);
    setOpen(true);
  };
  const choose = (i: number) => {
    if (options[i] && options[i].value !== value) onChange(options[i].value);
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [open, active]);
  useEffect(() => {
    if (!open) return;
    const outside = (e: Event) => {
      const t = e.target as Node;
      if (!box.current?.contains(t) && !list.current?.contains(t)) setOpen(false);
    };
    document.addEventListener('mousedown', outside);
    window.addEventListener('scroll', outside, true); // the panel scrolled: the list would float away
    window.addEventListener('resize', outside);
    return () => {
      document.removeEventListener('mousedown', outside);
      window.removeEventListener('scroll', outside, true);
      window.removeEventListener('resize', outside);
    };
  }, [open]);

  const onKey = (e: React.KeyboardEvent) => {
    const last = options.length - 1;
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
        e.preventDefault();
        show();
      }
      return;
    }
    const move: Record<string, number> = { ArrowDown: Math.min(active + 1, last), ArrowUp: Math.max(active - 1, 0), Home: 0, End: last };
    if (e.key in move) {
      e.preventDefault();
      setActive(move[e.key]);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      choose(active);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation(); // closes the list, not the dialog around it
      setOpen(false);
    } else if (e.key === 'Tab') {
      setOpen(false);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const now = Date.now();
      typed.current = { text: (now - typed.current.at < 600 ? typed.current.text : '') + e.key.toLowerCase(), at: now };
      const text = typed.current.text;
      const order = [...options.keys()].map((k) => (active + (text.length === 1 ? 1 : 0) + k) % options.length);
      const hit = order.find((i) => options[i].label.toLowerCase().startsWith(text));
      if (hit != null) setActive(hit);
    }
  };

  return (
    <>
      <button
        ref={box}
        type="button"
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-activedescendant={open ? `${id}-${active}` : undefined}
        aria-invalid={invalid || undefined}
        title={current?.label}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onKey}
        className={`${BOX} ${invalid ? 'border-red-500' : 'border-bambu-dark-tertiary focus:border-bambu-green'} inline-flex items-center gap-1.5 pl-1.5 pr-1 outline-none text-left`}
      >
        <OptionIcon icon={current?.icon} />
        <span className="flex-1 min-w-0 truncate">{current?.label ?? value}</span>
        <ChevronDown className="w-3 h-3 flex-shrink-0 text-bambu-gray" aria-hidden />
      </button>
      {open && (
        <ul
          ref={list}
          id={`${id}-list`}
          role="listbox"
          aria-label={label}
          style={{ position: 'fixed', ...place }}
          className="z-[60] overflow-y-auto rounded border border-bambu-dark-tertiary bg-bambu-dark-secondary py-1 shadow-xl"
        >
          {options.map((o, i) => (
            <li
              key={o.value}
              id={`${id}-${i}`}
              data-index={i}
              role="option"
              aria-selected={o.value === value}
              onMouseDown={(e) => e.preventDefault()} // keeps the focus on the combobox
              onMouseEnter={() => setActive(i)}
              onClick={() => choose(i)}
              className={`flex items-center gap-2 h-7 px-2 text-[13px] cursor-pointer whitespace-nowrap ${
                i === active ? 'bg-bambu-green/30 text-white' : o.value === value ? 'text-bambu-green-light' : 'text-bambu-gray-light'
              }`}
            >
              <OptionIcon icon={o.icon} />
              {o.label}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function SettingControl({ setting, value, errors, onChange, index, disabled = false }: Props) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const values = Array.isArray(value) ? value : [value];
  const originals = Array.isArray(setting.value) ? setting.value : [setting.value];
  const many = Array.isArray(setting.value);
  const shown = many && index != null ? [index] : values.map((_, i) => i);
  const inherit = t('farm.workspace.inherit');

  if (!setting.editable || disabled) {
    const v = Array.isArray(value) ? value[index ?? 0] : value;
    const icon = setting.type === 'enum' ? setting.options?.find((o) => o.value === v)?.icon : undefined;
    const text = formatValue(setting, value, inherit, lang, index);
    if (setting.type === 'bool') {
      return <input type="checkbox" disabled checked={ON.has(v ?? '')} aria-label={setting.label} title={text} className="w-4 h-4 accent-bambu-green opacity-70" />;
    }
    return (
      <span
        className={`${BOX} inline-flex items-center gap-1.5 px-2 border-bambu-dark-tertiary text-bambu-gray truncate`}
        aria-disabled={disabled || undefined}
        title={text}
      >
        <OptionIcon icon={icon} />
        <span className="truncate">{text}</span>
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
    <div className={`flex flex-col gap-1 ${setting.multiline ? 'w-full' : ''}`}>
      {shown.map((i) => {
        const v = values[i] ?? '';
        const error = errors[i];
        const message = error ? t(`farm.workspace.errors.${error}`, { min: setting.min, max: setting.max }) : undefined;
        const aria = many && index == null ? `${setting.label} — ${label(i)}` : setting.label;
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
          const options = [...(v === NIL ? [{ value: NIL, label: inherit }] : []), ...(setting.options ?? [])];
          input = <EnumSelect label={aria} value={v} options={options} invalid={!!error} onChange={(next) => change(i, next)} />;
        } else if (setting.type === 'string' && setting.multiline) {
          // G-code and notes: the full width of the row, as OrcaSlicer's text boxes
          input = (
            <textarea
              aria-label={aria}
              aria-invalid={error ? true : undefined}
              title={message}
              value={v}
              spellCheck={false}
              onChange={(e) => change(i, e.target.value)}
              className={`flex-1 min-w-0 h-36 rounded border bg-bambu-dark p-1.5 font-mono text-[12px] leading-snug text-white outline-none resize-y ${border}`}
            />
          );
        } else if (setting.type === 'string') {
          input = (
            <span className={`${BOX} ${border} w-[180px] inline-flex items-center`}>
              <input
                type="text"
                aria-label={aria}
                aria-invalid={error ? true : undefined}
                title={message ?? v}
                value={v}
                onChange={(e) => change(i, e.target.value)}
                className="h-full w-full min-w-0 bg-transparent px-2 outline-none"
              />
            </span>
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
                value={v === NIL ? '' : toLocaleDecimal(v, lang)}
                placeholder={v === NIL ? inherit : undefined}
                onChange={(e) =>
                  change(i, e.target.value === '' && originals[i] === NIL ? NIL : fromLocaleDecimal(e.target.value, lang))
                }
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
            {many && index == null && (
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
