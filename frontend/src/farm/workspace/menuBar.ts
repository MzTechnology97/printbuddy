// Fork (printfarm): the top bar's menus as OrcaSlicer v2.5.0 has them on Windows / Linux (MainFrame::init_menubar
// _as_editor, BBLTopbar): File, and the ≡ menu with Edit, View, Preferences, Speed Dial, Plugins and Help, in
// their order and with their accelerators. What works in the browser workspace now runs; the rest is greyed with
// the phase it comes in. Entries Printbuddy has elsewhere (recent files, bundles, setup wizard, network test,
// troubleshooting, updates) are left out, as in the parity notes.
import type { MenuItem } from './Viewport';
import type { ViewPreset } from './viewTools';

/** An OrcaSlicer string in the UI language (with its "&" mnemonic taken out). */
export type Tr = (text: string) => string;

/** "&About %s" -> "About %s": OrcaSlicer's menu mnemonics are not shown. */
export const stripMnemonic = (text: string) => text.replace(/&(?=\S)/g, '');

export interface MenuState {
  canUndo: boolean;
  canRedo: boolean;
  /** Something to save (a copy) and allowed to. */
  canSave: boolean;
  /** Unsaved changes to drop. */
  dirty: boolean;
  /** The current plate's slice, for its exports. */
  hasSlice: boolean;
  /** Prepare (the 3D view's menus act on it). */
  prepare: boolean;
  perspective: boolean;
  navigator: boolean;
  grid: boolean;
  labels: boolean;
  collision: boolean;
  overhangs: boolean;
  outline: boolean;
  /** Duplicate Current Plate: true, else why not (a model too large to rewrite, the plates' limit, not Prepare). */
  duplicatePlate: true | string;
}

export interface MenuActions {
  open: () => void;
  save: () => void;
  download3mf: () => void;
  exportSliced: () => void;
  exportGcode: () => void;
  quit: () => void;
  undo: () => void;
  redo: () => void;
  resetAll: () => void;
  selectAll: () => void;
  deselectAll: () => void;
  duplicatePlate: () => void;
  view: (preset: ViewPreset) => void;
  perspective: (on: boolean) => void;
  toggle: (option: 'navigator' | 'grid' | 'labels' | 'collision' | 'overhangs' | 'outline') => void;
  shortcuts: () => void;
}

/** Why an entry is greyed: the phase it comes in, or not in the browser workspace yet. */
export interface Hints {
  phase: (n: number) => string;
  later: string;
}

const on = (enabled: boolean, run: () => void) => (enabled ? run : undefined);

export function fileMenu(tr: Tr, hints: Hints, s: MenuState, a: MenuActions, extra: { download3mf: string }): MenuItem[] {
  const p2 = hints.phase(2), p4 = hints.phase(4);
  return [
    { label: tr('New Project'), shortcut: 'Ctrl+N', hint: p2 },
    { label: tr('Open Project'), shortcut: 'Ctrl+O', onClick: a.open },
    { label: tr('Save Project'), shortcut: 'Ctrl+S', onClick: on(s.canSave, a.save), separator: true },
    { label: tr('Save Project as'), shortcut: 'Ctrl+Shift+S', onClick: on(s.canSave, a.save) },
    { label: tr('Publish 3MF'), shortcut: 'Ctrl+Shift+E', hint: p4, separator: true },
    {
      label: tr('Import'),
      separator: true,
      items: [
        { label: tr('Import 3MF/STL/STEP/SVG/OBJ/AMF'), shortcut: 'Ctrl+I', hint: p2 },
        { label: tr('Import ZIP Archive'), hint: p2 },
      ],
    },
    {
      label: tr('Export'),
      items: [
        { label: tr('Export all objects as one STL'), hint: p4 },
        { label: tr('Export all objects as STLs'), hint: p4 },
        { label: tr('Export all objects as one DRC'), hint: p4 },
        { label: tr('Export all objects as DRCs'), hint: p4 },
        { label: tr('Export Generic 3MF'), hint: p4 },
        // Printbuddy's: the project file itself, as it is in the library
        { label: extra.download3mf, onClick: a.download3mf },
        { label: tr('Export plate sliced file'), shortcut: 'Ctrl+G', onClick: on(s.hasSlice, a.exportSliced), separator: true },
        { label: tr('Export all plate sliced file'), hint: hints.later },
        { label: tr('Export G-code'), onClick: on(s.hasSlice, a.exportGcode) },
        { label: tr('Export toolpaths as OBJ'), hint: p4 },
        { label: tr('Export Preset Bundle'), hint: p4, separator: true },
      ],
    },
    { label: tr('Quit'), shortcut: 'Ctrl+Q', onClick: a.quit, separator: true },
  ];
}

export function editMenu(tr: Tr, hints: Hints, s: MenuState, a: MenuActions, extra: { resetAll: string }): MenuItem[] {
  const p2 = hints.phase(2);
  return [
    { label: tr('Undo'), shortcut: 'Ctrl+Z', onClick: on(s.canUndo, a.undo) },
    { label: tr('Redo'), shortcut: 'Ctrl+Y', onClick: on(s.canRedo, a.redo) },
    { label: tr('Cut'), shortcut: 'Ctrl+X', hint: p2, separator: true },
    { label: tr('Copy'), shortcut: 'Ctrl+C', hint: p2 },
    { label: tr('Paste'), shortcut: 'Ctrl+V', hint: p2 },
    { label: tr('Delete Selected'), shortcut: 'Del', hint: p2 },
    { label: tr('Delete All'), shortcut: 'Ctrl+D', hint: p2 },
    { label: tr('Clone Selected'), shortcut: 'Ctrl+K', hint: p2, separator: true },
    {
      label: tr('Duplicate Current Plate'),
      onClick: s.duplicatePlate === true ? a.duplicatePlate : undefined,
      hint: s.duplicatePlate === true ? undefined : s.duplicatePlate,
      separator: true,
    },
    { label: tr('Select All'), shortcut: 'Ctrl+A', onClick: on(s.prepare, a.selectAll), separator: true },
    { label: tr('Deselect All'), shortcut: 'Esc', onClick: on(s.prepare, a.deselectAll) },
    // Printbuddy's: every unsaved change back to the project's value at once
    { label: extra.resetAll, onClick: on(s.dirty, a.resetAll), separator: true },
  ];
}

export function viewMenu(tr: Tr, trc: (context: string, text: string) => string, hints: Hints, s: MenuState, a: MenuActions): MenuItem[] {
  const p4 = hints.phase(4);
  const view = (preset: ViewPreset) => on(s.prepare, () => a.view(preset));
  const toggle = (option: Parameters<MenuActions['toggle']>[0]) => on(s.prepare, () => a.toggle(option));
  return [
    { label: tr('Default View'), shortcut: 'Ctrl+0', onClick: view('iso') },
    { label: trc('Camera View', 'Top'), shortcut: 'Ctrl+1', onClick: view('top') },
    { label: trc('Camera View', 'Bottom'), shortcut: 'Ctrl+2', onClick: view('bottom') },
    { label: trc('Camera View', 'Front'), shortcut: 'Ctrl+3', onClick: view('front') },
    { label: trc('Camera View', 'Rear'), shortcut: 'Ctrl+4', onClick: view('rear') },
    { label: trc('Camera View', 'Left'), shortcut: 'Ctrl+5', onClick: view('left') },
    { label: trc('Camera View', 'Right'), shortcut: 'Ctrl+6', onClick: view('right') },
    { label: tr('Use Perspective View'), checked: s.perspective, onClick: on(s.prepare, () => a.perspective(true)), separator: true },
    { label: tr('Use Orthogonal View'), checked: !s.perspective, onClick: on(s.prepare, () => a.perspective(false)) },
    { label: tr('Auto Perspective'), hint: hints.later },
    { label: tr('Show &G-code Window'), shortcut: 'C', hint: hints.later, separator: true },
    { label: tr('Show 3D Navigator'), checked: s.navigator, onClick: toggle('navigator') },
    { label: tr('Show Gridlines'), checked: s.grid, onClick: toggle('grid') },
    { label: tr('Show IDEX/IQEX Toolhead'), hint: p4 },
    { label: tr('Reset Window Layout'), hint: p4 },
    { label: tr('Show 3Dconnexion Settings'), shortcut: 'Ctrl+M', hint: p4, separator: true },
    { label: tr('Show &Labels'), shortcut: 'Ctrl+E', checked: s.labels, onClick: toggle('labels'), separator: true },
    { label: tr('Show all collision volumes'), shortcut: 'Ctrl+Shift+K', checked: s.collision, onClick: toggle('collision') },
    { label: tr('Show &Overhang'), checked: s.overhangs, onClick: toggle('overhangs') },
    { label: tr('Show Selected Outline (beta)'), checked: s.outline, onClick: toggle('outline') },
  ];
}

export function helpMenu(tr: Tr, hints: Hints, a: MenuActions): MenuItem[] {
  const p4 = hints.phase(4);
  return [
    { label: tr('Keyboard Shortcuts'), shortcut: '?', onClick: a.shortcuts },
    { label: tr('Show Configuration Folder'), hint: p4, separator: true },
    { label: tr('Migrate Filament Presets'), hint: p4 },
    { label: tr('Benchmark 3D Scene'), hint: p4, separator: true },
    { label: tr('Show Tip of the Day'), hint: p4, separator: true },
    { label: tr('&About %s').replace('%s', 'OrcaSlicer'), hint: p4 },
  ];
}

/** The ≡ menu (BBLTopbar's drop-down): Edit, View, Preferences, Speed Dial, Plugins, Help. */
export function mainMenu(
  tr: Tr,
  trc: (context: string, text: string) => string,
  hints: Hints,
  s: MenuState,
  a: MenuActions,
  extra: { resetAll: string },
): MenuItem[] {
  return [
    { label: trc('Menu', 'Edit'), items: editMenu(tr, hints, s, a, extra) },
    { label: tr('View'), items: viewMenu(tr, trc, hints, s, a) },
    { label: tr('Preferences'), shortcut: 'Ctrl+P', hint: hints.later },
    { label: tr('Open Speed Dial'), shortcut: 'Space', hint: hints.later, separator: true },
    { label: tr('Plugins'), hint: hints.phase(4), separator: true },
    { label: tr('Help'), items: helpMenu(tr, hints, a), separator: true },
  ];
}

/** What the top bar's keys can do now: each action is set only when it would run. */
export interface ShortcutActions {
  undo?: () => void;
  redo?: () => void;
  save?: () => void;
  slice?: () => void;
  print?: () => void;
  exportSliced?: () => void;
  /** A slice is running: Ctrl+R is held back so a reload doesn't lose it. */
  slicing: boolean;
}

/** The top bar's keys (Shortcuts.cpp: Ctrl+Z / Y / S / R / G, Ctrl+Shift+G). null: not ours, or nothing to do, so
 * the browser keeps the key (a reload, its own save...). 'slicing': Ctrl+R while slicing, held back on purpose. */
export function topBarShortcut(key: string, shift: boolean, a: ShortcutActions): (() => void) | 'slicing' | null {
  const k = key.toLowerCase();
  const run =
    k === 'z' && !shift ? a.undo
    : k === 'y' && !shift ? a.redo
    : k === 's' ? a.save
    : k === 'r' && !shift ? a.slice
    : k === 'g' ? (shift ? a.print : a.exportSliced)
    : undefined;
  if (run) return run;
  if (k === 'r' && !shift && a.slicing) return 'slicing';
  return null;
}

/** The Print button's actions (MainFrame::available_print_actions for a Bambu-network printer, without Send /
 * Send all / multi-device, which Printbuddy does with its own queue). */
export const PRINT_ACTIONS = ['printPlate', 'printAll', 'exportSliced', 'exportAllSliced', 'exportGcode'] as const;
export type PrintAction = (typeof PRINT_ACTIONS)[number];
export const PRINT_LABELS: Record<PrintAction, string> = {
  printPlate: 'Print plate',
  printAll: 'Print all',
  exportSliced: 'Export plate sliced file',
  exportAllSliced: 'Export all sliced file',
  exportGcode: 'Export G-code file',
};
/** Actions the browser workspace can't do yet (greyed in the drop-down): a phase, or 'later'. */
export const PRINT_LATER: Partial<Record<PrintAction, 'later' | 4>> = { printAll: 4, exportAllSliced: 'later' };
