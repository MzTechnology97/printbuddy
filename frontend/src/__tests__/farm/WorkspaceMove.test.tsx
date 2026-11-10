/**
 * Printfarm (fork): the 3MF Workspace's Move (batch E2a). 3MF transforms read as OrcaSlicer reads them, an
 * instance placed anew in the 3D view and in the copy's numbers, the Move gizmo's drag, the Move window, and a
 * move as one undo step.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import * as THREE from 'three';
import JSZip from 'jszip';
import { render } from '../utils';
import { buildModelGroup, parse3MF, type ViewerScene } from '../../components/ModelViewer';
import { type DraftHistory, draftHistory } from '../../farm/workspace/draft';
import { projection, selectionBox } from '../../farm/workspace/moveGizmo';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';
import { applyPlacements, fromSpec, heightAboveBed, placementsToSave, selectedItems, toSpec, withMoved } from '../../farm/workspace/placement';
import { MainToolbar, MoveWindow } from '../../farm/workspace/Viewport';

const LAYOUT = { tag: 'v', source: 's', pages: { process: [], filament: [], printer: [] }, options: {}, object_keys: [], plate_keys: {}, it: {} } as unknown as OrcaLayout;
const TURNED = [0, 1, 0, -1, 0, 0, 0, 0, 1, 0, 0, 0]; // 90° about Z, as 3MF stores it

/** A 10 mm cube project: one build item of object 1 per transform, shown as the workspace shows it. */
async function scene(...transforms: string[]): Promise<ViewerScene> {
  const zip = new JSZip();
  const v = [0, 10].flatMap((x) => [0, 10].flatMap((y) => [0, 10].map((z) => `<vertex x="${x}" y="${y}" z="${z}"/>`))).join('');
  const faces = [[0, 1, 2], [1, 3, 2], [4, 6, 5], [5, 6, 7], [0, 4, 1], [1, 4, 5], [2, 3, 6], [3, 7, 6], [0, 2, 4], [2, 6, 4], [1, 5, 3], [3, 5, 7]];
  const t = faces.map(([a, b, c]) => `<triangle v1="${a}" v2="${b}" v3="${c}"/>`).join('');
  zip.file(
    '3D/3dmodel.model',
    `<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>` +
      `<object id="1" type="model"><mesh><vertices>${v}</vertices><triangles>${t}</triangles></mesh></object></resources>` +
      `<build>${transforms.map((tr) => `<item objectid="1" transform="${tr}"/>`).join('')}</build></model>`,
  );
  const model = buildModelGroup(await parse3MF(await zip.generateAsync({ type: 'arraybuffer' })), null, [], true);
  model.scale.z = -1; // as viewTools.unmirror shows it
  model.position.z = 256;
  model.updateMatrixWorld(true);
  return { model } as unknown as ViewerScene;
}

/** The instances' box in the slicer's axes: [min x, min y, min z, max x, max y, max z]. */
function slicerBox(view: ViewerScene, keys: string[]) {
  const b = selectionBox(view, keys)!;
  return [b.min.x, b.min.z, b.min.y, b.max.x, b.max.z, b.max.y].map((n) => Math.round(n * 1000) / 1000 + 0);
}

describe('3MF transforms, as OrcaSlicer reads them', () => {
  it('stores the 4x3 matrix by columns: a turned instance turns the way the slicer turns it', async () => {
    expect(new THREE.Vector3(10, 0, 0).applyMatrix4(fromSpec(TURNED)).toArray().map((n) => Math.round(n) + 0)).toEqual([0, 10, 0]);
    const view = await scene(TURNED.join(' '));
    expect(slicerBox(view, ['1:0'])).toEqual([-10, 0, 0, 0, 10, 10]);
    expect(view.model.children[0].userData.transform).toEqual(TURNED);
    expect(toSpec(fromSpec([...TURNED.slice(0, 9), 12.5, -3, 4]))).toEqual([...TURNED.slice(0, 9), 12.5, -3, 4]);
  });
});

describe('an instance placed anew (placement.ts)', () => {
  const FILE = '1 0 0 0 1 0 0 0 1 100 110 0';

  it('moves in the 3D view as the copy will place it, and back where the file has it is no placement', async () => {
    const view = await scene(FILE, '1 0 0 0 1 0 0 0 1 150 110 0');
    expect(slicerBox(view, ['1:0'])).toEqual([100, 110, 0, 110, 120, 10]);
    const placed = withMoved(view, {}, ['1:0'], [-60, 5, 2]);
    expect(placed).toEqual({ '1:0': [1, 0, 0, 0, 1, 0, 0, 0, 1, 40, 115, 2] });
    applyPlacements(view, placed);
    expect(slicerBox(view, ['1:0'])).toEqual([40, 115, 2, 50, 125, 12]);
    expect(slicerBox(view, ['1:1'])).toEqual([150, 110, 0, 160, 120, 10]); // the other instance stays
    expect(heightAboveBed(view, ['1:0'])).toBeCloseTo(2);
    expect(withMoved(view, placed, ['1:0'], [60, -5, -2])).toEqual({});
    expect(placementsToSave(placed)).toEqual([{ object_id: '1', instance: 0, matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1, 40, 115, 2] }]);
    applyPlacements(view, {});
    expect(slicerBox(view, ['1:0'])).toEqual([100, 110, 0, 110, 120, 10]);
  });

  it('a turned instance moves on the slicer axes, not its own', async () => {
    const view = await scene([...TURNED.slice(0, 9), 50, 50, 0].join(' '));
    applyPlacements(view, withMoved(view, {}, ['1:0'], [10, 0, 0]));
    expect(slicerBox(view, ['1:0'])).toEqual([50, 50, 0, 60, 60, 10]);
  });

  it('a selection moves one instance, or every instance of the objects selected', async () => {
    const view = await scene(FILE, '1 0 0 0 1 0 0 0 1 150 110 0');
    expect(selectedItems(view, ['1'], 1)).toEqual(['1:1']);
    expect(selectedItems(view, ['1'], null)).toEqual(['1:0', '1:1']);
    expect(selectedItems(null, ['1'], null)).toEqual([]);
  });
});

describe('the Move gizmo (GLGizmoMove3D)', () => {
  it('takes the pointer ray to a move along its axis, in 1 mm steps with Shift', () => {
    const ray = new THREE.Ray(new THREE.Vector3(5.4, 10, 0), new THREE.Vector3(0, -1, 0));
    const x = new THREE.Vector3(1, 0, 0);
    expect(projection(ray, new THREE.Vector3(0, 0, 0), x, false)).toBeCloseTo(5.4);
    expect(projection(ray, new THREE.Vector3(0, 0, 0), x, true)).toBe(5);
  });
});

describe("a move is one undo step (D1's history)", () => {
  const start: DraftHistory = { draft: {}, past: [], future: [], last: null };
  it('Undo brings the instance back, Redo moves it again; Undo all takes it with the edits', () => {
    let h = draftHistory(start, { type: 'edit', edit: (d) => ({ ...d, layer_height: '0.1' }), key: 'layer_height', at: 0 });
    h = draftHistory(h, { type: 'place', placed: { '1:0': [1, 0, 0, 0, 1, 0, 0, 0, 1, 40, 110, 0] } });
    expect(h.past).toHaveLength(2);
    h = draftHistory(h, { type: 'undo' });
    expect([h.placed ?? {}, h.draft]).toEqual([{}, { layer_height: '0.1' }]);
    h = draftHistory(h, { type: 'redo' });
    expect(Object.keys(h.placed ?? {})).toEqual(['1:0']);
    h = draftHistory(h, { type: 'switch', switched: { refs: [], values: {} }, draft: {}, placed: {} }); // Undo all
    expect([h.placed, h.draft]).toEqual([{}, {}]);
    h = draftHistory(h, { type: 'undo' });
    expect(Object.keys(h.placed ?? {})).toEqual(['1:0']);
    expect(draftHistory(h, { type: 'place', placed: h.placed! })).toBe(h); // the same placement is no step
  });
});

describe("Orca's Move window and toolbar", () => {
  it('shows the position, commits a field on Enter, greys Drop on the bed; in Object coordinates a move from 0', () => {
    const onPosition = vi.fn();
    const onCs = vi.fn();
    const { rerender } = render(
      <MoveWindow layout={LAYOUT} position={[100, 110, 0]} canDrop={false} onCs={onCs} onPosition={onPosition} onDrop={vi.fn()} onDone={vi.fn()} />,
    );
    const x = screen.getByRole('textbox', { name: 'Position X' });
    expect(x).toHaveValue('100.00');
    fireEvent.change(x, { target: { value: '40' } });
    fireEvent.keyDown(x, { key: 'Enter' });
    expect(onPosition).toHaveBeenCalledWith(0, 40);
    expect(screen.getByRole('button', { name: 'Drop' })).toBeDisabled();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'object' } });
    expect(onCs).toHaveBeenCalledWith('object');
    rerender(<MoveWindow layout={LAYOUT} position={[100, 110, 0]} cs="object" canDrop onPosition={onPosition} onDrop={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByRole('textbox', { name: 'Relative X' })).toHaveValue('0.00');
  });

  it('Move works with a selection, and says why it does not without one', () => {
    const { unmount } = render(<MainToolbar layout={LAYOUT} active={null} onTool={vi.fn()} ready={{ move: true }} />);
    expect(screen.getByRole('button', { name: 'Move [M]' })).toBeEnabled();
    unmount();
    render(<MainToolbar layout={LAYOUT} active={null} onTool={vi.fn()} ready={{ move: 'Please select at least one object.' }} />);
    expect(screen.getByRole('button', { name: 'Move:\nPlease select at least one object.' })).toBeDisabled();
  });
});
