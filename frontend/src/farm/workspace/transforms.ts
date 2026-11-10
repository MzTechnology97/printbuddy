// Fork (printfarm): OrcaSlicer's instance transforms (v2.5.0) on top of placement.ts: a move, rotation or scale of
// the instances selected, in World or Object coordinates (Selection::translate / rotate / scale_and_translate), the
// object's other instances kept in step (synchronize_unselected_instances), then each one put back on the bed as
// GLCanvas3D::do_move / do_rotate / do_scale do with the object's auto_drop. Matrices act on column vectors in the
// slicer's coordinates (mm), as placement.fromSpec reads 3MF's numbers.
import * as THREE from 'three';
import type { ViewerScene } from '../../components/ModelViewer';
import { type Placed, type Spec, fileSpec, fromSpec, itemKey, toSpec } from './placement';

export type Coordinates = 'world' | 'object';
type Vec = [number, number, number];

/** Model.hpp SINKING_Z_THRESHOLD */
const SINKING = -0.001;

const items = (view: ViewerScene) => view.model.children.filter((c) => c.userData.objectId != null);
const keyOf = (item: THREE.Object3D) => itemKey(String(item.userData.objectId), Number(item.userData.instance ?? 0));

/** The item's vertices where the file puts them, in the slicer's coordinates (kept on the item: the mesh doesn't move). */
function filePoints(item: THREE.Object3D): Float32Array {
  if (item.userData.points) return item.userData.points as Float32Array;
  const out: number[] = [];
  const toItem = new THREE.Matrix4();
  const v = new THREE.Vector3();
  item.updateMatrixWorld(true);
  const fromItem = item.matrixWorld.clone().invert();
  item.traverse((o) => {
    if (!(o instanceof THREE.Mesh)) return;
    const pos = (o.geometry as THREE.BufferGeometry).getAttribute('position');
    toItem.copy(fromItem).multiply(o.matrixWorld);
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i).applyMatrix4(toItem);
      out.push(v.x, v.z, v.y); // the viewer's Y-up frame back to the slicer's
    }
  });
  const points = new Float32Array(out);
  item.userData.points = points;
  return points;
}

/** Visit the item's vertices under ``spec`` (slicer coordinates). */
function eachPoint(item: THREE.Object3D, spec: Spec, visit: (x: number, y: number, z: number) => void) {
  const m = fromSpec(spec).multiply(fromSpec(fileSpec(item)).invert()).elements;
  const p = filePoints(item);
  for (let i = 0; i < p.length; i += 3) {
    const x = p[i], y = p[i + 1], z = p[i + 2];
    visit(m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]);
  }
}

function minZ(item: THREE.Object3D, spec: Spec): number {
  let low = Infinity;
  eachPoint(item, spec, (_x, _y, z) => (low = Math.min(low, z)));
  return low;
}

/** The instances' box in the slicer's axes, or in the first one's own (rotated) axes for Object coordinates. */
export function boxOf(view: ViewerScene, placed: Placed, keys: string[], cs: Coordinates = 'world'): THREE.Box3 | null {
  const box = new THREE.Box3();
  const v = new THREE.Vector3();
  const first = items(view).find((i) => keys.includes(keyOf(i)));
  const into = cs === 'object' && first ? rotationOf(placed[keyOf(first)] ?? fileSpec(first)).invert() : null;
  for (const item of items(view)) {
    if (!keys.includes(keyOf(item))) continue;
    eachPoint(item, placed[keyOf(item)] ?? fileSpec(item), (x, y, z) => {
      v.set(x, y, z);
      if (into) v.applyMatrix4(into);
      box.expandByPoint(v);
    });
  }
  return box.isEmpty() ? null : box;
}

type Sphere = { center: THREE.Vector3; radius: number };

/** The selection's bounding sphere: Selection::get_bounding_sphere's (CGAL's minimum sphere), the rotation's pivot. */
export function boundingSphere(view: ViewerScene, placed: Placed, keys: string[]): Sphere | null {
  const pts: THREE.Vector3[] = [];
  for (const item of items(view))
    if (keys.includes(keyOf(item))) eachPoint(item, placed[keyOf(item)] ?? fileSpec(item), (x, y, z) => pts.push(new THREE.Vector3(x, y, z)));
  return pts.length ? minSphere(pts) : null;
}

const EPS = 1e-7;
const inside = (s: Sphere, p: THREE.Vector3) => p.distanceTo(s.center) <= s.radius * (1 + 1e-9) + EPS;

function sphereOf(ps: THREE.Vector3[]): Sphere {
  if (ps.length === 1) return { center: ps[0].clone(), radius: 0 };
  if (ps.length === 2) {
    const c = ps[0].clone().add(ps[1]).multiplyScalar(0.5);
    return { center: c, radius: c.distanceTo(ps[0]) };
  }
  const [a, b, c] = ps;
  const ab = b.clone().sub(a), ac = c.clone().sub(a);
  if (ps.length === 3) {
    const n = ab.clone().cross(ac);
    const d = 2 * n.lengthSq();
    if (d < EPS) return widest(ps);
    const center = ac.clone().cross(n).multiplyScalar(ab.lengthSq()).add(n.clone().cross(ab).multiplyScalar(ac.lengthSq())).divideScalar(d).add(a);
    return { center, radius: center.distanceTo(a) };
  }
  // four points: the centre x solves 2(p - a)·(x - a) = |p - a|² for b, c, d
  const ad = ps[3].clone().sub(a);
  const m = new THREE.Matrix3().set(ab.x, ab.y, ab.z, ac.x, ac.y, ac.z, ad.x, ad.y, ad.z);
  if (Math.abs(m.determinant()) < EPS) return widest(ps);
  const center = new THREE.Vector3(ab.lengthSq(), ac.lengthSq(), ad.lengthSq()).multiplyScalar(0.5).applyMatrix3(m.invert()).add(a);
  return { center, radius: center.distanceTo(a) };
}

/** Boundary points in a line or a plane: the sphere of their farthest pair. */
function widest(ps: THREE.Vector3[]): Sphere {
  let best = sphereOf([ps[0], ps[1]]);
  for (let i = 0; i < ps.length; i++)
    for (let j = i + 1; j < ps.length; j++) {
      const s = sphereOf([ps[i], ps[j]]);
      if (s.radius > best.radius) best = s;
    }
  return best;
}

/** Welzl's minimum enclosing sphere, iteratively (no recursion depth), on the points in a fixed shuffled order. */
export function minSphere(points: THREE.Vector3[]): Sphere {
  const p = [...points];
  let seed = 1;
  for (let i = p.length - 1; i > 0; i--) {
    seed = (seed * 16807) % 2147483647;
    const j = seed % (i + 1);
    [p[i], p[j]] = [p[j], p[i]];
  }
  let s = sphereOf([p[0]]);
  for (let i = 1; i < p.length; i++) {
    if (inside(s, p[i])) continue;
    s = sphereOf([p[i]]);
    for (let j = 0; j < i; j++) {
      if (inside(s, p[j])) continue;
      s = sphereOf([p[i], p[j]]);
      for (let k = 0; k < j; k++) {
        if (inside(s, p[k])) continue;
        s = sphereOf([p[i], p[j], p[k]]);
        for (let l = 0; l < k; l++) if (!inside(s, p[l])) s = sphereOf([p[i], p[j], p[k], p[l]]);
      }
    }
  }
  return s;
}

/** The rotation of a transform: its polar decomposition's (Eigen's computeRotationScaling, as Orca's
 * Transformation::get_rotation_matrix), so the shear a scale in World coordinates leaves is not taken for one. */
export function rotationOf(spec: Spec): THREE.Matrix4 {
  let r = new THREE.Matrix3().setFromMatrix4(fromSpec(spec));
  for (let i = 0; i < 30; i++) {
    const it = r.clone().invert().transpose();
    const next = new THREE.Matrix3().fromArray(r.elements.map((v, k) => 0.5 * (v + it.elements[k])));
    const done = next.elements.every((v, k) => Math.abs(v - r.elements[k]) < 1e-12);
    r = next;
    if (done) break;
  }
  return new THREE.Matrix4().setFromMatrix3(r);
}

/** M' = T(p)·A·T(-p)·M: A about the point p in the slicer's axes (Selection::transform_instance_relative, World);
 * in Object coordinates A is taken in the instance's own axes (R·A·R⁻¹, R its rotation). */
export function about(spec: Spec, a: THREE.Matrix4, p: THREE.Vector3, cs: Coordinates = 'world'): Spec {
  const r = rotationOf(spec);
  const inAxes = cs === 'object' ? r.clone().multiply(a).multiply(r.clone().invert()) : a;
  const t = new THREE.Matrix4().makeTranslation(p.x, p.y, p.z);
  const back = new THREE.Matrix4().makeTranslation(-p.x, -p.y, -p.z);
  return toSpec(t.multiply(inAxes).multiply(back).multiply(fromSpec(spec)));
}

const AXES = [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1)];
export const rotationAbout = (axis: 0 | 1 | 2, degrees: number) => new THREE.Matrix4().makeRotationAxis(AXES[axis], THREE.MathUtils.degToRad(degrees));

/** A direction on the slicer's axes in the instance's own axes, for a move in Object coordinates. */
export function inObjectAxes(spec: Spec, d: Vec): Vec {
  return new THREE.Vector3(...d).applyMatrix4(rotationOf(spec)).toArray() as Vec;
}

/** Transformation::get_rotation_by_quaternion, as the Rotate window's Absolute row shows it: Eigen's
 * eulerAngles(2, 1, 0) of the rotation with X and Z swapped, in degrees, under 0.001 as 0 (delete_negative_sign). */
export function absoluteRotation(spec: Spec): Vec {
  const e = rotationOf(spec).elements; // column-major
  const m = (r: number, c: number) => e[c * 4 + r];
  // Eigen's EulerAngles.h for (2, 1, 0): i = 2, j = 1, k = 0, an odd order
  let a0 = Math.atan2(m(1, 0), m(0, 0));
  const c2 = Math.hypot(m(2, 2), m(2, 1));
  let a1: number;
  if (a0 < 0) {
    a0 += Math.PI;
    a1 = Math.atan2(-m(2, 0), -c2);
  } else a1 = Math.atan2(-m(2, 0), c2);
  const s1 = Math.sin(a0), c1 = Math.cos(a0);
  const a2 = Math.atan2(s1 * m(0, 2) - c1 * m(1, 2), c1 * m(1, 1) - s1 * m(0, 1));
  return [a2, a1, a0].map((a) => {
    const d = THREE.MathUtils.radToDeg(a);
    return Math.abs(d) < 0.001 ? 0 : d;
  }) as Vec;
}

/** The transform with its rotation taken out, its scale and position kept (Transformation::reset_rotation). */
export function withoutRotation(spec: Spec): Spec {
  const m = fromSpec(spec);
  const unturned = rotationOf(spec).invert().multiply(m);
  unturned.setPosition(new THREE.Vector3().setFromMatrixPosition(m));
  return toSpec(unturned);
}

/** The transform's rotation and scale replaced by ``by``'s, its position kept (back to when the tool opened). */
export const withLinearOf = (spec: Spec, by: Spec): Spec => [...by.slice(0, 9), ...spec.slice(9)];

export interface Change {
  keys: string[];
  /** each selected instance's new transform */
  apply: (spec: Spec, key: string) => Spec;
  /** the object's other instances: their height only ('none'), or their rotation and scale too ('general') */
  sync: 'none' | 'general';
  /** do_move snaps what floats; do_rotate / do_scale also lift what the change sank, unless it was sinking already */
  drop: 'move' | 'transform';
}

const linear = (m: THREE.Matrix4) => m.clone().setPosition(0, 0, 0);
const position = (m: THREE.Matrix4) => new THREE.Vector3().setFromMatrixPosition(m);

/** ``placed`` after a change, as Orca leaves it: the object's other instances in step, each one back on the bed when
 * its object drops automatically; an instance back where the file has it is no placement. */
export function changed(view: ViewerScene, placed: Placed, change: Change, autoDrop: (objectId: string) => boolean): Placed {
  const all = items(view);
  const spec = (item: THREE.Object3D) => placed[keyOf(item)] ?? fileSpec(item);
  const next: Record<string, Spec> = {};
  for (const item of all) if (change.keys.includes(keyOf(item))) next[keyOf(item)] = change.apply(spec(item), keyOf(item));
  // synchronize_unselected_instances, from the first instance selected of each object
  for (const item of all) {
    if (change.keys.includes(keyOf(item))) continue;
    const lead = all.find((i) => change.keys.includes(keyOf(i)) && i.userData.objectId === item.userData.objectId);
    if (!lead) continue;
    const oldI = fromSpec(spec(lead)), newI = fromSpec(next[keyOf(lead)]);
    let mine = fromSpec(spec(item));
    if (change.sync === 'general') {
      const at = position(mine);
      mine = linear(mine).multiply(linear(oldI).invert()).multiply(linear(newI)).setPosition(at);
    }
    const at = position(mine);
    mine.setPosition(at.x, at.y, position(newI).z); // instances of an object stay at one height
    next[keyOf(item)] = toSpec(mine);
  }
  // the bed (GLCanvas3D::do_move / do_rotate / do_scale)
  for (const item of all) {
    const key = keyOf(item);
    if (!next[key] || !autoDrop(String(item.userData.objectId))) continue;
    const shift = minZ(item, next[key]);
    const sinkingBefore = minZ(item, spec(item)) < SINKING;
    const drop = change.drop === 'move' ? shift > SINKING : !sinkingBefore || shift > SINKING;
    if (drop && Math.abs(shift) > 1e-9) next[key] = [...next[key].slice(0, 11), Number((next[key][11] - shift).toFixed(9))];
  }
  const out = { ...placed };
  for (const item of all) {
    const key = keyOf(item);
    if (!next[key]) continue;
    const file = fileSpec(item);
    if (next[key].every((x, i) => Math.abs(x - file[i]) < 1e-9)) delete out[key];
    else out[key] = next[key];
  }
  return out;
}

/** A rotation in World coordinates about X or Y, or one in Object coordinates that tilts the object, turns the other
 * instances too (Selection::rotate's requires_general_synchronization); a turn about the bed's Z leaves them be. */
export function rotationSync(cs: Coordinates, axis: 0 | 1 | 2, spec: Spec, turned: Spec): 'none' | 'general' {
  if (cs === 'world') return axis === 2 ? 'none' : 'general';
  const up = (s: Spec, v: THREE.Vector3) => v.clone().applyMatrix4(rotationOf(s)).z;
  return AXES.some((v) => Math.abs(up(spec, v) - up(turned, v)) > 1e-4) ? 'general' : 'none';
}
