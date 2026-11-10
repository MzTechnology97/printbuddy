/**
 * Printfarm (fork): the 3MF Workspace's Rotate and Scale, and Orca's rules after any transform (batch E2b): the pivot
 * (bounding sphere / box centre), World and Object coordinates, the object's other instances in step, the bed, the
 * Rotate window's absolute angles, the gizmos' drag math and the two windows.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import * as THREE from 'three';
import JSZip from 'jszip';
import { render } from '../utils';
import { buildModelGroup, parse3MF, type ViewerScene } from '../../components/ModelViewer';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';
import { orcaText } from '../../farm/workspace/orcaLayout';
import { type Placed, moved } from '../../farm/workspace/placement';
import {
  type Change,
  about,
  absoluteRotation,
  boundingSphere,
  boxOf,
  changed,
  minSphere,
  rotationAbout,
  rotationSync,
  withoutRotation,
} from '../../farm/workspace/transforms';
import { ringAngle, scaleRatio } from '../../farm/workspace/transformGizmos';
import { RotateWindow, ScaleWindow } from '../../farm/workspace/Viewport';

const LAYOUT = { tag: 'v', source: 's', pages: { process: [], filament: [], printer: [] }, options: {}, object_keys: [], plate_keys: {}, it: {} } as unknown as OrcaLayout;

/** A 10 x 10 x 20 mm box (object 1), one build item per transform. */
async function scene(...transforms: string[]): Promise<ViewerScene> {
  const zip = new JSZip();
  const v = [0, 10].flatMap((x) => [0, 10].flatMap((y) => [0, 20].map((z) => `<vertex x="${x}" y="${y}" z="${z}"/>`))).join('');
  const faces = [[0, 1, 2], [1, 3, 2], [4, 6, 5], [5, 6, 7], [0, 4, 1], [1, 4, 5], [2, 3, 6], [3, 7, 6], [0, 2, 4], [2, 6, 4], [1, 5, 3], [3, 5, 7]];
  const t = faces.map(([a, b, c]) => `<triangle v1="${a}" v2="${b}" v3="${c}"/>`).join('');
  zip.file(
    '3D/3dmodel.model',
    `<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>` +
      `<object id="1" type="model"><mesh><vertices>${v}</vertices><triangles>${t}</triangles></mesh></object></resources>` +
      `<build>${transforms.map((tr) => `<item objectid="1" transform="${tr}"/>`).join('')}</build></model>`,
  );
  const model = buildModelGroup(await parse3MF(await zip.generateAsync({ type: 'arraybuffer' })), null, [], true);
  model.scale.z = -1;
  model.position.z = 256;
  model.updateMatrixWorld(true);
  return { model } as unknown as ViewerScene;
}
const AT = (x: number, y: number, z = 0) => `1 0 0 0 1 0 0 0 1 ${x} ${y} ${z}`;
const round = (b: THREE.Box3 | null) => (b ? [...b.min.toArray(), ...b.max.toArray()].map((n) => Math.round(n * 1000) / 1000 + 0) : null);
const always = () => true;

describe('the rotation pivot and angles (Selection::get_bounding_sphere, get_rotation_by_quaternion)', () => {
  it("is the selection's minimum bounding sphere", async () => {
    const s = minSphere([0, 10].flatMap((x) => [0, 10].flatMap((y) => [0, 20].map((z) => new THREE.Vector3(x, y, z)))));
    expect(s.center.toArray().map((n) => Math.round(n * 1e6) / 1e6)).toEqual([5, 5, 10]);
    expect(s.radius).toBeCloseTo(Math.sqrt(25 + 25 + 100));
    const view = await scene(AT(100, 110));
    expect(boundingSphere(view, {}, ['1:0'])!.center.toArray().map((n) => Math.round(n * 1e6) / 1e6)).toEqual([105, 115, 10]);
  });

  it("shows Eigen's eulerAngles(2, 1, 0), X and Z swapped, as Orca's Absolute row", () => {
    const spec = (m: THREE.Matrix4) => about([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], m, new THREE.Vector3());
    const r = (v: number[]) => v.map((n) => Math.round(n * 1000) / 1000 + 0);
    expect(r(absoluteRotation(spec(new THREE.Matrix4())))).toEqual([0, 0, 0]);
    expect(r(absoluteRotation(spec(rotationAbout(2, 90))))).toEqual([0, 0, 90]);
    expect(r(absoluteRotation(spec(rotationAbout(0, 30))))).toEqual([30, 0, 0]);
    // Eigen keeps Z in [0°, 180°]; ±180 on Y as the sign of a 0 in the matrix has it (atan2(-0, -1)), in Orca too
    expect(r(absoluteRotation(spec(rotationAbout(2, -90)))).map(Math.abs)).toEqual([180, 180, 90]);
    expect(r(absoluteRotation(spec(rotationAbout(0, 90).premultiply(rotationAbout(2, 90)))))).toEqual([90, 0, 90]);
  });

  it('a contextual msgid without a translation shows its text alone', () => {
    expect(orcaText(LAYOUT, 'Noun\u0004Scale', 'en')).toBe('Scale');
    expect(orcaText({ ...LAYOUT, it: { 'Noun\u0004Scale': 'Ridimensiona' } }, 'Noun\u0004Scale', 'it')).toBe('Ridimensiona');
  });
});

describe('a transform as Orca leaves it (do_rotate / do_scale / do_move with auto_drop)', () => {
  const turn = (axis: 0 | 1 | 2, degrees: number, view: ViewerScene, placed: Placed = {}, keys = ['1:0']): Change => {
    const pivot = boundingSphere(view, placed, keys)!.center;
    return { keys, apply: (s) => about(s, rotationAbout(axis, degrees), pivot), sync: 'general', drop: 'transform' };
  };

  it('a box turned onto its side lies on the bed, about its sphere centre', async () => {
    const view = await scene(AT(100, 110));
    const placed = changed(view, {}, turn(0, 90, view), always);
    expect(round(boxOf(view, placed, ['1:0']))).toEqual([100, 105, 0, 110, 125, 10]); // 20 mm along Y now, on the bed
  });

  it('turning one instance about X turns the others the same (in their own place, at its height); about Z, not', async () => {
    const view = await scene(AT(100, 110), AT(150, 110));
    const tilted = changed(view, {}, turn(0, 90, view), always);
    // synchronize_unselected_instances copies the turn, not the pivot: the other one turns about its own origin
    expect(round(boxOf(view, tilted, ['1:1']))).toEqual([150, 90, 0, 160, 110, 10]);
    const pivot = boundingSphere(view, {}, ['1:0'])!.center;
    const spun = changed(view, {}, { keys: ['1:0'], apply: (s) => about(s, rotationAbout(2, 90), pivot), sync: 'none', drop: 'transform' }, always);
    expect(spun['1:1']).toBeUndefined(); // as the file has it
  });

  it('scales about the box centre and puts it back on the bed; Object coordinates scale on its own axes', async () => {
    const view = await scene(AT(100, 110));
    const c = boxOf(view, {}, ['1:0'])!.getCenter(new THREE.Vector3());
    const half = new THREE.Matrix4().makeScale(0.5, 0.5, 0.5);
    const placed = changed(view, {}, { keys: ['1:0'], apply: (s) => about(s, half, c), sync: 'general', drop: 'transform' }, always);
    expect(round(boxOf(view, placed, ['1:0']))).toEqual([102.5, 112.5, 0, 107.5, 117.5, 10]);
    // turned 90° about Z, then twice as long along its own X: on the bed's Y
    const turned = changed(view, {}, turn(2, 90, view), always);
    const c2 = boxOf(view, turned, ['1:0'])!.getCenter(new THREE.Vector3());
    const long = new THREE.Matrix4().makeScale(2, 1, 1);
    const object = changed(view, turned, { keys: ['1:0'], apply: (s) => about(s, long, c2, 'object'), sync: 'general', drop: 'transform' }, always);
    const size = boxOf(view, object, ['1:0'])!.getSize(new THREE.Vector3()).toArray().map((n) => Math.round(n * 1000) / 1000);
    expect(size).toEqual([10, 20, 20]);
  });

  it('what floats after a move drops back; what was sinking stays so; an object without auto_drop stays put', async () => {
    const view = await scene(AT(100, 110));
    const up: Change = { keys: ['1:0'], apply: (s) => moved(s, [0, 0, 5]), sync: 'none', drop: 'move' };
    expect(changed(view, {}, up, always)).toEqual({}); // back on the bed: no placement
    expect(changed(view, {}, up, () => false)['1:0'][11]).toBe(5);
    const sunk = changed(view, {}, { keys: ['1:0'], apply: (s) => moved(s, [0, 0, -5]), sync: 'none', drop: 'move' }, always);
    expect(sunk['1:0'][11]).toBe(-5); // sinking is allowed
    const stillSunk = changed(view, sunk, turn(2, 45, view, sunk), always);
    expect(boxOf(view, stillSunk, ['1:0'])!.min.z).toBeCloseTo(-5); // a turn leaves a sinking object sinking
    expect(changed(view, sunk, { keys: ['1:0'], apply: withoutRotation, sync: 'general', drop: 'transform' }, always)['1:0'][11]).toBe(-5);
  });

  it("the other instances follow a tilt in Object coordinates, not a spin about the object's own up axis", () => {
    const flat = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0];
    expect(rotationSync('world', 2, flat, flat)).toBe('none');
    expect(rotationSync('world', 0, flat, flat)).toBe('general');
    expect(rotationSync('object', 2, flat, about(flat, rotationAbout(2, 30), new THREE.Vector3(), 'object'))).toBe('none');
    expect(rotationSync('object', 0, flat, about(flat, rotationAbout(0, 30), new THREE.Vector3(), 'object'))).toBe('general');
  });
});

describe("the gizmos' drag (GLGizmoRotate::on_dragging, GLGizmoScale3D::calc_ratio)", () => {
  const frame = { center: new THREE.Vector3(0, 0, 0), radius: 25 }; // the ring: 30 mm
  const at = (deg: number, r: number) => new THREE.Vector3(Math.cos(THREE.MathUtils.degToRad(deg)) * r, Math.sin(THREE.MathUtils.degToRad(deg)) * r, 0);
  const deg = (rad: number) => THREE.MathUtils.radToDeg(rad);

  it('takes the angle in the ring plane, in 45° steps near the centre and 5° steps on the scale', () => {
    expect(deg(ringAngle(frame, 2, at(37, 50)))).toBeCloseTo(37);
    expect(deg(ringAngle(frame, 2, at(37, 15)))).toBeCloseTo(45); // 10 to 20 mm from the centre: 45° steps
    expect(deg(ringAngle(frame, 2, at(37, 31)))).toBeCloseTo(35); // on the scale (30 to 33 mm): 5° steps
    expect(deg(ringAngle(frame, 2, at(359.99, 50)))).toBeCloseTo(359.99);
  });

  it('scales by how far the grab moved from the bottom centre, in 5% steps with Shift', () => {
    const drag = new THREE.Vector3(10, 0, 0), pivot = new THREE.Vector3(0, 0, 0), up = new THREE.Vector3(0, 0, 1);
    const down = (x: number) => new THREE.Ray(new THREE.Vector3(x, 0, 50), new THREE.Vector3(0, 0, -1));
    expect(scaleRatio(down(15), drag, pivot, up, false, false)).toBeCloseTo(1.5);
    expect(scaleRatio(down(15.2), drag, pivot, up, false, true)).toBeCloseTo(1.5);
  });
});

describe("Orca's Rotate and Scale windows", () => {
  it('Rotate: World, a relative turn from 0, the absolute angles, the two resets', () => {
    const on = { relative: vi.fn(), absolute: vi.fn(), resetRelative: vi.fn(), resetAbsolute: vi.fn() };
    render(
      <RotateWindow
        layout={LAYOUT}
        absolute={[0, 0, 90]}
        changed
        turned
        onRelative={on.relative}
        onAbsolute={on.absolute}
        onResetRelative={on.resetRelative}
        onResetAbsolute={on.resetAbsolute}
        onDone={vi.fn()}
      />,
    );
    const z = screen.getByRole('textbox', { name: 'Relative Z' });
    fireEvent.change(z, { target: { value: '15' } });
    fireEvent.keyDown(z, { key: 'Enter' });
    expect(on.relative).toHaveBeenCalledWith(2, 15);
    expect(z).toHaveValue('0.00'); // back at 0 after the turn
    expect(screen.getByRole('textbox', { name: 'Absolute Z' })).toHaveValue('90.00');
    fireEvent.click(screen.getByRole('button', { name: 'Reset current rotation to real zeros.' }));
    expect(on.resetAbsolute).toHaveBeenCalled();
  });

  it('Scale: the scale and size fields, Uniform scale, the reset only when scaled', () => {
    const on = { scale: vi.fn(), size: vi.fn(), uniform: vi.fn(), reset: vi.fn() };
    const props = {
      layout: LAYOUT, cs: 'world' as const, onCs: vi.fn(), objectCs: true, size: [10, 10, 20] as [number, number, number], uniform: true,
      onUniform: on.uniform, onScale: on.scale, onSize: on.size, onReset: on.reset, onDone: vi.fn(),
    };
    const { rerender } = render(<ScaleWindow {...props} scale={[100, 100, 100]} />);
    expect(screen.getByRole('button', { name: 'Reset scale' })).toHaveClass('invisible');
    const sx = screen.getByRole('textbox', { name: 'Scale X' });
    fireEvent.change(sx, { target: { value: '50' } });
    fireEvent.keyDown(sx, { key: 'Enter' });
    expect(on.scale).toHaveBeenCalledWith(0, 50);
    const size = screen.getByRole('textbox', { name: 'Size Z' });
    fireEvent.change(size, { target: { value: '0' } });
    fireEvent.keyDown(size, { key: 'Enter' });
    expect(on.size).not.toHaveBeenCalled(); // a size must be above 0
    fireEvent.click(screen.getByRole('checkbox'));
    expect(on.uniform).toHaveBeenCalledWith(false);
    rerender(<ScaleWindow {...props} scale={[50, 50, 50]} />);
    expect(screen.getByRole('button', { name: 'Reset scale' })).not.toHaveClass('invisible');
  });
});
