// Fork (printfarm): the workspace's unsaved changes. Pure functions, so they're tested apart from the UI.
// The backend checks every value again (farm/workspace/settings.py); this only spares a round trip.
import { useCallback, useMemo, useReducer } from 'react';
import type { PresetRef, SettingValue, WorkspaceSetting } from './api';
import type { PlateOp, PlateRef } from './plates';
import type { RangeEdits } from './ranges';

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

/** ``edits`` with ``values`` of one target (undefined: the key's edit dropped, the file's own value again). */
export function withOwn(edits: OwnEdits, target: string, values: Record<string, string | null | undefined>): OwnEdits {
  const mine = { ...(edits[target] ?? {}) };
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined) delete mine[key];
    else mine[key] = value;
  }
  const next = { ...edits };
  if (Object.keys(mine).length) next[target] = mine;
  else delete next[target];
  return next;
}

/** The preset switches made from the sidebar combos (presetSwitch.ts): which presets, and the values they set. */
export interface Switched {
  refs: PresetRef[];
  values: Record<string, SettingValue>;
}
export const NO_SWITCH: Switched = { refs: [], values: {} };

/** The instances placed anew (placement.ts): ``objectId:instance`` -> its transform's 12 numbers. */
export type Placements = Record<string, number[]>;
const NO_PLACEMENTS: Placements = {};

/** Objects' and parts' own settings edited (objects.ts ``ownTarget``): target -> key -> its new value, or null when
 * it's no longer its own (Orca's reset arrow: the project's value applies). */
export type OwnEdits = Record<string, Record<string, string | null>>;
const NO_OWN: OwnEdits = {};
const NO_OPS: PlateOp[] = [];
/** The instances a move put on another plate: ``objectId:instance`` -> the plate it lies on (plates.ts PlateRef). */
export type OnPlate = Record<string, PlateRef>;
const NO_PLATE: OnPlate = {};
const NO_RANGES: RangeEdits = {};

/** One undo step: the draft, the preset switches it was made on, the instances placed anew, the objects' own
 * settings edited and the plates added, removed or moved (none: ``undefined``). */
export interface DraftStep {
  draft: Draft;
  switched?: Switched;
  placed?: Placements;
  own?: OwnEdits;
  plateOps?: PlateOp[];
  onPlate?: OnPlate;
  /** the objects' height ranges edited (ranges.ts) */
  ranges?: RangeEdits;
}
/** The draft with its undo / redo history (Orca's Undo / Redo, Ctrl+Z / Ctrl+Y, on the settings edits). */
export interface DraftHistory extends DraftStep {
  past: DraftStep[];
  future: DraftStep[];
  /** The setting the last edit changed and when: typing in one field is one step, not one per key. */
  last: { key: string; at: number } | null;
}
export type DraftAction =
  | { type: 'edit'; edit: (d: Draft) => Draft; key: string | null; at: number }
  /** a preset switch (or Undo all): the switches and the draft change together, as one step */
  | { type: 'switch'; switched: Switched; draft: Draft; placed?: Placements; own?: OwnEdits; plateOps?: PlateOp[]; onPlate?: OnPlate; ranges?: RangeEdits }
  /** a plate added, removed or moved: the plates, the instances they move and their own settings, one step */
  | { type: 'plates'; plateOps: PlateOp[]; placed: Placements; own: OwnEdits }
  /** a move (rotation, scale) of instances, with the plates it put them on: one step */
  | { type: 'place'; placed: Placements; onPlate?: OnPlate }
  /** an object's (part's) own settings: typing in one field is one step, as an edit */
  | { type: 'own'; edit: (o: OwnEdits) => OwnEdits; key: string | null; at: number }
  /** an object's height ranges: typing in one range's field is one step, as an edit */
  | { type: 'ranges'; edit: (r: RangeEdits) => RangeEdits; key: string | null; at: number }
  | { type: 'undo' }
  | { type: 'redo' };
/** Edits of the same setting closer than this make one undo step. */
export const COALESCE_MS = 1000;
const HISTORY = 100;

const stepOf = (h: DraftHistory): DraftStep => ({
  draft: h.draft,
  switched: h.switched,
  placed: h.placed,
  own: h.own,
  plateOps: h.plateOps,
  onPlate: h.onPlate,
  ranges: h.ranges,
});

export function draftHistory(h: DraftHistory, a: DraftAction): DraftHistory {
  if (a.type === 'undo') {
    if (!h.past.length) return h;
    return { ...h.past[h.past.length - 1], past: h.past.slice(0, -1), future: [stepOf(h), ...h.future], last: null };
  }
  if (a.type === 'redo') {
    if (!h.future.length) return h;
    return { ...h.future[0], past: [...h.past, stepOf(h)], future: h.future.slice(1), last: null };
  }
  if (a.type === 'switch' || a.type === 'place' || a.type === 'plates') {
    const next: DraftStep =
      a.type === 'place'
        ? { ...stepOf(h), placed: a.placed, onPlate: a.onPlate ?? h.onPlate }
        : a.type === 'plates'
          ? { ...stepOf(h), plateOps: a.plateOps, placed: a.placed, own: a.own }
          : {
              draft: a.draft,
              switched: a.switched,
              placed: a.placed ?? h.placed,
              own: a.own ?? h.own,
              plateOps: a.plateOps ?? h.plateOps,
              onPlate: a.onPlate ?? h.onPlate,
              ranges: a.ranges ?? h.ranges,
            };
    const flat = (x: DraftStep) =>
      JSON.stringify([x.draft, x.switched ?? NO_SWITCH, x.placed ?? NO_PLACEMENTS, x.own ?? NO_OWN, x.plateOps ?? NO_OPS, x.onPlate ?? NO_PLATE, x.ranges ?? NO_RANGES]);
    if (flat(next) === flat(h)) return h;
    return { ...next, past: [...h.past, stepOf(h)].slice(-HISTORY), future: [], last: null };
  }
  const step: DraftStep =
    a.type === 'own'
      ? { ...stepOf(h), own: a.edit(h.own ?? NO_OWN) }
      : a.type === 'ranges'
        ? { ...stepOf(h), ranges: a.edit(h.ranges ?? NO_RANGES) }
        : { ...stepOf(h), draft: a.edit(h.draft) };
  const flat = (x: DraftStep) => JSON.stringify([x.draft, x.own ?? NO_OWN, x.ranges ?? NO_RANGES]);
  if (flat(step) === flat(h)) return h;
  const key = a.key == null ? null : `${a.type}:${a.key}`;
  const same = key != null && h.last?.key === key && a.at - h.last.at < COALESCE_MS;
  const past = same ? h.past : [...h.past, stepOf(h)].slice(-HISTORY);
  return { ...step, past, future: [], last: key == null ? null : { key, at: a.at } };
}

/**
 * ``source``: the project's settings. ``view``: the settings as the switches made show them (presetTools.withSwitched);
 * ``settings`` is that view, so the values are checked against the switched presets.
 */
export function useSettingsDraft(
  source: WorkspaceSetting[],
  view?: (settings: WorkspaceSetting[], values: Switched['values']) => WorkspaceSetting[],
) {
  const [history, dispatch] = useReducer(draftHistory, { draft: {}, past: [], future: [], last: null });
  const draft = history.draft;
  const switched = history.switched ?? NO_SWITCH;
  const placed = history.placed ?? NO_PLACEMENTS;
  const own = history.own ?? NO_OWN;
  const plateOps = history.plateOps ?? NO_OPS;
  const onPlate = history.onPlate ?? NO_PLATE;
  const ranges = history.ranges ?? NO_RANGES;
  const settings = useMemo(() => (view ? view(source, switched.values) : source), [view, source, switched.values]);
  const byKey = useMemo(() => new Map(settings.map((s) => [s.key, s])), [settings]);
  const set = useCallback(
    (setting: WorkspaceSetting, value: SettingValue) =>
      dispatch({ type: 'edit', edit: (d) => withValue(d, setting, value), key: setting.key, at: Date.now() }),
    [],
  );
  const reset = useCallback((key: string) => dispatch({ type: 'edit', edit: (d) => withoutKey(d, key), key: null, at: Date.now() }), []);
  // Undo all: the edits, the preset switches, the placements and the objects' own settings, one step that Undo brings back
  const resetAll = useCallback(
    () =>
      dispatch({ type: 'switch', switched: NO_SWITCH, draft: {}, placed: NO_PLACEMENTS, own: NO_OWN, plateOps: NO_OPS, onPlate: NO_PLATE, ranges: NO_RANGES }),
    [],
  );
  /** Plates added, removed or moved (all of them so far), with the instances they move and their own settings. */
  const setPlates = useCallback(
    (ops: PlateOp[], placed: Placements, ownEdits: OwnEdits) => dispatch({ type: 'plates', plateOps: ops, placed, own: ownEdits }),
    [],
  );
  /** ``value``: the target's new own value of ``key``; null: no longer its own; undefined: back to what the file says. */
  const setOwn = useCallback(
    (target: string, key: string, value: string | null | undefined) =>
      dispatch({ type: 'own', edit: (o) => withOwn(o, target, { [key]: value }), key: value === undefined ? null : `${target}|${key}`, at: Date.now() }),
    [],
  );
  /** Several targets' keys in one step (Orca's filament change on an object drops its parts' own). */
  const setOwnTargets = useCallback(
    (values: Record<string, Record<string, string | null | undefined>>) =>
      dispatch({
        type: 'own',
        edit: (o) => Object.entries(values).reduce((acc, [target, keys]) => withOwn(acc, target, keys), o),
        key: null,
        at: Date.now(),
      }),
    [],
  );
  /** Several keys of one target in one step (Orca's colEditing: all its settings reset). */
  const setOwnMany = useCallback(
    (target: string, values: Record<string, string | null | undefined>) =>
      dispatch({ type: 'own', edit: (o) => withOwn(o, target, values), key: null, at: Date.now() }),
    [],
  );
  /** The height ranges edited (``key``: the field typed in, whose keystrokes make one step; null: a step of its own). */
  const setRanges = useCallback(
    (edit: (r: RangeEdits) => RangeEdits, key: string | null = null) => dispatch({ type: 'ranges', edit, key, at: Date.now() }),
    [],
  );
  const place = useCallback((next: Placements, plates?: OnPlate) => dispatch({ type: 'place', placed: next, onPlate: plates }), []);
  const switchTo = useCallback((next: Switched, nextDraft: Draft) => dispatch({ type: 'switch', switched: next, draft: nextDraft }), []);
  const undo = useCallback(() => dispatch({ type: 'undo' }), []);
  const redo = useCallback(() => dispatch({ type: 'redo' }), []);
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
  return {
    settings, switched, switchTo, placed, place, onPlate, own, setOwn, setOwnMany, setOwnTargets, plateOps, setPlates, ranges, setRanges, draft, set, reset, resetAll, invalid,
    count:
      Object.keys(draft).length + Object.values(own).reduce((n, keys) => n + Object.keys(keys).length, 0) + plateOps.length + Object.keys(ranges).length,
    undo, redo, canUndo: history.past.length > 0, canRedo: history.future.length > 0,
  };
}
