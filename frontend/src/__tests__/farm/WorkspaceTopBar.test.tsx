/**
 * Printfarm (fork): the 3MF Workspace's top bar (batch D1). Undo / redo of the settings edits, the File and ≡
 * menus in OrcaSlicer's order (with real submenus), the Slice / Print buttons with their ▾, and the slice's checks
 * against the bed.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import { render } from '../utils';
import { COALESCE_MS, type DraftHistory, draftHistory } from '../../farm/workspace/draft';
import { type MenuActions, type MenuState, fileMenu, mainMenu, stripMnemonic, topBarShortcut } from '../../farm/workspace/menuBar';
import { TopBar, type TopBarProps } from '../../farm/workspace/TopBar';
import { ContextMenu } from '../../farm/workspace/Viewport';
import { bedRectangle, sliceWarnings, warningText } from '../../farm/workspace/gcode/checks';
import { parseGcode } from '../../farm/workspace/gcode/parse';

describe('undo / redo of the settings edits', () => {
  const start: DraftHistory = { draft: {}, past: [], future: [], last: null };
  const edit = (h: DraftHistory, key: string, value: string, at: number) =>
    draftHistory(h, { type: 'edit', edit: (d) => ({ ...d, [key]: value }), key, at });

  it('typing in one field is one step; another field, or a pause, is another', () => {
    let h = edit(start, 'layer_height', '0.1', 0);
    h = edit(h, 'layer_height', '0.12', 200); // same field, soon after: the same step
    expect(h.past).toHaveLength(1);
    h = edit(h, 'layer_height', '0.16', 200 + COALESCE_MS + 1); // after a pause: a new step
    h = edit(h, 'wall_loops', '3', 200 + COALESCE_MS + 2);
    expect(h.past).toHaveLength(3);
    h = draftHistory(h, { type: 'undo' });
    expect(h.draft).toEqual({ layer_height: '0.16' });
    h = draftHistory(draftHistory(h, { type: 'undo' }), { type: 'undo' });
    expect(h.draft).toEqual({});
    expect(draftHistory(h, { type: 'undo' })).toBe(h); // nothing more to undo
    h = draftHistory(h, { type: 'redo' });
    expect(h.draft).toEqual({ layer_height: '0.12' });
  });

  it('a new edit drops what could be redone, and an edit that changes nothing is no step', () => {
    let h = edit(start, 'a', '1', 0);
    h = draftHistory(h, { type: 'undo' });
    expect(h.future).toHaveLength(1);
    h = edit(h, 'b', '2', 5000);
    expect(h.future).toHaveLength(0);
    const same = draftHistory(h, { type: 'edit', edit: (d) => ({ ...d }), key: null, at: 9000 });
    expect(same).toBe(h);
  });
});

const state: MenuState = {
  canUndo: true, canRedo: false, canSave: false, dirty: true, hasSlice: false, prepare: true, perspective: true,
  navigator: true, grid: true, labels: false, collision: false, overhangs: false, outline: true,
};
const actions = (): MenuActions => ({
  open: vi.fn(), save: vi.fn(), download3mf: vi.fn(), exportSliced: vi.fn(), exportGcode: vi.fn(), quit: vi.fn(),
  undo: vi.fn(), redo: vi.fn(), resetAll: vi.fn(), selectAll: vi.fn(), deselectAll: vi.fn(), view: vi.fn(),
  perspective: vi.fn(), toggle: vi.fn(), shortcuts: vi.fn(),
});
const tr = (s: string) => stripMnemonic(s);
const trc = (_c: string, s: string) => s;
const hints = { phase: (n: number) => `phase ${n}`, later: 'later' };

describe('menus (MainFrame.cpp)', () => {
  it("File: Orca's order; what works runs, the rest is greyed with its phase", () => {
    const a = actions();
    const items = fileMenu(tr, hints, state, a, { download3mf: 'Download the 3MF' });
    expect(items.map((i) => i.label)).toEqual(['New Project', 'Open Project', 'Save Project', 'Save Project as', 'Publish 3MF', 'Import', 'Export', 'Quit']);
    expect(items[0].onClick).toBeUndefined();
    expect(items[0].hint).toBe('phase 2');
    expect(items[2].onClick).toBeUndefined(); // nothing to save
    const exp = items.find((i) => i.label === 'Export')!.items!;
    const sliced = exp.find((i) => i.label === 'Export plate sliced file')!;
    expect([sliced.shortcut, sliced.onClick]).toEqual(['Ctrl+G', undefined]);
    expect(exp.find((i) => i.label === 'Export all plate sliced file')).toMatchObject({ hint: 'later' });
    const withSlice = fileMenu(tr, hints, { ...state, hasSlice: true, canSave: true }, a, { download3mf: 'x' });
    withSlice.find((i) => i.label === 'Export')!.items!.find((i) => i.label === 'Export G-code')!.onClick!();
    expect(a.exportGcode).toHaveBeenCalled();
    expect(withSlice[3].onClick).toBe(a.save);
  });

  it('≡: Edit, View, Preferences, Speed Dial, Plugins and Help; View has the camera views and the switches', () => {
    const a = actions();
    const items = mainMenu(tr, trc, hints, state, a, { resetAll: 'Reset all' });
    expect(items.map((i) => i.label)).toEqual(['Edit', 'View', 'Preferences', 'Open Speed Dial', 'Plugins', 'Help']);
    const edit = items[0].items!;
    expect(edit[0]).toMatchObject({ label: 'Undo', shortcut: 'Ctrl+Z' });
    expect(edit[1].onClick).toBeUndefined(); // nothing to redo
    const view = items[1].items!;
    expect(view.slice(0, 7).map((i) => i.shortcut)).toEqual(['Ctrl+0', 'Ctrl+1', 'Ctrl+2', 'Ctrl+3', 'Ctrl+4', 'Ctrl+5', 'Ctrl+6']);
    view.find((i) => i.label === 'Top')!.onClick!();
    expect(a.view).toHaveBeenCalledWith('top');
    expect(view.find((i) => i.label === 'Show Labels')).toMatchObject({ checked: false, shortcut: 'Ctrl+E' });
    expect(view.find((i) => i.label === 'Use Orthogonal View')!.checked).toBe(false);
    expect(items[5].items![0]).toMatchObject({ label: 'Keyboard Shortcuts', shortcut: '?' });
  });

  it('a submenu opens with the right arrow, runs its item and closes the whole menu', () => {
    const run = vi.fn();
    const close = vi.fn();
    render(<ContextMenu items={[{ label: 'View', items: [{ label: 'Top', onClick: run }] }, { label: 'Help', items: [] }]} at={{ x: 0, y: 0 }} onClose={close} />);
    const view = screen.getByRole('menuitem', { name: 'View' });
    expect(view).toHaveAttribute('aria-haspopup', 'true');
    fireEvent.keyDown(view, { key: 'ArrowRight' });
    expect(view).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Top' }));
    expect(run).toHaveBeenCalled();
    expect(close).toHaveBeenCalled();
  });

  it('Esc or a chosen item gives the focus back to what opened the menu', () => {
    const { unmount } = render(<button type="button">File</button>);
    const trigger = screen.getByRole('button', { name: 'File' });
    trigger.focus();
    const close = vi.fn();
    const menu = render(<ContextMenu items={[{ label: 'Save', onClick: vi.fn() }]} at={{ x: 0, y: 0 }} onClose={close} />);
    screen.getByRole('menuitem', { name: 'Save' }).focus();
    fireEvent.keyDown(window, { key: 'Escape' }); // jsdom's selector engine can't run the menu's own key handler
    expect(close).toHaveBeenCalled();
    expect(trigger).toHaveFocus();
    menu.unmount();
    trigger.focus();
    render(<ContextMenu items={[{ label: 'Save', onClick: vi.fn() }]} at={{ x: 0, y: 0 }} onClose={vi.fn()} />);
    screen.getByRole('menuitem', { name: 'Save' }).focus();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Save' }));
    expect(trigger).toHaveFocus();
    unmount();
  });
});

describe('the top bar keys (Shortcuts.cpp)', () => {
  it('a key runs its action only when there is one; otherwise the browser keeps it', () => {
    const save = vi.fn();
    expect(topBarShortcut('s', false, { save, slicing: false })).toBe(save);
    expect(topBarShortcut('z', false, { slicing: false })).toBeNull(); // nothing to undo: the browser's undo
    expect(topBarShortcut('r', false, { slicing: false })).toBeNull(); // nothing to slice: the browser reloads
    expect(topBarShortcut('p', false, { save, slicing: false })).toBeNull(); // not ours
  });

  it('Ctrl+R while slicing is held back on purpose (no reload mid-slice)', () => {
    expect(topBarShortcut('r', false, { slicing: true })).toBe('slicing');
    expect(topBarShortcut('R', true, { slicing: true })).toBeNull(); // Ctrl+Shift+R stays the browser's
  });

  it('Ctrl+G exports the sliced file, Ctrl+Shift+G prints', () => {
    const print = vi.fn(), exportSliced = vi.fn();
    expect(topBarShortcut('g', false, { print, exportSliced, slicing: false })).toBe(exportSliced);
    expect(topBarShortcut('G', true, { print, exportSliced, slicing: false })).toBe(print);
  });
});

describe('TopBar', () => {
  const props = (over: Partial<TopBarProps> = {}): TopBarProps => ({
    title: 'Bracket.3mf', subtitle: 'OrcaSlicer', dirty: true, file: [], main: [],
    labels: { close: 'Close', file: 'File', menu: 'Menu', save: 'Save Project', undo: 'Undo', redo: 'Redo', tabs: 'Workspace' },
    onClose: vi.fn(), modes: [{ key: 'prepare', label: 'Prepare' }, { key: 'preview', label: 'Preview' }], mode: 'prepare', onMode: vi.fn(),
    slice: { label: 'Slice plate', run: vi.fn(), busy: false, options: [], more: 'Slice options' },
    print: { label: 'Print plate', options: [], more: 'Print options' },
    ...over,
  });

  it('marks unsaved changes with "*" and greys Undo / Redo / Print when they have nothing to do', () => {
    render(<TopBar {...props()} />);
    expect(screen.getByRole('heading')).toHaveTextContent('*Bracket.3mf');
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Print plate' })).toBeDisabled();
  });

  it('the ▾ picks what the Slice button does; the button runs it', () => {
    const all = vi.fn();
    const p = props({ slice: { label: 'Slice plate', run: vi.fn(), busy: false, more: 'Slice options', options: [{ label: 'Slice all', onClick: all }, { label: 'Slice plate', checked: true, onClick: vi.fn() }] } });
    render(<TopBar {...p} />);
    fireEvent.click(screen.getByRole('button', { name: 'Slice plate' }));
    expect(p.slice.run).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Slice options' }));
    const menu = screen.getByRole('menu');
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Slice all' }));
    expect(all).toHaveBeenCalled();
  });

  it('a menu trigger closes its own open menu', () => {
    render(<TopBar {...props({ file: [{ label: 'Save Project', onClick: vi.fn() }] })} />);
    const file = screen.getByRole('button', { name: 'File' });
    fireEvent.click(file);
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.mouseDown(file); // the menu's close-on-outside-press must not see this press...
    fireEvent.click(file); // ...or the click would open it again
    expect(screen.queryByRole('menu')).toBeNull();
    expect(file).toHaveAttribute('aria-expanded', 'false');
  });
});

describe("the slice's checks against the bed (GLCanvas3D)", () => {
  const plate = (lines: string[], height = 250) =>
    parseGcode(
      ['; CONFIG_BLOCK_START', '; printable_area = 0x0,256x0,256x256,0x256', `; printable_height = ${height}`, '; CONFIG_BLOCK_END', 'M83', ...lines].join('\n'),
    );

  it('finds nothing on a plate within the bed', () => {
    expect(sliceWarnings(plate(['; CHANGE_LAYER', '; Z_HEIGHT: 0.2', '; FEATURE: Outer wall', 'G1 X10 Y10 Z0.2 F600', 'G1 X20 Y10 E1']))).toEqual([]);
  });

  it('a path beyond the edges is an error; the start G-code (custom) out there is not', () => {
    const out = plate(['; FEATURE: Custom', 'G1 X-40 Y5 Z0.2', 'G1 X-30 Y5 E2', '; CHANGE_LAYER', '; Z_HEIGHT: 0.2', '; FEATURE: Outer wall', 'G1 X250 Y10', 'G1 X260 Y10 E1']);
    expect(sliceWarnings(out).map((w) => [w.key, w.error])).toEqual([['outside', true]]);
    const purgeOnly = plate(['; FEATURE: Custom', 'G1 X-40 Y5 Z0.2', 'G1 X-30 Y5 E2', '; CHANGE_LAYER', '; Z_HEIGHT: 0.2', '; FEATURE: Outer wall', 'G1 X10 Y10', 'G1 X20 Y10 E1']);
    expect(sliceWarnings(purgeOnly)).toEqual([]);
  });

  it('too tall: the height error alone (Orca then leaves the edge check out)', () => {
    const tall = plate(['; CHANGE_LAYER', '; Z_HEIGHT: 5', '; FEATURE: Outer wall', 'G1 X300 Y10 Z5', 'G1 X310 Y10 E1'], 4);
    expect(sliceWarnings(tall).map((w) => w.key)).toEqual(['height']);
  });

  it('a prime tower beyond the plate is a warning', () => {
    const tower = plate(['; CHANGE_LAYER', '; Z_HEIGHT: 0.2', '; FEATURE: Prime tower', 'G1 X250 Y250 Z0.2', 'G1 X258 Y250 E1']);
    expect(sliceWarnings(tower).map((w) => [w.key, w.error])).toEqual([['outside', true], ['tower', false]]);
  });

  it('a round or custom bed is not checked at the edges (no false error from the box corners)', () => {
    const round = parseGcode(
      ['; CONFIG_BLOCK_START', '; printable_area = 0x-100,70x-70,100x0,70x70,0x100,-70x70,-100x0,-70x-70', '; printable_height = 250', '; CONFIG_BLOCK_END', 'M83',
        '; CHANGE_LAYER', '; Z_HEIGHT: 0.2', '; FEATURE: Outer wall', 'G1 X-90 Y0 Z0.2', 'G1 X90 Y0 E5', 'G1 X0 Y90 E5'].join('\n'),
    );
    expect(sliceWarnings(round)).toEqual([]);
    expect(bedRectangle([[256, 256], [0, 0], [256, 0], [0, 256]])).toEqual([0, 0, 256, 256]);
    expect(bedRectangle([[0, 0], [256, 0], [128, 256]])).toBeNull();
  });

  it('extrusion before the first feature tag (role undefined) is left out, as Orca', () => {
    expect(sliceWarnings(plate(['G1 X-40 Y5 Z0.2', 'G1 X-30 Y5 E2', '; CHANGE_LAYER', '; Z_HEIGHT: 0.2', '; FEATURE: Outer wall', 'G1 X10 Y10', 'G1 X20 Y10 E1']))).toEqual([]);
  });

  it("the nozzle's extruder_offset is added, as GCodeProcessor (X1 / P1: 0x2)", () => {
    const at = (offset: string) =>
      parseGcode(
        ['; CONFIG_BLOCK_START', '; printable_area = 0x0,256x0,256x256,0x256', '; printable_height = 250', `; extruder_offset = ${offset}`, '; CONFIG_BLOCK_END', 'M83',
          '; CHANGE_LAYER', '; Z_HEIGHT: 0.2', '; FEATURE: Outer wall', 'G1 X10 Y255 Z0.2', 'G1 X20 Y255 E1'].join('\n'),
      );
    expect(sliceWarnings(at('0x0'))).toEqual([]);
    expect(sliceWarnings(at('0x2')).map((w) => w.key)).toEqual(['outside']);
    expect(at('0x2').stats.pathBox?.[4]).toBeCloseTo(257);
  });

  // the start G-code of the public v2.5.0 profiles, as their CLI slices print it: all tagged custom, so left out
  it('real start G-code patterns: A1 wipe off the bed, X1 / P1S / H2D / X2D purge lines, K2 (Klipper) purge on the edge', () => {
    const a1 = plate(['; FEATURE: Custom', 'G1 X-28.5 F30000', 'G1 X-48.2 F3000', 'G1 X-28.5 E0.5 F3000 ;wipe and shake', 'G1 X-48.2 E0.5 F3000']);
    const x1 = plate(['; FEATURE: Custom', 'G1 X10.1 Y200.0 Z0.28 F1500.0 E15 ;Draw the first line', 'G1 X10.4 Y20 Z0.28 F1500.0 E30 ;Draw the second line']);
    const k2 = plate([';TYPE:Custom', 'G1 X0 Y0 Z0.3 F6000', 'G1 X150 Y0 E15 F6000', 'G1 X-2 Y0 E15 F6000']);
    for (const p of [a1, x1, k2]) expect(sliceWarnings(p)).toEqual([]);
  });
});

describe("each nozzle's printable area and height (H2D, X2D: GCodeProcessor::check_multi_extruder_gcode_valid)", () => {
  // the public v2.5.0 H2D machine profile: the left nozzle can't reach x > 325, the right one x < 25
  const H2D = [
    '; printable_area = 0x0,350x0,350x320,0x320',
    '; printable_height = 325',
    '; extruder_printable_area = 0x0,325x0,325x320,0x320#25x0,350x0,350x320,25x320',
    '; extruder_printable_height = 320,325',
    '; extruder_offset = 0x0,0x0',
    '; nozzle_diameter = 0.4,0.4',
    '; printer_model = Bambu Lab H2D',
  ];
  /** ``objects``: per object, its filament (0-based, as T<n>) and the x span its extrusions end in; ``map``: filament -> nozzle (1-based) */
  const slice = (objects: { filament: number; x: [number, number]; z?: number }[], { map = '2,1', config = H2D } = {}) =>
    parseGcode(
      ['; CONFIG_BLOCK_START', ...config, `; filament_map = ${map}`, '; CONFIG_BLOCK_END', 'M83', '; FEATURE: Custom', 'G1 X-10 Y5 Z0.2', 'G1 X0 Y5 E2',
        ...objects.flatMap((o, i) => [
          `; CHANGE_LAYER`, `; Z_HEIGHT: ${o.z ?? 0.2}`, `; start printing object, unique label id: ${8 + 4 * i}`, `T${o.filament}`,
          '; FEATURE: Outer wall', `G1 X${o.x[0] - 1} Y100 Z${o.z ?? 0.2}`, `G1 X${o.x[0]} Y100 E0.1`, `G1 X${o.x[1]} Y100 E1`, `; stop printing object, unique label id: ${8 + 4 * i}`,
        ])].join('\n'),
    );
  const text = (w: ReturnType<typeof sliceWarnings>[number]) => warningText(w, (t) => t);

  it('finds nothing when each filament stays in its nozzle\'s area (the custom start G-code off the bed is left out)', () => {
    expect(sliceWarnings(slice([{ filament: 1, x: [10, 300] }, { filament: 0, x: [30, 340] }]))).toEqual([]);
  });

  it("a filament past its nozzle's reach is an error naming the filament and the nozzle", () => {
    // filament_map 2,1: filament 2 (T1) prints with the left nozzle, which stops at x = 325
    const out = sliceWarnings(slice([{ filament: 1, x: [300, 330] }]));
    expect(out.map((w) => [w.key, w.error])).toEqual([['nozzleArea', true]]);
    expect(text(out[0])).toBe('Filament 2 is placed in the left nozzle, but the generated G-code path exceeds the printable range of the left nozzle.');
    // touching the edge counts, as Orca's box grown by SCALED_EPSILON
    expect(sliceWarnings(slice([{ filament: 1, x: [300, 325] }])).map((w) => w.key)).toEqual(['nozzleArea']);
    expect(sliceWarnings(slice([{ filament: 1, x: [300, 324.9] }]))).toEqual([]);
  });

  it('one line per nozzle, left first; a filament two objects print is listed twice, as Orca lists it', () => {
    const out = sliceWarnings(slice([{ filament: 0, x: [5, 20] }, { filament: 1, x: [320, 330] }, { filament: 1, x: [330, 340] }]));
    expect(text(out[0]).split('\n')).toEqual([
      'Filaments 2, 2 are placed in the left nozzle, but the generated G-code path exceeds the printable range of the left nozzle.',
      'Filament 1 is placed in the right nozzle, but the generated G-code path exceeds the printable range of the right nozzle.',
    ]);
  });

  it("the nozzle's own height (320 for the left one), with Orca's wiki line", () => {
    const out = sliceWarnings(slice([{ filament: 1, x: [100, 120], z: 322 }]));
    expect(out.map((w) => w.key)).toEqual(['nozzleHeight']);
    expect(text(out[0]).split('\n')).toEqual([
      'Filament 2 is placed in the left nozzle, but the generated G-code path exceeds the printable height of the left nozzle.',
      'Open wiki for more information.',
    ]);
    expect(sliceWarnings(slice([{ filament: 0, x: [100, 120], z: 322 }]))).toEqual([]); // the right nozzle reaches 325
  });

  it('in Italian, with the nozzle translated; "Tool N" for a printer that is not a Bambu Lab one', () => {
    const it = (t: string) => (({
      'Filament %1% is placed in the %2%, but the generated G-code path exceeds the printable range of the %2%.':
        'Il filamento %1% è posizionato nel %2%, ma il percorso G-code generato supera l\'area stampabile del %2%.',
      'left nozzle': 'ugello sinistro',
      'Tool %d': 'Testina %d',
    }) as Record<string, string>)[t] ?? t;
    const h2d = sliceWarnings(slice([{ filament: 1, x: [300, 330] }]));
    expect(warningText(h2d[0], it)).toBe('Il filamento 2 è posizionato nel ugello sinistro, ma il percorso G-code generato supera l\'area stampabile del ugello sinistro.');
    const other = sliceWarnings(slice([{ filament: 1, x: [300, 330] }], { config: H2D.map((l) => l.replace('Bambu Lab H2D', 'Other IDEX')) }));
    expect(warningText(other[0], it)).toContain('nel Testina 1,');
  });

  it('not on a single-nozzle printer, nor for a nozzle area that is not a rectangle (no false error)', () => {
    const single = H2D.map((l) => l.replace('0.4,0.4', '0.4'));
    expect(sliceWarnings(slice([{ filament: 1, x: [300, 330] }], { config: single }))).toEqual([]);
    // fdm_bbl_3dp_002_common's right-nozzle area, a quadrilateral that is not a rectangle
    const odd = H2D.map((l) => l.replace('25x0,350x0,350x320,25x320', '25x0,350x0,350x320,0x320'));
    expect(sliceWarnings(slice([{ filament: 0, x: [5, 20] }], { config: odd }))).toEqual([]);
  });

  it("keeps each object's and filament's paths apart, custom G-code left out", () => {
    const tp = slice([{ filament: 1, x: [10, 20] }, { filament: 0, x: [30, 40] }]);
    expect(tp.stats.nozzlePaths.map((p) => [p.object, p.filament, p.box[0], p.box[2]])).toEqual([[8, 1, 10, 20], [12, 0, 30, 40]]);
  });
});
