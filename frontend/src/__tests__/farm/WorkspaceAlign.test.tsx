/**
 * Printfarm (fork): the 3MF Workspace's Lay on face, align and distribute (batch E2d), as OrcaSlicer v2.5.0's
 * GLGizmoFlatten / LayOnFace.cpp and GLGizmoAlignment: the instances or the parts of one instance lined up within the
 * selection or to their plate (object), the reasons a button is greyed, the instance's own axes in Object
 * coordinates, the faces of the hull and the turn that lays one down; several parts of an instance changed together.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import * as THREE from 'three';
import JSZip from 'jszip';
import { render } from '../utils';
import { buildModelGroup, parse3MF, type ViewerScene } from '../../components/ModelViewer';
import { type AlignSelection, alignMoves, alignSuffix, alignTargets, canAlign } from '../../farm/workspace/align';
import { facePlanes, laidOnFace } from '../../farm/workspace/layOnFace';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';
import { type Spec, applyPlacements, fromSpec, moved } from '../../farm/workspace/placement';
import { boxOf, changed, changedParts, targetKey } from '../../farm/workspace/transforms';
import { MoveWindow } from '../../farm/workspace/Viewport';

const LAYOUT = { tag: 'v', source: 's', pages: { process: [], filament: [], printer: [] }, options: {}, object_keys: [], plate_keys: {}, it: {} } as unknown as OrcaLayout;

const cubePoints = (size: number) => [0, size].flatMap((x) => [0, size].flatMap((y) => [0, size].map((z) => [x, y, z])));
const cube = (size: number) => {
  const v = cubePoints(size).map(([x, y, z]) => `<vertex x="${x}" y="${y}" z="${z}"/>`).join('');
  const faces = [[0, 1, 2], [1, 3, 2], [4, 6, 5], [5, 6, 7], [0, 4, 1], [1, 4, 5], [2, 3, 6], [3, 7, 6], [0, 2, 4], [2, 6, 4], [1, 5, 3], [3, 5, 7]];
  return `<mesh><vertices>${v}</vertices><triangles>${faces.map(([a, b, c]) => `<triangle v1="${a}" v2="${b}" v3="${c}"/>`).join('')}</triangles></mesh>`;
};

async function view(model: string, files: Record<string, string> = {}): Promise<ViewerScene> {
  const zip = new JSZip();
  zip.file('3D/3dmodel.model', model);
  for (const [name, text] of Object.entries(files)) zip.file(name, text);
  const group = buildModelGroup(await parse3MF(await zip.generateAsync({ type: 'arraybuffer' })), null, [], true);
  group.scale.z = -1;
  group.position.z = 256;
  group.updateMatrixWorld(true);
  return { model: group, bed: { x: 256, y: 256 } } as unknown as ViewerScene;
}
const NS = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"';

/** Objects 1, 2, 3: cubes of 10, 20 and 4 mm at X 10, 50 and 100 (Y 10, 30, 70). */
const three = () =>
  view(
    `<?xml version="1.0"?><model ${NS}><resources>` +
      `<object id="1" type="model">${cube(10)}</object><object id="2" type="model">${cube(20)}</object><object id="3" type="model">${cube(4)}</object>` +
      `</resources><build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 10 0"/><item objectid="2" transform="1 0 0 0 1 0 0 0 1 50 30 0"/>` +
      `<item objectid="3" transform="1 0 0 0 1 0 0 0 1 100 70 0"/></build></model>`,
  );

/** Object 2: part 1 (a 10 mm cube) and part 3 (a 4 mm one, 20 mm along X) in /3D/Objects/object_1.model; two
 * instances, the second turned 90° about Z (the same scene as WorkspaceParts). */
const parts = () =>
  view(
    `<?xml version="1.0"?><model ${NS} xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"><resources>` +
      `<object id="2" type="model"><components>` +
      `<component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>` +
      `<component p:path="/3D/Objects/object_1.model" objectid="3" transform="1 0 0 0 1 0 0 0 1 20 0 0"/>` +
      `</components></object></resources><build>` +
      `<item objectid="2" transform="1 0 0 0 1 0 0 0 1 100 100 0"/><item objectid="2" transform="0 1 0 -1 0 0 0 0 1 200 100 0"/>` +
      `</build></model>`,
    {
      '3D/Objects/object_1.model': `<?xml version="1.0"?><model ${NS}><resources><object id="1" type="model">${cube(10)}</object><object id="3" type="model">${cube(4)}</object></resources></model>`,
    },
  );

const sel = (v: ViewerScene, over: Partial<AlignSelection>): AlignSelection => ({
  view: v,
  placed: {},
  instances: [],
  parts: [],
  modelPart: () => true,
  objectCs: false,
  autoDrop: () => true,
  plateOf: () => 0,
  plateCount: 1,
  ...over,
});
const round = (m: Record<string, number[]> | null) =>
  m && Object.fromEntries(Object.entries(m).map(([k, v]) => [k, v.map((x) => Math.round(x * 1000) / 1000 + 0)]));
const X_MIN = { axis: 0, kind: 'min' } as const;

describe('align and distribute instances', () => {
  it('lines them up within the selection: left, centre; distributing spaces the centres between the outer two', async () => {
    const v = await three();
    const s = sel(v, { instances: ['1:0', '2:0', '3:0'] });
    expect(round(alignMoves(s, X_MIN, false))).toEqual({ '2:0': [-40, 0, 0], '3:0': [-90, 0, 0] });
    // the selection's box is X 10..104, its centre 57
    expect(round(alignMoves(s, { axis: 0, kind: 'center' }, false))).toEqual({ '1:0': [42, 0, 0], '2:0': [-3, 0, 0], '3:0': [-45, 0, 0] });
    // centres 15, 60, 102: the middle one to 58.5
    expect(round(alignMoves(s, { axis: 0, kind: 'distribute' }, false))).toEqual({ '2:0': [-1.5, 0, 0] });
    expect(canAlign(sel(v, { instances: ['1:0', '2:0'] }), { axis: 0, kind: 'distribute' }, false)).toBe(false); // three to distribute
    // and the move keeps them on the bed, as do_move
    const moves = alignMoves(s, { axis: 1, kind: 'max' }, false)!;
    const next = changed(v, {}, { keys: Object.keys(moves), apply: (spec, k) => moved(spec, moves[k] as [number, number, number]), sync: 'none', drop: 'move' }, () => true);
    expect(next['1:0'].slice(9)).toEqual([10, 64, 0]);
  });

  it('in Z only without Auto-Drop, and never to the plate; to the plate all on one plate, 0.1 mm in from its edge', async () => {
    const v = await three();
    const s = sel(v, { instances: ['1:0', '2:0', '3:0'] });
    const top = { axis: 2, kind: 'max' } as const;
    expect(canAlign(s, top, false)).toBe(false);
    expect(alignTargets(s, top, false).why).toBe('Turn off Auto-Drop for the selected objects to move them in Z');
    expect(round(alignMoves({ ...s, autoDrop: () => false }, top, false))).toEqual({ '1:0': [0, 0, 10], '3:0': [0, 0, 16] });
    expect(alignTargets({ ...s, autoDrop: () => false }, top, true).why).toBe('Aligning to the plate is available in X and Y only');
    // to the plate: the selection moves as one
    expect(round(alignMoves(s, X_MIN, true))).toEqual({ '1:0': [-9.9, 0, 0], '2:0': [-9.9, 0, 0], '3:0': [-9.9, 0, 0] });
    // the second of two plates is 1.2 beds along X (PartPlateList::compute_shape_position)
    expect(round(alignMoves({ ...s, plateOf: () => 1, plateCount: 2 }, X_MIN, true))?.['1:0']).toEqual([297.3, 0, 0]);
    const split = { ...s, plateOf: (k: string) => (k === '3:0' ? 1 : 0), plateCount: 2 };
    expect(alignTargets(split, X_MIN, true).why).toBe('All selected objects must be on the same plate');
    expect(canAlign(split, X_MIN, false)).toBe(true); // within the selection, any plate
  });
});

describe('align and distribute parts', () => {
  it("a lone instance's model parts within it; parts selected with Alt to the object holding them", async () => {
    const v = await parts();
    const lone = sel(v, { instances: ['2:0'] });
    expect(alignTargets(lone, X_MIN, false).targets).toEqual(['2:0/1', '2:0/3']);
    const right = alignMoves(lone, { axis: 0, kind: 'max' }, false)!;
    expect(round(right)).toEqual({ '2:0/1': [14, 0, 0] });
    // the part moves in every instance: its own transform is saved
    const next = changedParts(v, {}, Object.keys(right), (w, k) => moved(w, right[k] as [number, number, number]), 'move', () => true);
    expect(next['2/1']).toEqual([1, 0, 0, 0, 1, 0, 0, 0, 1, 14, 0, 0]);
    // to the parent: the instance's box (its model parts), here X 100..124
    const alt = sel(v, { parts: ['2:0/3'] });
    expect(round(alignMoves(alt, X_MIN, true))).toEqual({ '2:0/3': [-20, 0, 0] });
    // a lone instance aligns to its plate (Instance mode), parts to their object only when selected with Alt
    expect(alignTargets(lone, X_MIN, true).targets).toEqual(['2:0']);
    // modifiers, negative volumes and blockers stay where they are
    const onlyOne = sel(v, { instances: ['2:0'], modelPart: (_, p) => p === '1' });
    expect(canAlign(onlyOne, X_MIN, false)).toBe(false);
  });

  it("distributes parts by the outer parts' widths; in Object coordinates on the instance's axes", async () => {
    const v = await parts();
    expect(alignMoves(sel(v, { parts: ['2:0/1', '2:0/3'] }), { axis: 0, kind: 'distribute' }, false)).toBeNull(); // two parts
    // instance 1 is turned 90° about Z: along its own X the small part sits 20 mm from the big one's left side
    const turned = sel(v, { parts: ['2:1/1', '2:1/3'], objectCs: true });
    const moves = alignMoves(turned, X_MIN, false)!;
    expect(round(moves)).toEqual({ '2:1/3': [0, -20, 0] }); // the instance's X is the bed's Y
    const next = changedParts(v, {}, Object.keys(moves), (w, k) => moved(w, moves[k] as [number, number, number]), 'move', () => true);
    expect(next['2/3'].slice(9).map((n) => Math.round(n * 1e6) / 1e6 + 0)).toEqual([0, 0, 0]); // in the object, on the big part
  });
});

describe('several parts of one instance', () => {
  it('change together, each kept in every instance; the object stays on the bed', async () => {
    const v = await parts();
    const both = [targetKey('2', 0, '1'), targetKey('2', 0, '3')];
    const up = changedParts(v, {}, both, (w) => moved(w, [5, 0, 3]), 'move', () => true);
    expect(up['2/1'].slice(9)).toEqual([5, 0, 3]);
    expect(up['2/3'].slice(9)).toEqual([25, 0, 3]);
    expect(up['2:0'][11]).toBe(-3); // nothing on the bed any more: the object drops
    applyPlacements(v, up);
    expect(boxOf(v, up, ['2:0'])!.min.toArray().map((n) => Math.round(n) + 0)).toEqual([105, 100, 0]);
    expect(changedParts(v, {}, [targetKey('2', 0, '1'), targetKey('2', 1, '3')], (w) => w, 'move', () => true)).toEqual({}); // one instance only
  });
});

describe('Lay on face', () => {
  it("offers the hull's faces, largest first, too small ones left out, and turns the chosen one down", () => {
    const points = cubePoints(10).flat();
    const at: Spec = [1, 0, 0, 0, 1, 0, 0, 0, 1, 50, 50, 0];
    const planes = facePlanes(points, at);
    expect(planes).toHaveLength(6);
    expect(planes.every((p) => Math.abs(p.area - 100) < 1e-6)).toBe(true);
    // drawn inside the face, 0.1 mm off it
    const top = planes.find((p) => p.normal.z > 0.99)!;
    expect(Math.min(...top.outline.map((v) => v.x))).toBeCloseTo(0.5, 6);
    expect(top.outline.every((v) => Math.abs(v.z - 10.1) < 1e-6)).toBe(true);
    // a 2 mm cube as printed: 4 mm² faces, too small to rest on
    expect(facePlanes(points, [0.2, 0, 0, 0, 0.2, 0, 0, 0, 0.2, 0, 0, 0])).toEqual([]);
    // the +X face down: X ends up pointing down; the top face down: over about X, its footprint kept
    const side = fromSpec(laidOnFace(at, new THREE.Vector3(1, 0, 0)));
    expect(new THREE.Vector3(1, 0, 0).transformDirection(side).toArray().map((n) => Math.round(n) + 0)).toEqual([0, 0, -1]);
    expect(laidOnFace(at, new THREE.Vector3(0, 0, 1)).map((n) => Math.round(n) + 0)).toEqual([1, 0, 0, 0, -1, 0, 0, 0, -1, 50, 50, 0]);
  });

  it('puts the object on the bed afterwards, even when it was sinking (do_rotate for "Gizmo-Place on Face")', async () => {
    const v = await three();
    const sunk = { '1:0': [1, 0, 0, 0, 1, 0, 0, 0, 1, 10, 10, -3] };
    const lay = (drop: 'transform' | 'flatten') =>
      changed(v, sunk, { keys: ['1:0'], apply: (spec) => laidOnFace(spec, new THREE.Vector3(0, 0, -1)), sync: 'general', drop }, () => true);
    expect(lay('transform')['1:0'][11]).toBe(-3); // a rotation leaves a sinking object sinking
    expect(lay('flatten')['1:0']).toBeUndefined(); // Lay on face puts it back on the bed: where the file has it
    // on its +X face: turned about Y, lifted onto the bed
    const side = changed(v, {}, { keys: ['1:0'], apply: (spec) => laidOnFace(spec, new THREE.Vector3(1, 0, 0)), sync: 'general', drop: 'flatten' }, () => true);
    expect(side['1:0'].map((n) => Math.round(n * 1e6) / 1e6 + 0)).toEqual([0, 0, -1, 0, 1, 0, 1, 0, 0, 10, 10, 10]);
  });
});

describe("the Move window's align half", () => {
  it('offers Selection and the parent, runs an enabled button, says why a greyed one is', () => {
    const onRun = vi.fn();
    const onHover = vi.fn();
    const onToParent = vi.fn();
    const b = (axis: 0 | 1 | 2, kind: 'min' | 'center' | 'max' | 'distribute', enabled = true, why: string | null = null) => ({
      type: { axis, kind },
      icon: 'align_x_min_dark',
      label: `${kind}${alignSuffix({ axis, kind })}`,
      enabled,
      why,
    });
    render(
      <MoveWindow
        layout={LAYOUT}
        position={[0, 0, 0]}
        canDrop={false}
        onPosition={vi.fn()}
        onDrop={vi.fn()}
        onDone={vi.fn()}
        align={{
          canWithin: true,
          toParent: false,
          parentLabel: 'Plate',
          onToParent,
          align: [b(0, 'min'), b(0, 'center'), b(0, 'max'), b(1, 'min'), b(1, 'center'), b(1, 'max'), b(2, 'min', false, 'Turn off Auto-Drop')],
          distribute: [b(0, 'distribute', false, 'Select at least 3 parts or objects'), b(1, 'distribute'), b(2, 'distribute')],
          onRun,
          onHover,
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'min (-X)' }));
    expect(onRun).toHaveBeenCalledWith({ axis: 0, kind: 'min' });
    fireEvent.mouseEnter(screen.getByRole('button', { name: 'center (Y)' }));
    expect(onHover).toHaveBeenLastCalledWith({ axis: 1, kind: 'center' });
    const greyed = screen.getByRole('button', { name: 'distribute (X)' });
    expect(greyed).toHaveAttribute('aria-disabled', 'true');
    expect(greyed).toHaveAttribute('title', 'distribute (X)\nSelect at least 3 parts or objects');
    fireEvent.click(greyed);
    expect(onRun).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByRole('combobox', { name: 'Reference for alignment' }), { target: { value: 'parent' } });
    expect(onToParent).toHaveBeenCalledWith(true);
  });
});
