/**
 * Printfarm (fork): 3MF Workspace, batch E6a. OrcaSlicer's Height Range Modifier: an object's height ranges under
 * "Layers" in the objects list, their editor (Z min / Z max, add after, remove, "Invalid numeric.") and each
 * range's own settings (TabPrintLayer), each change a step of the undo history; what the copy is asked to save.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import type { LayerRange, WorkspaceDocument, WorkspaceObject, WorkspaceSetting } from '../../farm/workspace/api';
import { useSettingsDraft } from '../../farm/workspace/draft';
import { objectMenu } from '../../farm/workspace/menus';
import { ObjectsPanel } from '../../farm/workspace/ObjectsPanel';
import type { Selection } from '../../farm/workspace/objects';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';
import {
  addAfter,
  defaultOptions,
  editMax,
  editMin,
  firstRanges,
  parseZ,
  rangeLabel,
  rangesToSave,
  withRanges,
} from '../../farm/workspace/ranges';

const base = {
  description: null,
  unit: null,
  min: null,
  max: null,
  step: null,
  options: null,
  editable: true,
  readonly_reason: null,
  differs_from_system: false,
  overrides: [],
} as const;
const SETTINGS: WorkspaceSetting[] = [
  { ...base, key: 'layer_height', label: 'Layer height', section: 'process', type: 'float', value: '0.2' },
  { ...base, key: 'wall_loops', label: 'Wall loops', section: 'process', type: 'int', min: 0, value: '3' },
  { ...base, key: 'enable_support', label: 'Enable support', section: 'process', type: 'bool', value: '0' },
];
const LAYOUT: OrcaLayout = {
  tag: 'test',
  source: '',
  pages: {
    process: [
      { title: 'Quality', icon: '', groups: [{ title: 'Layer height', icon: null, keys: ['layer_height'] }] },
      { title: 'Strength', icon: '', groups: [{ title: 'Walls', icon: null, keys: ['wall_loops'] }] },
      { title: 'Support', icon: '', groups: [{ title: 'Support', icon: null, keys: ['enable_support'] }] },
    ],
    filament: [],
    printer: [],
  },
  options: {
    layer_height: { type: 'coFloat', label: 'Layer height', mode: 'simple' },
    wall_loops: { type: 'coInt', label: 'Wall loops', mode: 'simple' },
    enable_support: { type: 'coBool', label: 'Enable support', mode: 'simple' },
  },
  sections: { layer_height: 'process', wall_loops: 'process', enable_support: 'process' },
  it: {},
  object_keys: ['enable_support', 'layer_height', 'wall_loops'],
  region_keys: ['wall_loops'],
  plate_keys: {},
};
const opts = (layer_height: string) => ({ extruder: '0', layer_height });
const range = (min_z: number, max_z: number, layer_height = '0.2'): LayerRange => ({ min_z, max_z, options: opts(layer_height) });
const CUBE = {
  id: '4',
  name: 'Cube B',
  extruder: '1',
  parts: [{ id: '1', name: 'Cube B', type: 'normal_part' }],
  overrides: [{ key: 'layer_height', value: '0.12', part: null, part_id: null }],
  ranges: [range(0, 2, '0.1')],
} as WorkspaceObject;
const DOC = {
  editable: true,
  plates: [{ index: 1, name: null, object_ids: ['4'], instances: [], metadata: { plater_id: '1' } }],
  objects: [CUBE],
  settings: SETTINGS,
} as unknown as WorkspaceDocument;
const minLayer = () => 0.07;

describe('the ranges (ranges.ts)', () => {
  it("starts with 0-2 mm at the object's own layer height and filament 0", () => {
    expect(defaultOptions(CUBE, undefined, '0.2')).toEqual({ layer_height: '0.12', extruder: '0' });
    expect(defaultOptions({ ...CUBE, overrides: [] }, undefined, '0.2')).toEqual({ layer_height: '0.2', extruder: '0' });
    expect(firstRanges(opts('0.2'))).toEqual([range(0, 2)]);
  });

  it('adds after the last, splits the next one, fills a gap (add_layer_range_after_current)', () => {
    const d = opts('0.3');
    expect(addAfter([range(0, 2)], 0, d, minLayer)).toEqual([range(0, 2), { min_z: 2, max_z: 4, options: d }]);
    // the next starts where this ends: split at its middle, its settings kept on the upper half
    expect(addAfter([range(0, 2), range(2, 4, '0.1')], 0, d, minLayer)).toEqual([
      range(0, 2),
      { min_z: 2, max_z: 3, options: d },
      range(3, 4, '0.1'),
    ]);
    expect(addAfter([range(0, 2), range(5, 6)], 0, d, minLayer)).toEqual([range(0, 2), { min_z: 2, max_z: 5, options: d }, range(5, 6)]);
    // too thin to split, or a gap thinner than a layer: nothing
    expect(addAfter([range(0, 2), range(2, 2.1)], 0, d, minLayer)).toBeNull();
    expect(addAfter([range(0, 2), range(2.05, 3)], 0, d, minLayer)).toBeNull();
  });

  it('edits a Z as LayerRangeEditor does', () => {
    // a start past the end takes the end with it (half a millimetre after), and the ranges stay in order
    expect(editMin([range(0, 2), range(3, 4)], 0, 5)).toEqual([range(3, 4), range(5, 5.5)]);
    expect(editMin([range(0, 2)], 0, 1)).toEqual([range(1, 2)]);
    expect(editMin([range(0, 2)], 0, 0)).toBeNull();
    // an end below the start is refused; overlaps are Orca's to keep
    expect(editMax([range(1, 2)], 0, 0.5)).toBeNull();
    expect(editMax([range(0, 2), range(3, 4)], 0, 3.5)).toEqual([range(0, 3.5), range(3, 4)]);
  });

  it('reads a Z with either separator and refuses what Orca calls "Invalid numeric."', () => {
    expect(parseZ('1,5')).toBe(1.5);
    expect(parseZ(' 2 ')).toBe(2);
    expect(parseZ('.')).toBe(0);
    expect(parseZ('-1')).toBeNull();
    expect(parseZ('abc')).toBeNull();
  });

  it('labels a range as Orca does, and drops an edit back to the file', () => {
    expect(rangeLabel(range(0, 2.125), 'Range', 'mm')).toBe('Range 0.00-2.13 (mm)');
    expect(withRanges({}, CUBE, [range(0, 2, '0.1')])).toEqual({});
    expect(rangesToSave(withRanges({}, CUBE, []))).toEqual([{ object_id: '4', ranges: [] }]);
  });

  it("is the object menu's Height Range Modifier", () => {
    let clicked = 0;
    const on = { select: () => {}, zoom: () => {}, info: () => {}, settings: () => {} };
    const item = (heightRange?: () => void) => objectMenu((s) => s, 'Del', true, { ...on, heightRange }).find((i) => i.label === 'Height Range Modifier');
    expect(item()?.onClick).toBeUndefined(); // greyed where the copy can't write them
    item(() => clicked++)?.onClick?.();
    expect(clicked).toBe(1);
  });
});

function Harness({ initial }: { initial: Selection }) {
  const { own, setOwn, setOwnMany, ranges, setRanges, draft, undo } = useSettingsDraft(SETTINGS);
  const [selection, setSelection] = useState<Selection>(initial);
  return (
    <>
      <output data-testid="saved">{JSON.stringify(rangesToSave(ranges))}</output>
      <output data-testid="selection">{JSON.stringify(selection)}</output>
      <button onClick={undo}>undo</button>
      <ObjectsPanel
        layout={LAYOUT}
        doc={DOC}
        draft={draft}
        colours={['#ffffff', '#000000']}
        stats={new Map()}
        selection={selection}
        onSelect={setSelection}
        onMenu={() => {}}
        own={{
          edits: own,
          set: setOwn,
          setMany: setOwnMany,
          ranges: {
            edits: ranges,
            set: (obj, next, key = null) => setRanges((r) => withRanges(r, obj, next), key),
            defaults: (obj) => defaultOptions(obj, own, '0.2'),
            minLayerHeight: minLayer,
          },
        }}
      />
    </>
  );
}
const saved = () => JSON.parse(screen.getByTestId('saved').textContent || '[]');
const selected = () => JSON.parse(screen.getByTestId('selection').textContent || 'null');

describe('the Height Range Modifier in the objects list', () => {
  it('lists the ranges under Layers and edits them all from there', async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ kind: 'object', id: '4', plate: 1, layers: true, range: null }} />);
    expect(screen.getByText('Layers').closest('[role=treeitem]')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Range 0.00-2.00 (mm)')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Add height range: Range 0.00-2.00 (mm)' }));
    expect(saved()).toEqual([{ object_id: '4', ranges: [range(0, 2, '0.1'), range(2, 4, '0.12')] }]);
    expect(screen.getByText('Range 2.00-4.00 (mm)')).toBeInTheDocument();

    // a value Orca can't read: its message, and the field shows the range's value again
    const max = screen.getByRole('textbox', { name: 'Range 2.00-4.00 (mm): Z max' });
    await user.clear(max);
    await user.type(max, 'abc{Enter}');
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid numeric.');
    expect(max).toHaveValue('4');
    await user.clear(max);
    await user.type(max, '5{Enter}');
    expect(saved()[0].ranges[1]).toEqual(range(2, 5, '0.12'));

    await user.click(screen.getByRole('button', { name: 'Remove height range: Range 0.00-2.00 (mm)' }));
    expect(saved()[0].ranges).toEqual([range(2, 5, '0.12')]);
    await user.click(screen.getByRole('button', { name: 'undo' }));
    expect(saved()[0].ranges).toHaveLength(2);
  });

  it("edits one range's own settings (TabPrintLayer) and follows it when its Z moves", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ kind: 'object', id: '4', plate: 1, layers: true, range: 0 }} />);
    // its layer height and the region's settings, nothing an object only can have
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Frequent1', 'Quality1', 'Strength']);
    const height = screen.getByRole('textbox', { name: 'Layer height' });
    expect(height).toHaveValue('0.1');
    await user.clear(height);
    await user.type(height, '0.08');
    expect(saved()).toEqual([{ object_id: '4', ranges: [range(0, 2, '0.08')] }]);
    // typing in one field is one undo step
    await user.click(screen.getByRole('button', { name: 'undo' }));
    expect(saved()).toEqual([]);

    const min = screen.getByRole('textbox', { name: 'Range 0.00-2.00 (mm): Z min' });
    await user.clear(min);
    await user.type(min, '1{Enter}');
    expect(saved()).toEqual([{ object_id: '4', ranges: [range(1, 2, '0.1')] }]);
    expect(selected()).toMatchObject({ layers: true, range: 0 });
  });
});
