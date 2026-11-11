/**
 * Printfarm (fork): 3MF Workspace, batch E3a. An object's and a part's own settings edited as in OrcaSlicer's object
 * and part tabs (TabPrintObject, TabPrintPart): a value changed becomes its own, the reset arrow drops it, the
 * list's cog (colEditing) drops them all, each a step of the undo history; what the copy is asked to save.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import type { WorkspaceDocument, WorkspaceSetting } from '../../farm/workspace/api';
import { draftHistory, useSettingsDraft, type DraftHistory } from '../../farm/workspace/draft';
import { ObjectsPanel } from '../../farm/workspace/ObjectsPanel';
import { type Selection, editTarget, hiddenPart, ownSettings, ownToSave } from '../../farm/workspace/objects';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';

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
  { ...base, key: 'sparse_infill_density', label: 'Sparse infill density', section: 'process', type: 'float', percent: 'always', value: '15%' },
  { ...base, key: 'enable_support', label: 'Enable support', section: 'process', type: 'bool', value: '0' },
];
const LAYOUT: OrcaLayout = {
  tag: 'test',
  source: '',
  pages: {
    process: [
      { title: 'Quality', icon: '', groups: [{ title: 'Layer height', icon: null, keys: ['layer_height'] }] },
      { title: 'Strength', icon: '', groups: [{ title: 'Walls', icon: null, keys: ['wall_loops', 'sparse_infill_density'] }] },
      { title: 'Support', icon: '', groups: [{ title: 'Support', icon: null, keys: ['enable_support'] }] },
    ],
    filament: [],
    printer: [],
  },
  options: {
    layer_height: { type: 'coFloat', label: 'Layer height', mode: 'simple' },
    wall_loops: { type: 'coInt', label: 'Wall loops', mode: 'simple' },
    sparse_infill_density: { type: 'coPercent', label: 'Sparse infill density', mode: 'simple' },
    enable_support: { type: 'coBool', label: 'Enable support', mode: 'simple' },
  },
  sections: { layer_height: 'process', wall_loops: 'process', sparse_infill_density: 'process', enable_support: 'process' },
  it: {},
  object_keys: ['enable_support', 'layer_height', 'sparse_infill_density', 'wall_loops'],
  region_keys: ['sparse_infill_density', 'wall_loops'],
  plate_keys: {},
};
const DOC = {
  editable: true,
  plates: [{ index: 1, name: null, object_ids: ['2', '4'], instances: [], metadata: { plater_id: '1' } }],
  objects: [
    {
      // one part named as the object: OrcaSlicer's list doesn't show it, its settings show with the object's
      id: '2',
      name: 'Cube A',
      extruder: '1',
      parts: [{ id: '1', name: 'Cube A', type: 'normal_part' }],
      overrides: [{ key: 'sparse_infill_density', value: '40%', part: 'Cube A', part_id: '1' }],
    },
    {
      id: '4',
      name: 'Cube B',
      extruder: '1',
      parts: [
        { id: '1', name: 'Cube B', type: 'normal_part' },
        { id: '3', name: 'Box', type: 'modifier_part' },
        { id: '5', name: 'Hole', type: 'negative_part' },
      ],
      overrides: [
        { key: 'wall_loops', value: '5', part: null, part_id: null },
        { key: 'sparse_infill_density', value: '80%', part: 'Box', part_id: '3' },
      ],
    },
  ],
  settings: SETTINGS,
} as unknown as WorkspaceDocument;

function Harness({ initial = null }: { initial?: Selection }) {
  const { own, setOwn, setOwnMany, draft, undo } = useSettingsDraft(SETTINGS);
  const [selection, setSelection] = useState<Selection>(initial);
  return (
    <>
      <output data-testid="saved">{JSON.stringify(ownToSave(own))}</output>
      <button onClick={undo}>undo</button>
      <ObjectsPanel
        layout={LAYOUT}
        doc={DOC}
        draft={draft}
        colours={['#ffffff']}
        stats={new Map()}
        selection={selection}
        onSelect={setSelection}
        onMenu={() => {}}
        own={{ edits: own, set: setOwn, setMany: setOwnMany }}
      />
    </>
  );
}
const saved = () => JSON.parse(screen.getByTestId('saved').textContent || '[]');

describe("an object's own settings", () => {
  it('edits, resets and undoes them as OrcaSlicer does', async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ kind: 'object', id: '4', plate: 1 }} />);
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Frequent1', 'Quality', 'Strength1', 'Support']);
    const walls = screen.getByRole('textbox', { name: 'Wall loops' });
    expect(walls).toHaveValue('5');
    expect(screen.getByTestId('setting-wall_loops')).toHaveTextContent('global: 3');
    await user.clear(walls);
    await user.type(walls, '6');
    expect(saved()).toEqual([{ object_id: '4', part_id: null, key: 'wall_loops', value: '6' }]);
    // a value set where the project's applies becomes the object's own too
    await user.click(screen.getByRole('checkbox', { name: 'Enable support' }));
    expect(saved()).toContainEqual({ object_id: '4', part_id: null, key: 'enable_support', value: '1' });
    // the reset arrow: no longer its own
    await user.click(screen.getByRole('button', { name: /Wall loops/ }));
    expect(saved()).toContainEqual({ object_id: '4', part_id: null, key: 'wall_loops', value: null });
    expect(screen.getByRole('textbox', { name: 'Wall loops' })).toHaveValue('3');
    await user.click(screen.getByRole('button', { name: 'undo' }));
    expect(screen.getByRole('textbox', { name: 'Wall loops' })).toHaveValue('6');
  });

  it('offers a modifier the part settings only, and nothing to edit on a negative part', async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Harness initial={{ kind: 'object', id: '4', plate: 1, part: 'Box', partId: '3' }} />);
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Frequent1', 'Strength1']);
    const density = screen.getByRole('textbox', { name: 'Sparse infill density' });
    await user.clear(density);
    await user.type(density, '60%');
    expect(saved()).toEqual([{ object_id: '4', part_id: '3', key: 'sparse_infill_density', value: '60%' }]);
    unmount();
    render(<Harness initial={{ kind: 'object', id: '4', plate: 1, part: 'Hole', partId: '5' }} />);
    expect(screen.queryByRole('textbox', { name: 'Wall loops' })).not.toBeInTheDocument();
  });

  it("edits the hidden part's key where it is, and the cog drops every own setting", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ kind: 'object', id: '2', plate: 1 }} />);
    const density = screen.getByRole('textbox', { name: 'Sparse infill density' });
    expect(density).toHaveValue('40%');
    await user.clear(density);
    await user.type(density, '30%');
    expect(saved()).toEqual([{ object_id: '2', part_id: '1', key: 'sparse_infill_density', value: '30%' }]);
    const row = within(screen.getByRole('tree')).getByText('Cube A').closest('div')!;
    await user.click(within(row).getByRole('button', { name: 'Click the icon to reset all settings of the object' }));
    expect(saved()).toEqual([{ object_id: '2', part_id: '1', key: 'sparse_infill_density', value: null }]);
    expect(within(row).queryByRole('button', { name: /reset all/ })).not.toBeInTheDocument();
  });
});

describe('own settings helpers', () => {
  const [cubeA, cubeB] = DOC.objects;

  it('finds whose a key is and what is saved', () => {
    expect(hiddenPart(cubeA)).toBe('1');
    expect(hiddenPart(cubeB)).toBeNull();
    expect(ownSettings(cubeA)).toEqual({ sparse_infill_density: '40%' });
    expect(editTarget(cubeA, null, 'sparse_infill_density')).toBe('2/1');
    expect(editTarget(cubeA, null, 'wall_loops')).toBe('2');
    expect(ownSettings(cubeB, '3', { '4/3': { sparse_infill_density: null, wall_loops: '2' } })).toEqual({ wall_loops: '2' });
  });

  it('keeps typing in one field as one undo step, and Undo all drops the own edits too', () => {
    let h: DraftHistory = { draft: {}, past: [], future: [], last: null };
    const own = (value: string, at: number) =>
      draftHistory(h, { type: 'own', edit: (o) => ({ ...o, '4': { wall_loops: value } }), key: '4|wall_loops', at });
    h = own('6', 0);
    h = own('61', 100);
    expect(h.past).toHaveLength(1);
    expect(h.own).toEqual({ '4': { wall_loops: '61' } });
    h = draftHistory(h, { type: 'switch', switched: { refs: [], values: {} }, draft: {}, placed: {}, own: {} });
    expect(h.own).toEqual({});
    h = draftHistory(h, { type: 'undo' });
    expect(h.own).toEqual({ '4': { wall_loops: '61' } });
  });
});
