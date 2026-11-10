/**
 * Printfarm (fork): the 3MF Workspace's parts placed anew (batch E2c). A part is its object's component (Bambu Studio
 * keeps every part of an object in one model file): each shows its own mesh, a part placed anew moves in every
 * instance (they share it, as Orca's ModelVolume), its change keeps the object on the bed, and the copy gets the
 * part's own transform. Also the pivot on a big mesh, on plain arrays.
 */
import { describe, it, expect } from 'vitest';
import * as THREE from 'three';
import JSZip from 'jszip';
import { buildModelGroup, parse3MF, type ViewerScene } from '../../components/ModelViewer';
import { applyPlacements, moved, placementsToSave } from '../../farm/workspace/placement';
import { boxOf, changedPart, minSphereOf, partWorld, targetKey } from '../../farm/workspace/transforms';

const cube = (size: number) => {
  const v = [0, size].flatMap((x) => [0, size].flatMap((y) => [0, size].map((z) => `<vertex x="${x}" y="${y}" z="${z}"/>`))).join('');
  const faces = [[0, 1, 2], [1, 3, 2], [4, 6, 5], [5, 6, 7], [0, 4, 1], [1, 4, 5], [2, 3, 6], [3, 7, 6], [0, 2, 4], [2, 6, 4], [1, 5, 3], [3, 5, 7]];
  return `<mesh><vertices>${v}</vertices><triangles>${faces.map(([a, b, c]) => `<triangle v1="${a}" v2="${b}" v3="${c}"/>`).join('')}</triangles></mesh>`;
};

/** Object 2: part 1 (a 10 mm cube) and part 3 (a 4 mm one, 20 mm along X), both in /3D/Objects/object_1.model; two
 * instances, the second turned 90° about Z. */
async function scene(): Promise<ViewerScene> {
  const zip = new JSZip();
  zip.file(
    '3D/3dmodel.model',
    `<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"><resources>` +
      `<object id="2" type="model"><components>` +
      `<component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>` +
      `<component p:path="/3D/Objects/object_1.model" objectid="3" transform="1 0 0 0 1 0 0 0 1 20 0 0"/>` +
      `</components></object></resources><build>` +
      `<item objectid="2" transform="1 0 0 0 1 0 0 0 1 100 100 0"/><item objectid="2" transform="0 1 0 -1 0 0 0 0 1 200 100 0"/>` +
      `</build></model>`,
  );
  zip.file(
    '3D/Objects/object_1.model',
    `<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>` +
      `<object id="1" type="model">${cube(10)}</object><object id="3" type="model">${cube(4)}</object></resources></model>`,
  );
  const model = buildModelGroup(await parse3MF(await zip.generateAsync({ type: 'arraybuffer' })), null, [], true);
  model.scale.z = -1;
  model.position.z = 256;
  model.updateMatrixWorld(true);
  return { model } as unknown as ViewerScene;
}
const round = (b: THREE.Box3 | null) => (b ? [...b.min.toArray(), ...b.max.toArray()].map((n) => Math.round(n * 1000) / 1000 + 0) : null);
const always = () => true;
const small = targetKey('2', 0, '3');

describe('parts of a Bambu Studio object', () => {
  it('each part shows its own mesh from the shared model file, with its component transform', async () => {
    const view = await scene();
    expect(round(boxOf(view, {}, [targetKey('2', 0, '1')]))).toEqual([100, 100, 0, 110, 110, 10]);
    expect(round(boxOf(view, {}, [small]))).toEqual([120, 100, 0, 124, 104, 4]);
    const parts = view.model.children[0].children.map((g) => [g.userData.partId, g.userData.transform?.[9]]);
    expect(parts).toEqual([['1', 0], ['3', 20]]);
  });

  it("a part moved in one instance moves in every instance, on that instance's axes; the other part stays", async () => {
    const view = await scene();
    // 10 mm along the bed's X as instance 0 shows it
    const placed = changedPart(view, {}, small, (w) => moved(w, [10, 0, 0]), 'move', always);
    expect(placed).toEqual({ '2/3': [1, 0, 0, 0, 1, 0, 0, 0, 1, 30, 0, 0] });
    applyPlacements(view, placed);
    expect(round(boxOf(view, placed, [small]))).toEqual([130, 100, 0, 134, 104, 4]);
    expect(round(boxOf(view, placed, [targetKey('2', 1, '3')]))).toEqual([196, 130, 0, 200, 134, 4]); // turned instance: along its Y
    expect(round(boxOf(view, placed, [targetKey('2', 0, '1')]))).toEqual([100, 100, 0, 110, 110, 10]);
    expect(placementsToSave(placed)).toEqual({ transforms: [], parts: [{ object_id: '2', part_id: '3', matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1, 30, 0, 0] }] });
    // and the 3D view shows it there
    const shown = new THREE.Box3().setFromObject(view.model.children[0].children[1]);
    expect(Math.round(shown.min.x)).toBe(130);
  });

  it('the object stays on the bed: a part raised alone lifts nothing; the only part on the bed raised drops back', async () => {
    const view = await scene();
    const up = (from: Record<string, number[]>, target: string) => changedPart(view, from, target, (w) => moved(w, [0, 0, 5]), 'move', always);
    const one = up({}, small);
    expect(round(boxOf(view, one, [small]))?.[2]).toBe(5); // the big part still holds the object on the bed
    const both = up(one, targetKey('2', 0, '1'));
    expect(round(boxOf(view, both, ['2:0']))?.[2]).toBe(0); // nothing on the bed any more: the object drops 5 mm
    expect(both['2:0'][11]).toBe(-5);
    expect(both['2:1'][11]).toBe(-5); // the other instance, the same
  });

  it('gives the part as the instance shows it (M·C) and its own (C)', async () => {
    const view = await scene();
    const p = partWorld(view, {}, targetKey('2', 1, '3'))!;
    expect(p.own).toEqual([1, 0, 0, 0, 1, 0, 0, 0, 1, 20, 0, 0]);
    expect(p.world.slice(9)).toEqual([200, 120, 0]);
  });
});

describe('the rotation pivot on a big mesh (the PR #151 review)', () => {
  it('runs on a flat array: 300 000 points well under a second, the same sphere', () => {
    const n = 300_000;
    const p = new Float32Array(n * 3);
    let s = 7;
    for (let i = 0; i < p.length; i++) {
      s = (s * 16807) % 2147483647;
      p[i] = (s / 2147483647) * 100;
    }
    p.set([0, 0, 0, 100, 100, 100], 0); // the corners make the sphere
    const t = performance.now();
    const sphere = minSphereOf(p);
    expect(performance.now() - t).toBeLessThan(1000);
    expect(sphere.center.toArray().map((v) => Math.round(v))).toEqual([50, 50, 50]);
    expect(sphere.radius).toBeCloseTo(Math.sqrt(3) * 50, 1);
  });
});
