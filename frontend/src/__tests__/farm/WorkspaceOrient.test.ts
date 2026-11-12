/**
 * Printfarm (fork): 3MF Workspace, batch E5. OrcaSlicer's Auto orientation (Orient.cpp) ported: each direction's
 * costs as Orca's own log prints them (orca-slicer --orient 1, its default parameters), and the turn Orca's GUI
 * parameters give. Made-up meshes: a T standing on its stem, a cylinder.
 */
import { describe, it, expect } from 'vitest';
import * as THREE from 'three';
import { DEFAULTS, autoOrient, orientations } from '../../farm/workspace/orient';

// a 15 x 15 x 20 stem under a 44 x 15 x 4 bar
const T = {
  positions: [
    -7.5, -7.5, 0, 7.5, -7.5, 0, -7.5, 7.5, 0, 7.5, 7.5, 0, -7.5, -7.5, 20, 7.5, -7.5, 20, -7.5, 7.5, 20, 7.5, 7.5, 20,
    -22, -7.5, 20, 22, -7.5, 20, -22, 7.5, 20, 22, 7.5, 20, -22, -7.5, 24, 22, -7.5, 24, -22, 7.5, 24, 22, 7.5, 24,
  ],
  indices: [
    0, 2, 3, 0, 3, 1, 4, 5, 7, 4, 7, 6, 0, 1, 5, 0, 5, 4, 2, 6, 7, 2, 7, 3, 0, 4, 6, 0, 6, 2, 1, 3, 7, 1, 7, 5,
    8, 10, 11, 8, 11, 9, 12, 13, 15, 12, 15, 14, 8, 9, 13, 8, 13, 12, 10, 14, 15, 10, 15, 11, 8, 12, 14, 8, 14, 10,
    9, 11, 15, 9, 15, 13,
  ],
};
// a cylinder of radius 10 and height 15, 24 sides
const CYLINDER = (() => {
  const positions = [0, 0, 0, 0, 0, 15];
  for (let k = 0; k < 24; k++) {
    const a = (k * Math.PI) / 12;
    positions.push(10 * Math.cos(a), 10 * Math.sin(a), 0, 10 * Math.cos(a), 10 * Math.sin(a), 15);
  }
  const indices: number[] = [];
  for (let k = 0; k < 24; k++) {
    const a = 2 + 2 * k, b = 2 + 2 * ((k + 1) % 24);
    indices.push(0, b, a, 1, a + 1, b + 1, a, b, b + 1, a, b + 1, a + 1);
  }
  return { positions, indices };
})();

/** Orca's log line for a direction: overhang, bottom, bottom hull, contour, low-angle area, unprintability. */
const row = (mesh: typeof T, up: [number, number, number]) => {
  const r = orientations(mesh, -0.5, DEFAULTS).results.find((x) => x.up.every((v, i) => Math.abs(v - up[i]) < 1e-4))!;
  const c = r.costs;
  return [c.overhang, c.bottom, c.bottomHull, c.contour, c.laf, r.cost].map((v) => Math.round(v * 10) / 10);
};

describe("Orca's costs, as its own log has them (default parameters)", () => {
  it('the T: standing, upside down, on its sides', () => {
    expect(row(T, [0, 0, 1])).toEqual([660, 337.5, 225, 73.5, 0, 2.5]);
    expect(row(T, [0, 0, -1])).toEqual([225, 990, 660, 125.9, 0, 0.3]);
    expect(row(T, [0, -1, 0])).toEqual([0, 714, 766, 106.9, 0, 0]);
    expect(row(T, [-1, 0, 0])).toEqual([300, 90, 60, 37.9, 0, 4]);
    expect(row(T, [-Math.SQRT1_2, 0, Math.SQRT1_2])).toEqual([1245, 0, 0, 0, 0, 3290]);
    expect(orientations(T, -0.5, DEFAULTS).results).toHaveLength(20); // as many directions as Orca tries
  });

  it('the cylinder: on its base it is best', () => {
    const { results } = orientations(CYLINDER, -0.5, DEFAULTS);
    expect(results[0].cost).toBeCloseTo(0, 1); // Orca's log: 0.0
    expect(row(CYLINDER, [0, 0, 1])).toEqual([0, 465.9, 310.6, 86.3, 0, 0]);
    expect(row(CYLINDER, [-1, 0, 0])).toEqual([313.3, 0, 0, 0, 156.6, 924.1]);
  });
});

describe('Auto orientation (the GUI: "Optimize support interface area")', () => {
  it('lays the T on its side, turned about X, and leaves the cylinder on its base', () => {
    const { up, rotation } = autoOrient(T, 30);
    expect(Math.abs(up[1])).toBeCloseTo(1); // on a side face: no overhang left
    expect(new THREE.Vector3(...up).applyMatrix4(rotation).z).toBeCloseTo(1);
    expect(autoOrient(CYLINDER, 30).up.map((v) => v + 0)).toEqual([0, 0, 1]);
    expect(autoOrient(CYLINDER, 30).rotation.equals(new THREE.Matrix4())).toBe(true);
  });

  it('a side fan turns nothing when the cooled overhangs face every side alike', () => {
    expect(autoOrient(CYLINDER, 30, 1).rotation.equals(new THREE.Matrix4())).toBe(true);
    expect(autoOrient(CYLINDER, 30, -1).rotation.equals(new THREE.Matrix4())).toBe(true);
  });
});
