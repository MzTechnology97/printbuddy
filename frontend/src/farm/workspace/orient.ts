// Fork (printfarm): OrcaSlicer's Auto orientation (libslic3r/Orient.cpp and GUI/Jobs/OrientJob.cpp, v2.5.0), the
// Tweaker method: candidate "up" directions from the mesh's and its convex hull's largest faces plus 18 fixed ones, each
// scored by its overhang, bottom and low-angle areas; the least "unprintable" wins (straight up on a tie), and the
// instance is turned so it points up. With the printer's side fan (fan_direction, auxiliary_fan) it is then turned
// about Z so its cooled overhangs face the fan (find_cooling_direction2). Orca's own settings: "Optimize support
// interface area" on (setMinimalSupportAreaPrams), its default; the rotation is the same for every instance of an
// object (from the object's mesh, its model parts placed).
import * as THREE from 'three';
import { ConvexHull } from 'three/examples/jsm/math/ConvexHull.js';

/** OrientJob.cpp setMinimalSupportAreaPrams: the parameters Orca's GUI orients with (min_volume off). */
const GUI = {
  RELATIVE_F: 20,
  CONTOUR_F: 0.5,
  BOTTOM_F: 2.5,
  BOTTOM_HULL_F: 0.1,
  TAR_C: 0.1,
  TAR_D: 1,
  FIRST_LAY_H: 0.2,
  LAF_MAX: 0.999,
  LAF_MIN: 0.97,
  TAR_LAF: 0.001,
  BOTTOM_MIN: 0.1,
};
/** Orient.hpp OrientParams' own values (min_volume off): Orca's command line (``--orient``) orients with them, an
 * overhang being a face more than 60° from up (ASCENT -0.5). Kept to check this port against Orca's own log. */
export const DEFAULTS = {
  RELATIVE_F: 6.610621027964314,
  CONTOUR_F: 0.23228623269775997,
  BOTTOM_F: 1.167152017941474,
  BOTTOM_HULL_F: 0.1,
  TAR_C: 0.24308070476924726,
  TAR_D: 0.6284515508160871,
  FIRST_LAY_H: 0.2,
  LAF_MAX: 0.999,
  LAF_MIN: 0.9703,
  TAR_LAF: 0.01,
  BOTTOM_MIN: 0.1,
};
type Params = typeof GUI;
const EPSILON = 1e-4; // libslic3r.h

/** A mesh in the object's coordinates: vertices (flat xyz) and triangles (flat vertex indices). */
export interface OrientMeshData {
  positions: ArrayLike<number>;
  indices: ArrayLike<number>;
}

export type V = [number, number, number];
const dot = (a: V, b: V) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const neg = (a: V): V => [-a[0], -a[1], -a[2]];

interface Faces {
  normals: V[];
  areas: number[];
  /** each face's three vertices */
  corners: [V, V, V][];
}

function faces(positions: ArrayLike<number>, indices: ArrayLike<number>): Faces {
  const out: Faces = { normals: [], areas: [], corners: [] };
  const at = (i: number): V => [positions[3 * i], positions[3 * i + 1], positions[3 * i + 2]];
  for (let f = 0; f + 2 < indices.length; f += 3) {
    const a = at(indices[f]), b = at(indices[f + 1]), c = at(indices[f + 2]);
    const u: V = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v: V = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    const n: V = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const len = Math.hypot(...n);
    out.normals.push(len ? [n[0] / len, n[1] / len, n[2] / len] : [0, 0, 0]);
    out.areas.push(len / 2);
    out.corners.push([a, b, c]);
  }
  return out;
}

/** The convex hull's faces (convex_hull_3d), triangles. */
function hullFaces(positions: ArrayLike<number>): Faces {
  const pts: THREE.Vector3[] = [];
  for (let i = 0; i + 2 < positions.length; i += 3) pts.push(new THREE.Vector3(positions[i], positions[i + 1], positions[i + 2]));
  const out: Faces = { normals: [], areas: [], corners: [] };
  if (pts.length < 4) return out;
  for (const face of new ConvexHull().setFromPoints(pts).faces) {
    const e = face.edge;
    const c = [e.head().point, e.next.head().point, e.next.next.head().point].map((p) => [p.x, p.y, p.z] as V);
    out.normals.push([face.normal.x, face.normal.y, face.normal.z]);
    out.areas.push(face.area);
    out.corners.push(c as [V, V, V]);
  }
  return out;
}

const quantize = (n: V): V => n.map((x) => Math.floor(x * 1000) / 1000) as V;

/** area_cumulation_accurate: the normals grouped (quantized to 0.001), the groups with the most area first; each
 * group gives its largest face's own normal. */
function largestDirections(f: Faces, count: number): V[] {
  const groups = new Map<string, { area: number; largest: number; normal: V }>();
  f.normals.forEach((n, i) => {
    const key = quantize(n).join(',');
    const g = groups.get(key) ?? { area: 0, largest: 0, normal: [0, 0, 0] as V };
    g.area += f.areas[i];
    if (f.areas[i] > g.largest) {
      g.largest = f.areas[i];
      g.normal = n;
    }
    groups.set(key, g);
  });
  return [...groups.values()]
    .sort((a, b) => b.area - a.area)
    .slice(0, count)
    .map((g) => g.normal);
}

const S = Math.SQRT1_2;
/** add_supplements */
const SUPPLEMENTS: V[] = [
  [0, 0, -1], [S, 0, -S], [0, S, -S], [-S, 0, -S], [0, -S, -S],
  [1, 0, 0], [S, S, 0], [0, 1, 0], [-S, S, 0], [-1, 0, 0], [-S, -S, 0], [0, -1, 0], [S, -S, 0],
  [S, 0, S], [0, S, S], [-S, 0, S], [0, -S, S], [0, 0, 1],
];

/** remove_duplicates: Eigen's isApprox (relative, 1e-7) against the ones kept; a zero vector goes. */
function withoutDuplicates(dirs: V[]): V[] {
  const tol2 = 1e-14;
  const sq = (a: V) => dot(a, a);
  const out: V[] = [dirs[0]];
  for (const d of dirs.slice(1)) {
    const dup = out.some((k) => sq([k[0] - d[0], k[1] - d[1], k[2] - d[2]]) <= tol2 * Math.min(sq(k), sq(d)));
    if (!dup && sq(d) > 0) out.push(d);
  }
  return out;
}

interface Costs {
  bottom: number;
  bottomHull: number;
  overhang: number;
  contour: number;
  laf: number;
  cooling: number[];
}

/** get_features with min_volume off (the overhang counts its area), ``up`` the direction tried. */
function features(mesh: Faces, hull: Faces, up: V, ascent: number, fan: boolean, P: Params): Costs {
  const zMax = new Float64Array(mesh.corners.length);
  let minZ = Infinity;
  mesh.corners.forEach(([a, b, c], i) => {
    const za = dot(a, up), zb = dot(b, up), zc = dot(c, up);
    zMax[i] = Math.max(za, zb, zc);
    minZ = Math.min(minZ, za, zb, zc);
  });
  const below = (z: number, h: number) => z < minZ + h - EPSILON;
  let bottom1 = 0, bottom2 = 0, overhang = 0, laf = 0, bottomHull = 0;
  const cooling: number[] = [];
  mesh.normals.forEach((n, i) => {
    const a = mesh.areas[i];
    const second = below(zMax[i], P.FIRST_LAY_H / 2);
    if (below(zMax[i], P.FIRST_LAY_H)) bottom1 += a;
    if (second) bottom2 += a;
    const proj = dot(n, up);
    if (proj < ascent && !second) overhang += a; // no appearance faces here (is_apperance 0)
    const abs = Math.abs(proj);
    if (abs < P.LAF_MAX && abs > P.LAF_MIN && zMax[i] > minZ + P.FIRST_LAY_H) laf += a;
    // the overhangs a side fan cools (between about 50° and 76° down), weighted
    if (fan) cooling.push(proj < -0.6427 && proj > -0.97 && !second ? a * (1 - proj) : 0);
  });
  hull.corners.forEach(([a, b, c], i) => {
    if (below(Math.max(dot(a, up), dot(b, up), dot(c, up)), P.FIRST_LAY_H)) bottomHull += hull.areas[i];
  });
  const bottom = bottom1 * 0.5 + bottom2;
  return { bottom, bottomHull, overhang, contour: 4 * Math.sqrt(bottom), laf, cooling };
}

/** target_function, min_volume off (area_projected is never measured: 0). */
function unprintability(c: Costs, P: Params): number {
  const cost =
    (P.RELATIVE_F * (c.overhang * P.TAR_C + P.TAR_D + P.TAR_LAF * c.laf)) /
    (P.TAR_D + P.CONTOUR_F * c.contour + P.BOTTOM_F * c.bottom + P.BOTTOM_HULL_F * c.bottomHull);
  return cost + (c.bottom < P.BOTTOM_MIN ? 100 : 0);
}

/** Geometry::rotation_from_two_vectors: the shortest rotation taking ``from`` to ``to``. */
function rotationBetween(from: V, to: V): THREE.Matrix4 {
  const a = new THREE.Vector3(...from).normalize(), b = new THREE.Vector3(...to).normalize();
  return new THREE.Matrix4().makeRotationFromQuaternion(new THREE.Quaternion().setFromUnitVectors(a, b));
}

export interface OrientResult {
  /** the direction that goes up, in the object's coordinates */
  up: V;
  /** the rotation put before the instance's own (instance->rotate(rotation_matrix), then the fan's) */
  rotation: THREE.Matrix4;
}

/** Each direction AutoOrienter::process tries, as "up", with its costs (Orca logs them), least unprintable first:
 * ``ascent`` the cosine past which a face overhangs (cos(180° − the support threshold angle)). */
export function orientations(mesh: OrientMeshData, ascent: number, params: Params = GUI, fan = false) {
  const m = faces(mesh.positions, mesh.indices);
  const hull = hullFaces(mesh.positions);
  const candidates = withoutDuplicates([[0, 0, -1], ...largestDirections(m, 10), ...largestDirections(hull, 14), ...SUPPLEMENTS]);
  const results = candidates.map((c) => {
    const up = neg(c);
    const costs = features(m, hull, up, ascent, fan, params);
    return { up, costs, cost: unprintability(costs, params) };
  });
  return { faces: m, results: results.sort((a, b) => a.cost - b.cost) };
}

/** AutoOrienter::process, then (with a side fan) find_cooling_direction2: ``overhangAngle`` is the support threshold
 * angle (degrees), ``fan`` the side the printer's auxiliary fan blows from (+1 right, -1 left or both, 0 none). */
export function autoOrient(mesh: OrientMeshData, overhangAngle: number, fan: -1 | 0 | 1 = 0): OrientResult {
  const ascent = Math.cos(Math.PI - (overhangAngle * Math.PI) / 180);
  const { faces: m, results } = orientations(mesh, ascent, GUI, fan !== 0);
  // a tie with straight up keeps it straight up (no flipping)
  let best = results[0];
  const z: V = [0, 0, 1];
  for (let i = 1; i < results.length - 1; i++) {
    if (Math.abs(results[i].cost - results[0].cost) < EPSILON && Math.abs(dot(results[0].up, z) - 1) > EPSILON) {
      if (Math.abs(dot(results[i].up, z) - 1) < EPSILON * EPSILON) {
        best = { ...results[i], up: z };
        break;
      }
    } else break;
  }
  let rotation = rotationBetween(best.up, z);
  if (fan !== 0) rotation = coolingTurn(m, best.costs.cooling, rotation, fan).multiply(rotation);
  return { up: best.up, rotation };
}

/** find_cooling_direction2: of 180 directions about Z, the one the oriented mesh's cooled overhangs face most; the turn
 * taking it to the fan's side (none when they face every side alike, within 1 mm²). */
function coolingTurn(m: Faces, weights: number[], rotation: THREE.Matrix4, fan: -1 | 1): THREE.Matrix4 {
  const normal = new THREE.Matrix3().setFromMatrix4(rotation);
  const normals = m.normals.map((n) => new THREE.Vector3(...n).applyMatrix3(normal).normalize());
  const machine: V = [fan, 0, 0];
  let best: V = [0, 0, 0], max = 0, origin = 0;
  for (let i = 0; i < 180; i++) {
    const a = (i * 2 * Math.PI) / 180;
    const dir: V = [Math.cos(a), Math.sin(a), 0];
    let sum = 0;
    normals.forEach((n, k) => {
      const p = n.x * dir[0] + n.y * dir[1];
      if (weights[k] > 0 && p > 0) sum += weights[k] * p;
    });
    if (sum > max) {
      max = sum;
      best = dir;
    }
    if (dot(dir, machine) > 0.999) origin = sum;
  }
  if (Math.abs(origin - max) < 1 || dot(best, best) === 0) return new THREE.Matrix4();
  return rotationBetween(best, machine);
}
