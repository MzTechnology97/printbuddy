// Fork (printfarm): the settings layout of OrcaSlicer (AGPL-3.0, like Printbuddy), or of Bambu Studio for its
// projects: pages, option groups and order of the process, filament and printer settings, labels, tooltips,
// units, mode (simple / advanced / expert / develop) and the Italian strings, generated from their sources by
// tools/orca_settings/generate.py (see orca-assets/README.md). The backend checks values against the same data.

export type Section = 'process' | 'filament' | 'printer';

export interface OrcaOption {
  type: string;
  label: string;
  tooltip?: string;
  unit?: string;
  min?: number;
  max?: number;
  /** simple | advanced | expert | develop */
  mode: string;
  /** icon: OrcaSlicer's param_<value> icon of that value, when it has one (orca-assets/icons) */
  enum?: { value: string; label: string; icon?: string }[];
  vector?: boolean;
  nullable?: boolean;
  readonly?: boolean;
  multiline?: boolean;
  open?: boolean;
}

export interface OrcaGroup {
  title: string;
  icon: string | null;
  keys: string[];
}

export interface OrcaPage {
  title: string;
  icon: string;
  groups: OrcaGroup[];
}

/** A row of OrcaSlicer's "Keyboard Shortcuts" dialog: ``id`` is the Shortcut of a key table row (null: a fixed
 * row); a key part with "Keyboard Shortcut\u0004" before it is translated in that context (see keyText). */
export interface ShortcutRow {
  id: string | null;
  section: string;
  keys: string[];
  label: string;
}

export interface ShortcutPage {
  title: string;
  caption: string;
  context: string;
  rows: ShortcutRow[];
}

export interface OrcaLayout {
  tag: string;
  source: string;
  slicer?: string;
  pages: Record<Section, OrcaPage[]>;
  options: Record<string, OrcaOption>;
  /** Settings an object or part can have of its own (PrintObjectConfig + PrintRegionConfig) */
  object_keys: string[];
  /** A plate's own settings: key in model_settings.config -> the option it sets */
  plate_keys: Record<string, string>;
  /** OrcaSlicer's "Keyboard Shortcuts" dialog (KBShortcutsDialog.cpp, Shortcuts.cpp) */
  shortcuts?: ShortcutPage[];
  /** English (as in OrcaSlicer) -> Italian (its it.po); a contextual string under "context\u0004text" */
  it: Record<string, string>;
}

// Loaded when the workspace opens: the files are large and no other page needs them.
const layoutModules = import.meta.glob<OrcaLayout>('./orca-assets/settings-layout*.json', { import: 'default' });

/** The slicer's layout: Bambu Studio's for its projects (with OrcaSlicer's Italian for the 3D view's own words),
 * OrcaSlicer's otherwise. */
export async function loadOrcaLayout(slicer?: string | null): Promise<OrcaLayout> {
  const orca = await layoutModules['./orca-assets/settings-layout.json']();
  if (slicer !== 'bambu_studio') return orca;
  const bambu = await layoutModules['./orca-assets/settings-layout-bambu.json']();
  // the 3D view, its keys and their dialog are OrcaSlicer's for every project
  return { ...bambu, shortcuts: orca.shortcuts, it: { ...orca.it, ...bambu.it } };
}

/** Orca's modes, each showing the ones before it; develop only with the DEV switch. */
export const MODES = ['simple', 'advanced', 'expert'] as const;
export type Mode = (typeof MODES)[number];

export function shownInMode(optionMode: string | undefined, mode: Mode, develop: boolean): boolean {
  if (optionMode === 'develop') return develop;
  const rank = MODES.indexOf((optionMode ?? 'simple') as Mode);
  return rank < 0 || rank <= MODES.indexOf(mode);
}

const icons = import.meta.glob<string>('./orca-assets/icons/*.svg', { eager: true, query: '?url', import: 'default' });

/** The URL of an OrcaSlicer icon copied into orca-assets/icons (undefined when not copied). */
export function orcaIcon(name: string | null | undefined): string | undefined {
  return name ? icons[`./orca-assets/icons/${name}.svg`] : undefined;
}

/** An OrcaSlicer string in the UI language: its Italian translation, else as OrcaSlicer writes it. */
export function orcaText(layout: OrcaLayout, text: string, language: string | undefined): string {
  const out = language?.startsWith('it') ? (layout.it[text] ?? text) : text;
  return out.slice(out.indexOf('\u0004') + 1); // a contextual msgid ("Noun\u0004Scale") untranslated: the text alone
}

/** An OrcaSlicer format string in the UI language with its values put in (``%1%``, ``%1$d``, ``%d``), without
 * the line break it ends with. */
export function orcaFormat(layout: OrcaLayout, text: string, language: string | undefined, ...values: (string | number)[]): string {
  let next = 0;
  return orcaText(layout, text, language)
    .replace(/%(\d+)(?:\$[a-z]|%)|%[ds]/g, (m, n?: string) => String(values[n ? Number(n) - 1 : next++] ?? m))
    .replace(/\n$/, '');
}

/** One of OrcaSlicer's plural strings (``one`` for 1, else ``many``) with its number. */
export function orcaPlural(layout: OrcaLayout, one: string, many: string, n: number, language: string | undefined): string {
  return orcaFormat(layout, n === 1 ? one : many, language, n);
}

const KEY_CONTEXT = 'Keyboard Shortcut\u0004';

/** A key in the shortcuts dialog: a named key ("Esc", "Arrow Left"), a modifier ("Shift") or a mouse button. */
export function keyText(layout: OrcaLayout, part: string, language: string | undefined): string {
  const bare = part.startsWith(KEY_CONTEXT) ? part.slice(KEY_CONTEXT.length) : part;
  return language?.startsWith('it') ? (layout.it[KEY_CONTEXT + bare] ?? layout.it[bare] ?? bare) : bare;
}

/** Every key the layout places on a page (keys it doesn't place are "other values"). */
export function placedKeys(layout: OrcaLayout): Set<string> {
  return new Set(Object.values(layout.pages).flatMap((pages) => pages.flatMap((p) => p.groups.flatMap((g) => g.keys))));
}
