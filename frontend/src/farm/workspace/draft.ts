// Fork (printfarm): the workspace's unsaved changes. Pure functions, so they're tested apart from the UI.
// The backend checks every value again (farm/workspace/settings.py); this only spares a round trip.
import { useCallback, useMemo, useState } from 'react';
import type { SettingValue, WorkspaceSetting } from './api';

export const NIL = 'nil';
export type Draft = Record<string, SettingValue>;
export type ValueError = 'number' | 'integer' | 'min' | 'max' | 'percent' | 'option' | 'inherit';

export const ON = new Set(['1', 'true']);

export function formatValue(setting: WorkspaceSetting, value: SettingValue, inherit: string): string {
  const one = (v: string) => {
    if (v === NIL) return inherit;
    if (setting.type === 'bool') return ON.has(v) ? '✓' : '✗';
    if (setting.type === 'enum') return setting.options?.find((o) => o.value === v)?.label ?? v;
    return v;
  };
  return Array.isArray(value) ? value.map(one).join(' / ') : one(value);
}

export function sameValue(a: SettingValue, b: SettingValue): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => v.trim() === b[i].trim());
  }
  return a.trim() === b.trim();
}

/** Why one element of a new value would be refused (null: it fits), compared with its original element. */
export function elementError(setting: WorkspaceSetting, value: string, original: string): ValueError | null {
  const text = value.trim();
  if (text === original) return null;
  if (text === NIL) return 'inherit';
  if (setting.type === 'bool') return null; // a checkbox: always 0/1 (or true/false)
  if (setting.type === 'enum') return setting.options?.some((o) => o.value === text) ? null : 'option';
  if (text.endsWith('%') && !original.endsWith('%')) return 'percent';
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
  if (sameValue(value, setting.value)) delete next[setting.key];
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
