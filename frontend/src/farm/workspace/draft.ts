// Fork (printfarm): the workspace's unsaved changes. Pure functions, so they're tested apart from the UI.
// The backend checks every value again (farm/workspace/settings.py); this only spares a round trip.
import { useCallback, useMemo, useState } from 'react';
import type { SettingValue, WorkspaceSetting } from './api';

export const NIL = 'nil';
export type Draft = Record<string, SettingValue>;
export type ValueError = 'number' | 'integer' | 'min' | 'max' | 'percent' | 'option' | 'inherit' | 'control' | 'length' | 'point';
/** As the backend (settings.MAX_TEXT): one text or G-code value */
const MAX_TEXT = 256 * 1024;

export const ON = new Set(['1', 'true']);

const DECIMAL = /^-?\d*\.\d*%?$/; // with "0." as typed ("0,"), so the comma stays on screen
const DECIMAL_COMMA = /^-?\d*,\d*%?$/;

/** A number as the UI language writes it (``0,2`` in Italian); the 3MF keeps the point. Other text as it is. */
export function toLocaleDecimal(value: string, language: string | undefined): string {
  return language?.startsWith('it') && DECIMAL.test(value.trim()) ? value.replace('.', ',') : value;
}

/** What was typed, back to the 3MF's point (``0,2`` -> ``0.2``); other text as it is. */
export function fromLocaleDecimal(text: string, language: string | undefined): string {
  return language?.startsWith('it') && DECIMAL_COMMA.test(text.trim()) ? text.replace(',', '.') : text;
}

/** ``index``: only that element of a per-filament / per-extruder value. */
export function formatValue(
  setting: WorkspaceSetting,
  value: SettingValue,
  inherit: string,
  language?: string,
  index?: number,
): string {
  const one = (v: string) => {
    if (v === NIL) return inherit;
    if (setting.type === 'bool') return ON.has(v) ? '✓' : '✗';
    if (setting.type === 'enum') return setting.options?.find((o) => o.value === v)?.label ?? v;
    return toLocaleDecimal(v, language);
  };
  if (Array.isArray(value) && index != null) return one(value[index] ?? '');
  return Array.isArray(value) ? value.map(one).join(' / ') : one(value);
}

/** ``exact``: text and G-code, where spaces and line breaks are part of the value. */
export function sameValue(a: SettingValue, b: SettingValue, exact = false): boolean {
  const same = (x: string, y: string) => (exact ? x === y : x.trim() === y.trim());
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => same(v, b[i]));
  }
  return same(a, b);
}

/** Why one element of a new value would be refused (null: it fits), compared with its original element. */
export function elementError(setting: WorkspaceSetting, value: string, original: string): ValueError | null {
  if (setting.type === 'string') {
    if (value === original) return null;
    if (new TextEncoder().encode(value).length > MAX_TEXT) return 'length';
    // eslint-disable-next-line no-control-regex -- the control characters a slicer can't read back
    const control = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(value) || (!setting.multiline && /[\r\n]/.test(value));
    return control ? 'control' : null;
  }
  const text = value.trim();
  if (text === original) return null;
  if (text === NIL) return 'inherit';
  if (setting.type === 'bool') return null; // a checkbox: always 0/1 (or true/false)
  if (setting.type === 'enum') return setting.options?.some((o) => o.value === text) || (setting.open && text) ? null : 'option';
  if (setting.type === 'point') return /^-?(\d+\.?\d*|\.\d+)(x-?(\d+\.?\d*|\.\d+)){1,2}$/.test(text) ? null : 'point';
  if (text.endsWith('%') && !setting.percent && !original.endsWith('%')) return 'percent';
  const numberText = text.replace(/%$/, '');
  if (setting.type === 'int' ? !/^-?\d+$/.test(numberText) : !/^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(numberText)) {
    return setting.type === 'int' ? 'integer' : 'number';
  }
  const number = Number(numberText);
  if (setting.min != null && number < setting.min) return 'min';
  if (setting.max != null && number > setting.max) return 'max';
  return null;
}

export function valueErrors(setting: WorkspaceSetting, value: SettingValue): (ValueError | null)[] {
  const values = Array.isArray(value) ? value : [value];
  const originals = Array.isArray(setting.value) ? setting.value : [setting.value];
  return values.map((v, i) => elementError(setting, v, originals[i] ?? ''));
}

/** The draft with ``key`` set to ``value``; a value back to the original is no change any more. */
export function withValue(draft: Draft, setting: WorkspaceSetting, value: SettingValue): Draft {
  const next = { ...draft };
  if (sameValue(value, setting.value, setting.type === 'string')) delete next[setting.key];
  else next[setting.key] = value;
  return next;
}

export function withoutKey(draft: Draft, key: string): Draft {
  const next = { ...draft };
  delete next[key];
  return next;
}

export function useSettingsDraft(settings: WorkspaceSetting[]) {
  const [draft, setDraft] = useState<Draft>({});
  const byKey = useMemo(() => new Map(settings.map((s) => [s.key, s])), [settings]);
  const set = useCallback((setting: WorkspaceSetting, value: SettingValue) => setDraft((d) => withValue(d, setting, value)), []);
  const reset = useCallback((key: string) => setDraft((d) => withoutKey(d, key)), []);
  const resetAll = useCallback(() => setDraft({}), []);
  const invalid = useMemo(
    () =>
      Object.entries(draft)
        .filter(([key, value]) => {
          const setting = byKey.get(key);
          return !setting || valueErrors(setting, value).some((e) => e !== null);
        })
        .map(([key]) => key),
    [draft, byKey],
  );
  return { draft, set, reset, resetAll, invalid, count: Object.keys(draft).length };
}
