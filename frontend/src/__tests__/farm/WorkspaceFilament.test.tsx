/**
 * Printfarm (fork): 3MF Workspace, batch E3b. An object's and a part's filament, as OrcaSlicer's object list sets
 * it: the Fila. column's list (``update_filament_in_config``: an object's choice drops its parts' own; "default"
 * for a modifier) and keys 0-9 on the selection (``set_extruder_for_selected_items``), each one undo step; what the
 * copy is asked to save; the 3D view in each part's filament colour. Made-up data.
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as THREE from 'three';
import { render } from '../utils';
import type { ViewerScene } from '../../components/ModelViewer';
import type { WorkspaceDocument, WorkspaceObject } from '../../farm/workspace/api';
import { useSettingsDraft } from '../../farm/workspace/draft';
import { ObjectsPanel } from '../../farm/workspace/ObjectsPanel';
import { type Selection, filamentEdits, filamentOf, ownToSave, printedFilament } from '../../farm/workspace/objects';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';
import { colourFilaments } from '../../farm/workspace/viewTools';

const COLOURS = ['#ffffff', '#ff0000', '#0000ff'];
const LAYOUT: OrcaLayout = {
  tag: 'test',
  source: '',
  pages: { process: [], filament: [], printer: [] },
  options: {},
  it: {},
  object_keys: [],
  region_keys: [],
  plate_keys: {},
};
const CUBE: WorkspaceObject = {
  id: '4',
  name: 'Cube',
  extruder: '1',
  parts: [
    { id: '1', name: 'Body', type: 'normal_part', extruder: null },
    { id: '2', name: 'Lid', type: 'normal_part', extruder: '3' },
    { id: '3', name: 'Box', type: 'modifier_part', extruder: '2' },
    { id: '5', name: 'Hole', type: 'negative_part', extruder: null },
  ],
  overrides: [],
};
const DOC = {
  editable: true,
  plates: [{ index: 1, name: null, object_ids: ['4'], instances: [], metadata: { plater_id: '1' } }],
  objects: [CUBE],
  settings: [],
} as unknown as WorkspaceDocument;

function Harness({ initial = null, colours = COLOURS }: { initial?: Selection; colours?: string[] }) {
  const { own, setOwn, setOwnMany, setOwnTargets, draft, undo } = useSettingsDraft([]);
  const [selection, setSelection] = useState<Selection>(initial);
  return (
    <>
      <output data-testid="saved">{JSON.stringify(ownToSave(own))}</output>
      <button onClick={undo}>undo</button>
      <ObjectsPanel
        layout={LAYOUT}
        doc={DOC}
        draft={draft}
        colours={colours}
        stats={new Map()}
        selection={selection}
        onSelect={setSelection}
        onMenu={() => {}}
        own={{ edits: own, set: setOwn, setMany: setOwnMany, setTargets: setOwnTargets }}
      />
    </>
  );
}
const saved = () =>
  Object.fromEntries(
    (JSON.parse(screen.getByTestId('saved').textContent || '[]') as { object_id: string; part_id: string | null; value: string | null }[]).map(
      (e) => [e.part_id ? `${e.object_id}/${e.part_id}` : e.object_id, e.value],
    ),
  );

describe('the Fila. column', () => {
  it("sets an object's filament from its list, dropping its parts' own, in one undo step", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const tree = screen.getByRole('tree');
    // the parts' chips: Body has none (its object's 1, dimmed), Lid 3, the modifier 2
    expect(within(tree).getByRole('button', { name: 'Filament default' })).toBeInTheDocument(); // Body, its own none
    await user.click(within(tree).getByRole('button', { name: 'Filament 1' }));
    const list = screen.getByRole('listbox', { name: 'Fila.' });
    expect(within(list).getAllByRole('option').map((o) => o.textContent)).toEqual(['11', '22', '33']); // no "default"
    await user.click(within(list).getAllByRole('button')[1]); // filament 2
    expect(saved()).toEqual({ '4': '2', '4/2': null, '4/3': null }); // Lid's and the modifier's own dropped
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'undo' }));
    expect(saved()).toEqual({});
  });

  it('offers a modifier "default", and a part its filaments', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const tree = screen.getByRole('tree');
    await user.click(within(tree).getByRole('button', { name: 'Filament 2' })); // the modifier
    const list = screen.getByRole('listbox');
    expect(within(list).getAllByRole('option').map((o) => o.textContent)).toEqual(['–default', '11', '22', '33']);
    await user.click(within(list).getAllByRole('button')[0]); // default
    expect(saved()).toEqual({ '4/3': '0' });
    await user.click(within(tree).getByRole('button', { name: 'Filament 3' })); // Lid
    await user.click(within(screen.getByRole('listbox')).getAllByRole('button')[0]); // filament 1
    expect(saved()).toEqual({ '4/3': '0', '4/2': '1' });
  });

  it("sets the selection's filament with keys 0-9, as the list does", () => {
    const { unmount } = render(<Harness initial={{ kind: 'object', id: '4', plate: 1 }} />);
    const tree = screen.getByRole('tree');
    fireEvent.keyDown(tree, { key: '3' });
    expect(saved()).toEqual({ '4': '3', '4/2': null }); // the model parts' own dropped, the modifier's kept
    fireEvent.keyDown(tree, { key: '4' }); // no fourth filament
    fireEvent.keyDown(tree, { key: '0' }); // an object: filament 1, as it was
    expect(saved()).toEqual({ '4/2': null });
    unmount();
    render(<Harness initial={{ kind: 'object', id: '4', plate: 1, part: 'Box', partId: '3' }} />);
    fireEvent.keyDown(screen.getByRole('tree'), { key: '0' }); // a modifier: default
    expect(saved()).toEqual({ '4/3': '0' });
  });

  it('leaves keys alone with one filament', () => {
    render(<Harness initial={{ kind: 'object', id: '4', plate: 1 }} colours={['#ffffff']} />);
    fireEvent.keyDown(screen.getByRole('tree'), { key: '1' });
    expect(saved()).toEqual({});
  });
});

describe('filament helpers', () => {
  it("resolves what each prints with, and a part's 0 key is its object's filament", () => {
    expect(filamentOf(CUBE)).toBe(1);
    expect(filamentOf(CUBE, '1')).toBe(0);
    expect(printedFilament(CUBE, '1')).toBe(1);
    expect(printedFilament(CUBE, '1', { '4': { extruder: '2' } })).toBe(2);
    expect(filamentEdits(CUBE, '1', 0, 'keys', { '4': { extruder: '2' } })).toEqual({ '4/1': { extruder: '2' } });
    expect(filamentEdits(CUBE, '5', 2, 'column')).toEqual({}); // a negative part has no filament of its own
    expect(filamentEdits(CUBE, '2', 3, 'column')).toEqual({ '4/2': { extruder: undefined } }); // as the file has it
  });

  it('colours each unpainted model part with its filament, the edits made', () => {
    const part = (partId: string) => {
      const g = new THREE.Group();
      g.userData = { partId };
      g.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshPhongMaterial({ color: '#00ae42' })));
      return g;
    };
    const item = new THREE.Group();
    item.userData = { objectId: '4', paint: { color: false } };
    item.add(part('1'), part('2'), part('3'));
    const model = new THREE.Group();
    model.add(item);
    const colourOf = (i: number) =>
      `#${((item.children[i].children[0] as THREE.Mesh).material as THREE.MeshPhongMaterial).color.getHexString()}`;
    colourFilaments({ model } as unknown as ViewerScene, [CUBE], { '4': { extruder: '2' } }, COLOURS);
    expect([colourOf(0), colourOf(1), colourOf(2)]).toEqual(['#ff0000', '#0000ff', '#00ae42']); // its object's, its own, a modifier
  });
});
