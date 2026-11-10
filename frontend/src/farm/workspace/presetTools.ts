// Fork (printfarm): the preset logic of OrcaSlicer's Save preset and Compare presets dialogs (SavePresetDialog.cpp,
// UnsavedChangesDialog.cpp, v2.5.0), pure so it's tested apart from the UI: the name check, a section's current
// values, the keys two presets differ in, and what a saved user preset holds.
import type { SettingValue, WorkspaceSetting } from './api';
import type { Draft } from './draft';
import type { Section } from './orcaLayout';

/** A message of the name check: Orca's msgid and the values for its placeholders. */
export interface NameMessage {
  text: string;
  values?: string[];
}
export interface NameCheck {
  status: 'valid' | 'warning' | 'invalid';
  messages: NameMessage[];
}

const UNUSABLE = '<>[]:/\\|?*"';
const SUFFIX_MODIFIED = ' (modified)';
const RESERVED = new Set(['CON', 'PRN', 'AUX', 'NUL', ...[...Array(10).keys()].flatMap((i) => [`COM${i}`, `LPT${i}`])]);

/** check_preset_name: ``existing`` are the user presets (they may be overwritten), ``system`` the names that
 * may not. */
export function checkPresetName(name: string, existing: string[], system: string[] = []): NameCheck {
  const invalid = (...messages: NameMessage[]): NameCheck => ({ status: 'invalid', messages });
  if ([...name].some((c) => UNUSABLE.includes(c)))
    return invalid({ text: 'Name is invalid;' }, { text: 'illegal characters:', values: [UNUSABLE] });
  if (name.includes(SUFFIX_MODIFIED)) return invalid({ text: 'Name is invalid;' }, { text: 'illegal suffix:', values: [SUFFIX_MODIFIED.trim()] });
  if (['Default Setting', 'Default Filament', 'Default Printer'].includes(name)) return invalid({ text: 'Name is unavailable.' });
  if (system.includes(name)) return invalid({ text: 'Overwriting a system profile is not allowed.' });
  if (existing.includes(name))
    return {
      status: 'warning',
      messages: [{ text: 'Preset "%1%" already exists.', values: [name] }, { text: 'Please note that saving will overwrite the current preset.' }],
    };
  if (!name) return invalid({ text: 'The name field is not allowed to be empty.' });
  // eslint-disable-next-line no-control-regex -- the check is for control characters
  if (/[\u0000-\u001f\u007f]/.test(name)) return invalid({ text: 'Name is invalid;' }, { text: 'control characters are not allowed.' });
  const base = name.split('.')[0].replace(/ +$/, '').toUpperCase();
  if (RESERVED.has(base)) return invalid({ text: 'The name is a reserved system name.' });
  const units = [...name].reduce((n, c) => n + (c.codePointAt(0)! > 0xffff ? 2 : 1), 0);
  if (new TextEncoder().encode(name).length > 230 || units > 150) return invalid({ text: 'The name is too long.' });
  if (name.startsWith(' ')) return invalid({ text: 'The name is not allowed to start with a space.' });
  if (name.endsWith(' ')) return invalid({ text: 'The name is not allowed to end with a space.' });
  return { status: 'valid', messages: [] };
}

/** The name the dialog suggests: a system preset's name with " - <suffix>", a user preset's own name. */
export function suggestedName(current: string | null, isSystem: boolean, suffix: string): string {
  if (!current) return 'Untitled';
  return isSystem ? `${current} - ${suffix}` : current;
}

const element = (v: SettingValue | undefined, i: number): SettingValue | undefined => (Array.isArray(v) ? (v[i] ?? v[0]) : v);

/** A section's values as the project has them now (unsaved changes on top); for a filament, its element of every
 * per-filament value, as a one-element list (OrcaSlicer's filament presets hold one). */
export function sectionValues(settings: WorkspaceSetting[], draft: Draft, section: Section, filament = 0): Record<string, SettingValue> {
  const out: Record<string, SettingValue> = {};
  for (const s of settings) {
    if (s.section !== section || s.readonly_reason === 'identity') continue;
    const v = s.key in draft ? draft[s.key] : s.value;
    out[s.key] = section === 'filament' ? [String(element(v, filament) ?? '')] : v;
  }
  return out;
}

const norm = (v: SettingValue | undefined) => (v === undefined ? '' : Array.isArray(v) ? (v.length === 1 ? v[0] : JSON.stringify(v)) : v);

/** The keys two presets' values differ in (a one-element list and its value are the same). A key one side
 * doesn't know (its parent couldn't be read) is not a difference: it's unknown. */
export function presetDiff(left: Record<string, SettingValue>, right: Record<string, SettingValue>, keys: Iterable<string>): string[] {
  return [...keys].filter((k) => k in left && k in right && norm(left[k]) !== norm(right[k]));
}

/** What a saved user preset holds (OrcaSlicer's user preset files): the preset it inherits and the keys that
 * differ from it, or, detached, every value of the section and no parent. */
export function userPresetSetting(
  section: Section,
  name: string,
  parent: string | null,
  values: Record<string, SettingValue>,
  changed: string[],
  detach: boolean,
): Record<string, unknown> {
  const keys = detach || !parent ? Object.keys(values) : changed.filter((k) => k in values);
  const body: Record<string, unknown> = Object.fromEntries(keys.map((k) => [k, values[k]]));
  return { ...body, name, from: 'User', type: section === 'process' ? 'process' : section, inherits: detach || !parent ? '' : parent };
}

/** The preset windows' titles (Tab::title), OrcaSlicer's msgids. */
export const SECTION_TITLES: Record<Section, string> = { printer: 'Printer', process: 'Process', filament: 'Filament' };
const ID_KEYS: Record<Section, string> = { process: 'print_settings_id', filament: 'filament_settings_id', printer: 'printer_settings_id' };

/** ``different_settings_to_system``: the keys each preset changes, [process, filaments..., printer]. */
function systemDiff(settings: WorkspaceSetting[]): string[][] {
  const v = settings.find((s) => s.key === 'different_settings_to_system')?.value;
  return Array.isArray(v) ? v.map((d) => d.split(';').filter(Boolean)) : [];
}
const slotOf = (slots: unknown[], section: Section, filament: number) =>
  section === 'process' ? 0 : section === 'printer' ? slots.length - 1 : 1 + filament;

/** The settings with a preset switch's values over them (the server's preview of settings.switch_preset);
 * ``differs_from_system`` follows the switched ``different_settings_to_system``. */
export function withSwitched(settings: WorkspaceSetting[], values: Record<string, SettingValue>): WorkspaceSetting[] {
  if (!Object.keys(values).length) return settings;
  const switched = settings.map((s) => (s.key in values ? { ...s, value: values[s.key] } : s));
  if (!('different_settings_to_system' in values)) return switched;
  const slots = systemDiff(switched);
  return switched.map((s) => {
    if (!s.section || !slots.length) return s;
    const mine = s.section === 'process' ? slots.slice(0, 1) : s.section === 'printer' ? slots.slice(-1) : slots.slice(1, -1);
    return { ...s, differs_from_system: mine.some((keys) => keys.includes(s.key)) };
  });
}

/** What switching a section (a filament's slot) leaves behind, OrcaSlicer's UnsavedChangesDialog: its unsaved
 * edits and the project's own changes from its preset (``different_settings_to_system``). */
export function unsavedKeys(settings: WorkspaceSetting[], draft: Draft, section: Section, filament = 0): string[] {
  const slots = systemDiff(settings);
  const own = new Set(slots[slotOf(slots, section, filament)] ?? []);
  const edited = (s: WorkspaceSetting) =>
    s.key in draft && (section !== 'filament' || !Array.isArray(s.value) || norm(element(draft[s.key], filament)) !== norm(element(s.value, filament)));
  return settings.filter((s) => s.section === section && s.editable && (own.has(s.key) || edited(s))).map((s) => s.key);
}

/** The unsaved edits after a switch (``before`` / ``after``: the settings without and with it). Transfer keeps the
 * values of ``keys`` over the new preset's; Discard drops them. A filament's switch touches its slot only: the
 * other slots keep their edits, and its slot of every other edit takes the new preset's value. */
export function draftAfterSwitch(
  before: WorkspaceSetting[],
  after: WorkspaceSetting[],
  draft: Draft,
  keys: string[],
  section: Section,
  filament: number,
  transfer: boolean,
): Draft {
  const was = new Map(before.map((s) => [s.key, s]));
  const now = new Map(after.map((s) => [s.key, s]));
  const out: Draft = { ...draft };
  const touched = new Set([...keys, ...Object.keys(draft).filter((k) => now.get(k)?.section === section)]);
  for (const key of touched) {
    const base = now.get(key)?.value;
    const old = key in draft ? draft[key] : was.get(key)?.value;
    if (base === undefined || old === undefined) continue;
    const keep = transfer && keys.includes(key);
    let value: SettingValue | undefined;
    if (section === 'filament' && Array.isArray(base)) {
      const rest = Array.isArray(draft[key]) ? (draft[key] as string[]) : base;
      value = base.map((v, i) => (i === filament ? (keep ? String(element(old, i) ?? v) : v) : (rest[i] ?? v)));
    } else value = keep ? old : undefined;
    if (value === undefined || norm(value) === norm(base)) delete out[key];
    else out[key] = value;
  }
  return out;
}

/** The project's preset name of a section (its element for a filament). */
export function projectPreset(settings: WorkspaceSetting[], section: Section, filament = 0): string | null {
  const v = settings.find((s) => s.key === ID_KEYS[section])?.value;
  const name = Array.isArray(v) ? (v[filament] ?? v[0]) : v;
  return name || null;
}

