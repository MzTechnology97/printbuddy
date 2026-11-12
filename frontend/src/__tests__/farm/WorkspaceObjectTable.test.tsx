/**
 * Printfarm (fork): 3MF Workspace, batch E3c. OrcaSlicer's object table ("Object/Part Settings",
 * GUI_ObjectTable.cpp): its rows and sorting, the value a cell inherits and the edit a new one makes, the reset
 * icon, copying a cell to cells of the same column, the side panel by category. Made-up data.
 */
import { describe, it, expect } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import type { WorkspaceDocument, WorkspaceObject, WorkspaceSetting } from '../../farm/workspace/api';
import { useSettingsDraft } from '../../farm/workspace/draft';
import { ownToSave } from '../../farm/workspace/objects';
import { ALL, cellEdit, filamentCellEdit, platesOf, sameSetting, sideSettings, sortRows, tableRows } from '../../farm/workspace/objectGrid';
import { ObjectTableDialog } from '../../farm/workspace/ObjectTable';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';

const setting = (key: string, type: WorkspaceSetting['type'], value: WorkspaceSetting['value'], more: Partial<WorkspaceSetting> = {}): WorkspaceSetting => ({
  key,
  label: key,
  section: 'process',
  type,
  value,
  editable: true,
  readonly_reason: null,
  differs_from_system: null,
  overrides: [],
  ...more,
});
const BRIMS = ['auto_brim', 'brim_ears', 'painted', 'outer_only', 'inner_only', 'outer_and_inner', 'no_brim', 'leading_edge_only'];
const SETTINGS = [
  setting('layer_height', 'float', '0.2', { min: 0 }),
  setting('wall_loops', 'int', '2', { min: 0 }),
  setting('sparse_infill_density', 'float', '15%', { percent: 'always', min: 0, max: 100 }),
  setting('enable_support', 'bool', '0'),
  setting('brim_type', 'enum', 'auto_brim', { options: BRIMS.map((v) => ({ value: v, label: v })) }),
  setting('outer_wall_speed', 'float', ['200'], { nullable: true }),
  setting('top_shell_layers', 'int', '4'),
  setting('seam_position', 'enum', 'aligned', { options: [{ value: 'aligned', label: 'Aligned' }] }),
];
const LAYOUT: OrcaLayout = {
  tag: 'test',
  source: '',
  pages: { process: [], filament: [], printer: [] },
  options: Object.fromEntries(SETTINGS.map((s) => [s.key, { type: 'coInt', label: s.key, mode: 'simple' }])),
  it: {},
  object_keys: SETTINGS.map((s) => s.key),
  region_keys: ['wall_loops', 'sparse_infill_density', 'outer_wall_speed', 'top_shell_layers'],
  plate_keys: {},
  table_settings: {
    object: {
      Quality: [{ key: 'layer_height', priority: 1 }, { key: 'seam_position', priority: 2 }],
      Support: [{ key: 'brim_type', priority: 1 }, { key: 'enable_support', priority: 8 }],
    },
    part: {
      Strength: [
        { key: 'wall_loops', priority: 1 },
        { key: 'top_shell_layers', label: 'Top solid layers', priority: 1 },
        { key: 'sparse_infill_density', priority: 1 },
      ],
      Speed: [{ key: 'outer_wall_speed', priority: 1 }],
    },
  },
};
const CUBE: WorkspaceObject = {
  id: '4',
  name: 'Cube',
  extruder: '1',
  parts: [
    { id: '1', name: 'Body', type: 'normal_part', extruder: null },
    { id: '2', name: 'Box', type: 'modifier_part', extruder: '2' },
    { id: '3', name: 'Hole', type: 'negative_part', extruder: null },
  ],
  overrides: [
    { key: 'wall_loops', value: '3', part: null, part_id: null },
    { key: 'wall_loops', value: '5', part: 'Box', part_id: '2' },
  ],
};
const BALL: WorkspaceObject = { id: '7', name: 'Ball', extruder: '2', parts: [{ id: '1', name: 'Ball', type: 'normal_part' }], overrides: [] };
const ARCH: WorkspaceObject = { id: '9', name: 'Arch', extruder: '1', parts: [{ id: '1', name: 'Arch', type: 'normal_part' }], overrides: [] };
const DOC = {
  editable: true,
  plates: [
    { index: 1, name: null, object_ids: ['7'], instances: [], metadata: {} },
    { index: 2, name: null, object_ids: ['4'], instances: [], metadata: {} },
  ],
  objects: [CUBE, BALL, ARCH], // Arch is on no plate
  settings: SETTINGS,
} as unknown as WorkspaceDocument;

function Harness({ start = null }: { start?: { objectId: string; partId: string | null } | null }) {
  const { own, setOwn, setOwnMany, setOwnTargets, draft, undo } = useSettingsDraft(SETTINGS);
  return (
    <>
      <output data-testid="saved">{JSON.stringify(ownToSave(own))}</output>
      <button onClick={undo}>undo</button>
      <ObjectTableDialog
        layout={LAYOUT}
        doc={DOC}
        draft={draft}
        colours={['#ffffff', '#ff0000']}
        stats={new Map()}
        editing={{ edits: own, set: setOwn, setMany: setOwnMany, setTargets: setOwnTargets }}
        start={start}
        onSelect={() => {}}
        onClose={() => {}}
      />
    </>
  );
}
const saved = () =>
  Object.fromEntries(
    (JSON.parse(screen.getByTestId('saved').textContent || '[]') as { object_id: string; part_id: string | null; key: string; value: string | null }[]).map(
      (e) => [`${e.part_id ? `${e.object_id}/${e.part_id}` : e.object_id}:${e.key}`, e.value],
    ),
  );
const rowNames = () => screen.getAllByRole('row').slice(1).map((r) => r.querySelectorAll('td')[2].textContent);
const cell = (row: string, col: string) => screen.getByTestId(`cell-${row}-${col}`);

describe('the object table: rows and values', () => {
  const rows = tableRows(DOC);
  const byKey = new Map(SETTINGS.map((s) => [s.key, s]));

  it('lists each object and, when it has more than one, its parts; sorts by plate, name or filament', () => {
    expect(rows.map((r) => `${r.obj.name}/${r.partId ?? ''}`)).toEqual(['Cube/', 'Cube/1', 'Cube/2', 'Cube/3', 'Ball/', 'Arch/']);
    const plates = platesOf(DOC);
    const names = (col: string | null, desc = false) => sortRows(rows, plates, col, desc).map((r) => `${r.obj.name}/${r.partId ?? ''}`);
    expect(names(null)).toEqual(['Ball/', 'Cube/', 'Cube/1', 'Cube/2', 'Cube/3', 'Arch/']); // plate 1, 2, outside
    expect(names('name')).toEqual(['Arch/', 'Ball/', 'Cube/', 'Cube/1', 'Cube/2', 'Cube/3']);
    expect(names('name', true)).toEqual(['Cube/', 'Cube/1', 'Cube/2', 'Cube/3', 'Ball/', 'Arch/']);
    expect(names('extruder')).toEqual(['Cube/', 'Cube/1', 'Cube/2', 'Cube/3', 'Arch/', 'Ball/']);
  });

  it("drops a value equal to the one it inherits: an object's the project's, a part's its object's", () => {
    const [cube, body, box] = rows;
    const walls = byKey.get('wall_loops')!;
    expect(cellEdit(cube, walls, '2', {})).toEqual({ '4': { wall_loops: null } }); // the project's 2
    expect(cellEdit(cube, walls, '6', {})).toEqual({ '4': { wall_loops: '6' } });
    expect(cellEdit(body, walls, '3', {})).toEqual({ '4/1': { wall_loops: undefined } }); // its object's 3: none, as the file
    expect(cellEdit(box, walls, '3', {})).toEqual({ '4/2': { wall_loops: null } }); // the modifier's own 5 dropped
    expect(cellEdit(cube, byKey.get('sparse_infill_density')!, '15.0%', {})).toEqual({ '4': { sparse_infill_density: undefined } });
    expect([sameSetting('0.2', '0.20'), sameSetting('15%', '15.0%'), sameSetting('15', '15%')]).toEqual([true, true, false]);
  });

  it("changes an object's filament keeping its parts' own unless equal (update_volume_values_from_object)", () => {
    expect(filamentCellEdit(rows[0], 2)).toEqual({ '4': { extruder: '2' }, '4/2': { extruder: null } }); // Box's 2 dropped
    expect(filamentCellEdit(rows[0], 3)).toEqual({ '4': { extruder: '3' } }); // Box keeps its 2
    expect(filamentCellEdit(rows[3], 1)).toEqual({}); // a negative part has none
  });

  it("lists the side panel's settings of a category: the part's, an object's too, by priority", () => {
    expect(sideSettings(LAYOUT, 'Strength', true, {})).toEqual([
      { category: 'Strength', keys: [{ key: 'wall_loops' }, { key: 'top_shell_layers', label: 'Top solid layers' }, { key: 'sparse_infill_density' }] },
    ]);
    expect(sideSettings(LAYOUT, 'Support', true, {})[0].keys).toEqual([]); // a part has no support settings
    expect(sideSettings(LAYOUT, 'Support', false, {})[0].keys.map((k) => k.key)).toEqual(['brim_type', 'enable_support']);
    expect(sideSettings(LAYOUT, ALL, false, { wall_loops: '3', layer_height: '0.1' })).toEqual([
      { category: 'Quality', keys: [{ key: 'layer_height' }] },
      { category: 'Strength', keys: [{ key: 'wall_loops' }] },
    ]);
  });
});

describe('the object table dialog', () => {
  it('edits a cell, shows its reset icon and puts the inherited value back with it', async () => {
    render(<Harness />);
    expect(rowNames()).toEqual(['Ball', 'Cube', 'Body', 'Box', 'Hole', 'Arch']);
    fireEvent.change(within(cell('7/', 'wall_loops')).getByRole('textbox'), { target: { value: '4' } });
    expect(saved()).toEqual({ '7:wall_loops': '4' });
    await userEvent.click(within(cell('7/', 'wall_loops')).getByRole('button', { name: 'Reset parameter' }));
    expect(saved()).toEqual({});
    // Cube's own 3 differs from the project's 2: its reset icon shows; Body inherits Cube's 3 and has none
    expect(within(cell('4/', 'wall_loops')).getByRole('button', { name: 'Reset parameter' })).toBeInTheDocument();
    expect(within(cell('4/1', 'wall_loops')).queryByRole('button', { name: 'Reset parameter' })).toBeNull();
    expect(within(cell('4/1', 'wall_loops')).getByRole('textbox')).toHaveValue('3');
  });

  it("shows a part's object-only cells read only, and a negative part's settings and filament too", () => {
    render(<Harness />);
    expect(within(cell('4/1', 'layer_height')).queryByRole('textbox')).toBeNull();
    expect(within(cell('4/3', 'wall_loops')).queryByRole('textbox')).toBeNull();
    expect(within(cell('4/', 'layer_height')).getByRole('textbox')).toBeInTheDocument();
    expect(within(cell('4/3', 'extruder')).queryByRole('combobox')).toBeNull();
  });

  it('copies a cell to cells of the same column in one undo step, and says why not elsewhere', async () => {
    render(<Harness />);
    const src = cell('7/', 'sparse_infill_density');
    fireEvent.change(within(src).getByRole('textbox'), { target: { value: '40%' } });
    fireEvent.mouseDown(src);
    fireEvent.keyDown(src, { key: 'c', ctrlKey: true });
    fireEvent.mouseDown(cell('4/', 'sparse_infill_density'));
    fireEvent.mouseDown(cell('4/2', 'sparse_infill_density'), { shiftKey: true });
    fireEvent.keyDown(cell('4/2', 'sparse_infill_density'), { key: 'v', ctrlKey: true });
    // Cube gets 40% of its own; Body and Box then inherit it, so they keep none
    expect(saved()).toEqual({ '7:sparse_infill_density': '40%', '4:sparse_infill_density': '40%' });
    await userEvent.click(screen.getByRole('button', { name: 'undo' }));
    expect(saved()).toEqual({ '7:sparse_infill_density': '40%' });
    fireEvent.mouseDown(cell('4/', 'wall_loops'));
    fireEvent.keyDown(cell('4/', 'wall_loops'), { key: 'v', ctrlKey: true });
    expect(screen.getByRole('alert')).toHaveTextContent('One cell can only be copied to one or more cells in the same column.');
  });

  it("opens on the row asked, shows its category's settings with its own in orange, and sorts by a header", async () => {
    render(<Harness start={{ objectId: '4', partId: '2' }} />);
    const side = () => screen.getByRole('complementary', { name: 'Box' });
    expect(within(side()).getByRole('heading')).toHaveTextContent('Strength'); // the Name cell: its own settings
    expect(within(side()).getByTestId('side-wall_loops').firstChild).toHaveClass('text-[#F1754E]');
    fireEvent.mouseDown(cell('4/2', 'wall_loops'));
    expect(within(side()).getAllByTestId(/^side-/).map((e) => e.dataset.testid)).toEqual(['side-wall_loops', 'side-top_shell_layers', 'side-sparse_infill_density']);
    await userEvent.click(within(within(side()).getByTestId('side-wall_loops')).getByRole('button', { name: 'Reset parameter' }));
    expect(saved()).toEqual({ '4/2:wall_loops': null });
    await userEvent.click(screen.getByRole('button', { name: 'Name ↑↓' }));
    expect(rowNames()).toEqual(['Arch', 'Ball', 'Cube', 'Body', 'Box', 'Hole']);
    await userEvent.click(screen.getByRole('button', { name: 'Name ↑↓' }));
    expect(rowNames()).toEqual(['Cube', 'Body', 'Box', 'Hole', 'Ball', 'Arch']);
  });

  it('sets a filament from its list, leaving the parts that differ', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(within(cell('4/', 'extruder')).getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: '2' }));
    expect(saved()).toEqual({ '4:extruder': '2', '4/2:extruder': null });
  });
});
