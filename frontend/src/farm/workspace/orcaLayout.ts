// Fork (printfarm): the settings layout of OrcaSlicer (AGPL-3.0, like Printbuddy): pages, option groups and
// order of the process, filament and printer settings, labels, tooltips, units, Simple / Advanced mode and the
// Italian strings, generated from its sources by tools/orca_settings/generate.py (see orca-assets/README.md).
// It drives the layout only: which settings can change and how a value is checked is Printbuddy's backend.

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
  enum?: { value: string; label: string }[];
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

export interface OrcaLayout {
  tag: string;
  source: string;
  pages: Record<Section, OrcaPage[]>;
  options: Record<string, OrcaOption>;
  /** English (as in OrcaSlicer) -> Italian (its it.po) */
  it: Record<string, string>;
}

// Loaded when the workspace opens: the file is large and no other page needs it.
const layoutModule = import.meta.glob<OrcaLayout>('./orca-assets/settings-layout.json', { import: 'default' });
export const loadOrcaLayout = (): Promise<OrcaLayout> => Object.values(layoutModule)[0]();

const icons = import.meta.glob<string>('./orca-assets/icons/*.svg', { eager: true, query: '?url', import: 'default' });

/** The URL of an OrcaSlicer icon copied into orca-assets/icons (undefined when not copied). */
export function orcaIcon(name: string | null | undefined): string | undefined {
  return name ? icons[`./orca-assets/icons/${name}.svg`] : undefined;
}

/** An OrcaSlicer string in the UI language: its Italian translation, else as OrcaSlicer writes it. */
export function orcaText(layout: OrcaLayout, text: string, language: string | undefined): string {
  return language?.startsWith('it') ? (layout.it[text] ?? text) : text;
}

/** Every key the layout places on a page (keys it doesn't place are "other values"). */
export function placedKeys(layout: OrcaLayout): Set<string> {
  return new Set(Object.values(layout.pages).flatMap((pages) => pages.flatMap((p) => p.groups.flatMap((g) => g.keys))));
}
