// Fork (printfarm): OrcaSlicer's instance transforms (v2.5.0) on top of placement.ts: a move, rotation or scale of
// the instances selected, in World or Object coordinates (Selection::translate / rotate / scale_and_translate), the
// object's other instances kept in step (synchronize_unselected_instances), then each one put back on the bed as
// GLCanvas3D::do_move / do_rotate / do_scale do with the object's auto_drop. Matrices act on column vectors in the
// slicer's coordinates (mm), as placement.fromSpec reads 3MF's numbers.
import * as THREE from 'three';
import type { ViewerScene } from '../../components/ModelViewer';
import { IDENTITY, type Placed, type Spec, fileSpec, fromSpec, itemKey, toSpec } from './placement';

/** World: the bed's axes; Object: the instance's own; Part: a part's own (GizmoObjectManipulation's World, Instance,
 * Local). */
export type Coordinates = 'world' | 'object' | 'part';
type Vec = [number, number, number];

/** Model.hpp SINKING_Z_THRESHOLD */
const SINKING = -0.001;

const items = (view: ViewerScene) => view.model.children.filter((c) => c.userData.objectId != null);
const keyOf = (item: THREE.Object3D) => itemKey(String(item.userData.objectId), Number(item.userData.instance ?? 0));
/** An item's part groups (ModelViewer: one per component, ``userData.partId`` its id in model_settings.config). */
const partsOf = (item: THREE.Object3D) => item.children.filter((c) => c.userData.partId !== undefined);

/** A part's placement key: shared by all the object's instances, as Orca's ModelVolume is. */
export const partKey = (objectId: string, partId: string) => `${objectId}/${partId}`;
export const isPartKey = (key: string) => key.includes('/');
/** What a tool works on: an instance (``objectId:instance``) or a part in it (``objectId:instance/partId``). */
export const targetKey = (objectId: string, instance: number, partId?: string | null) => itemKey(objectId, instance) + (partId ? `/${partId}` : '');
const splitTarget = (target: string) => {
  const [inst, part] = target.split('/');
  return { inst, part: part ?? null };
};

/** A part's vertices where the file puts them, in the slicer's coordinates, kept on its group (the mesh never moves). */
function filePoints(group: THREE.Object3D): Float32Array {
  if (group.userData.points) return group.userData.points as Float32Array;
  group.updateMatrixWorld(true);
  const fromGroup = group.matrixWorld.clone().invert();
  const toGroup = new THREE.Matrix4();
  const chunks: Float32Array[] = [];
  group.traverse((o) => {
    if (!(o instanceof THREE.Mesh)) return;
    const pos = (o.geometry as THREE.BufferGeometry).getAttribute('position');
    const e = toGroup.copy(fromGroup).multiply(o.matrixWorld).elements;
    const out = new Float32Array(pos.count * 3);
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
      out[3 * i] = e[0] * x + e[4] * y + e[8] * z + e[12];
      out[3 * i + 1] = e[2] * x + e[6] * y + e[10] * z + e[14]; // the viewer's Z is the slicer's Y
      out[3 * i + 2] = e[1] * x + e[5] * y + e[9] * z + e[13];
    }
    chunks.push(out);
  });
  const points = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of chunks) {
    points.set(c, at);
    at += c.length;
  }
  group.userData.points = points;
  return points;
}

/** The part's file transform (its component's), and its transform now. */
export const partFileSpec = (group: THREE.Object3D): Spec => (group.userData.transform as Spec | undefined) ?? IDENTITY;
const partSpec = (item: THREE.Object3D, group: THREE.Object3D, placed: Placed): Spec =>
  (group.userData.partId ? placed[partKey(String(item.userData.objectId), String(group.userData.partId))] : undefined) ?? partFileSpec(group);

/** From where the file puts a part to where it is: M·C·C₀⁻¹·M₀⁻¹ (M the instance's transform, C the part's). */
export function partMatrix(item: THREE.Object3D, group: THREE.Object3D, placed: Placed, instance: Spec = placed[keyOf(item)] ?? fileSpec(item)): THREE.Matrix4 {
  return fromSpec(instance)
    .multiply(fromSpec(partSpec(item, group, placed)))
    .multiply(fromSpec(partFileSpec(group)).invert())
    .multiply(fromSpec(fileSpec(item)).invert());
}

/** Each part of the item (or the one named) with its vertices and the matrix that places them now. */
function eachPart(item: THREE.Object3D, placed: Placed, part: string | null, visit: (points: Float32Array, m: number[]) => void, instance?: Spec) {
  for (const group of partsOf(item)) {
    if (part != null && String(group.userData.partId) !== part) continue;
    visit(filePoints(group), partMatrix(item, group, placed, instance).elements);
  }
}

function minZ(item: THREE.Object3D, placed: Placed, instance?: Spec): number {
  let low = Infinity;
  eachPart(item, placed, null, (p, m) => {
    for (let i = 0; i < p.length; i += 3) {
      const z = m[2] * p[i] + m[6] * p[i + 1] + m[10] * p[i + 2] + m[14];
      if (z < low) low = z;
    }
  }, instance);
  return low;
}

/** The targets' (instances' or a part's) points in the slicer's coordinates (turned by ``into``), in one flat array. */
export function targetPoints(view: ViewerScene, placed: Placed, targets: string[], into?: THREE.Matrix4): Float32Array {
  const parts: { p: Float32Array; m: number[] }[] = [];
  for (const item of items(view))
    for (const t of targets) {
      const { inst, part } = splitTarget(t);
      if (inst !== keyOf(item)) continue;
      eachPart(item, placed, part, (p, m) => parts.push({ p, m: into ? into.clone().multiply(new THREE.Matrix4().fromArray(m)).elements : m }));
    }
  const out = new Float32Array(parts.reduce((n, x) => n + x.p.length, 0)); // 0.1 µm at a metre: plenty, half the memory
  let at = 0;
  for (const { p, m } of parts) {
    for (let i = 0; i < p.length; i += 3) {
      const x = p[i], y = p[i + 1], z = p[i + 2];
      out[at++] = m[0] * x + m[4] * y + m[8] * z + m[12];
      out[at++] = m[1] * x + m[5] * y + m[9] * z + m[13];
      out[at++] = m[2] * x + m[6] * y + m[10] * z + m[14];
    }
  }
  return out;
}

/** The targets' box in the slicer's axes, or (Object coordinates) in the first target's own rotated axes. */
export function boxOf(view: ViewerScene, placed: Placed, targets: string[], cs: Coordinates = 'world', frame?: THREE.Matrix4): THREE.Box3 | null {
  // the axes: the first target's own (an instance's, a part's), or ``frame`` (a part's instance's, for Object)
  const into = cs !== 'world' ? (frame?.clone() ?? frameOf(view, placed, targets[0])).invert() : undefined;
  const p = targetPoints(view, placed, targets, into);
  if (!p.length) return null;
  let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
  for (let i = 0; i < p.length; i += 3) {
    const x = p[i], y = p[i + 1], z = p[i + 2];
    if (x < x0) x0 = x;
    if (x > x1) x1 = x;
    if (y < y0) y0 = y;
    if (y > y1) y1 = y;
    if (z < z0) z0 = z;
    if (z > z1) z1 = z;
  }
  return new THREE.Box3(new THREE.Vector3(x0, y0, z0), new THREE.Vector3(x1, y1, z1));
}

/** The rotation of a target's own axes: the instance's, or for a part the instance's and the part's together. */
export function frameOf(view: ViewerScene, placed: Placed, target: string | undefined): THREE.Matrix4 {
  if (!target) return new THREE.Matrix4();
  const { inst, part } = splitTarget(target);
  const item = items(view).find((i) => keyOf(i) === inst);
  if (!item) return new THREE.Matrix4();
  const m = fromSpec(placed[inst] ?? fileSpec(item));
  const group = part != null ? partsOf(item).find((g) => String(g.userData.partId) === part) : undefined;
  if (group) m.multiply(fromSpec(partSpec(item, group, placed)));
  return rotationOf(toSpec(m));
}

type Sphere = { center: THREE.Vector3; radius: number };

/** The targets' bounding sphere: Selection::get_bounding_sphere's (CGAL's minimum sphere), the rotation's pivot. */
export function boundingSphere(view: ViewerScene, placed: Placed, targets: string[]): Sphere | null {
  const p = targetPoints(view, placed, targets);
  return p.length ? minSphereOf(p) : null;
}

const EPS = 1e-7;

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

/** Welzl's minimum enclosing sphere on a flat xyz array: iterative (no recursion depth), the points in a fixed
 * shuffled order, the inside test on plain numbers; only the few boundary spheres build vectors. */
export function minSphereOf(p: ArrayLike<number>): Sphere {
  const n = Math.floor(p.length / 3);
  const order = new Uint32Array(n);
  for (let i = 0; i < n; i++) order[i] = i;
  let seed = 1;
  for (let i = n - 1; i > 0; i--) {
    seed = (seed * 16807) % 2147483647;
    const j = seed % (i + 1);
    const t = order[i];
    order[i] = order[j];
    order[j] = t;
  }
  const at = (k: number) => new THREE.Vector3(p[3 * order[k]], p[3 * order[k] + 1], p[3 * order[k] + 2]);
  let cx = 0, cy = 0, cz = 0, r = 0, r2 = 0;
  const set = (s: Sphere) => {
    cx = s.center.x;
    cy = s.center.y;
    cz = s.center.z;
    r = s.radius;
    r2 = r * r * (1 + 2e-9) + EPS;
  };
  const outside = (k: number) => {
    const i = 3 * order[k];
    const dx = p[i] - cx, dy = p[i + 1] - cy, dz = p[i + 2] - cz;
    return dx * dx + dy * dy + dz * dz > r2;
  };
  set(sphereOf([at(0)]));
  for (let i = 1; i < n; i++) {
    if (!outside(i)) continue;
    set(sphereOf([at(i)]));
    for (let j = 0; j < i; j++) {
      if (!outside(j)) continue;
      set(sphereOf([at(i), at(j)]));
      for (let k = 0; k < j; k++) {
        if (!outside(k)) continue;
        set(sphereOf([at(i), at(j), at(k)]));
        for (let l = 0; l < k; l++) if (outside(l)) set(sphereOf([at(i), at(j), at(k), at(l)]));
      }
    }
  }
  return { center: new THREE.Vector3(cx, cy, cz), radius: r };
}

/** The same on points (tests). */
export const minSphere = (points: THREE.Vector3[]) => minSphereOf(points.flatMap((v) => [v.x, v.y, v.z]));

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
export function about(spec: Spec, a: THREE.Matrix4, p: THREE.Vector3, cs: Coordinates = 'world', frame?: THREE.Matrix4): Spec {
  const r = frame ?? rotationOf(spec); // the axes A is taken in: the instance's, or for a part the one asked for
  const inAxes = cs !== 'world' ? r.clone().multiply(a).multiply(r.clone().invert()) : a;
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
  /** do_move snaps what floats; do_rotate / do_scale also lift what the change sank, unless it was sinking already;
   * Lay on face ("Gizmo-Place on Face") puts the object on the bed even when it was sinking */
  drop: 'move' | 'transform' | 'flatten';
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
    const shift = minZ(item, placed, next[key]);
    const sinkingBefore = change.drop !== 'flatten' && minZ(item, placed) < SINKING;
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

/** A part's change (Selection::transform_volume_relative): ``apply`` takes and gives the part's transform in the slicer's
 * coordinates as one instance shows it (W = M·C); the part's own becomes C' = M⁻¹·W', the same in every instance (they
 * share the part, as Orca's ModelVolume). Then each instance of the object back on the bed (do_move / do_rotate /
 * do_scale with auto_drop), as for an instance's change. */
export function changedPart(
  view: ViewerScene,
  placed: Placed,
  target: string,
  apply: (world: Spec) => Spec,
  drop: 'move' | 'transform',
  autoDrop: (objectId: string) => boolean,
): Placed {
  return changedParts(view, placed, [target], (w) => apply(w), drop, autoDrop);
}

/** The same for several parts of one instance (Orca's Volume selection), each with its own change. */
export function changedParts(
  view: ViewerScene,
  placed: Placed,
  targets: string[],
  apply: (world: Spec, target: string) => Spec,
  drop: 'move' | 'transform',
  autoDrop: (objectId: string) => boolean,
): Placed {
  const inst = splitTarget(targets[0] ?? '').inst;
  const item = items(view).find((i) => keyOf(i) === inst);
  if (!item || targets.some((t) => splitTarget(t).inst !== inst)) return placed;
  const objectId = String(item.userData.objectId);
  const m = fromSpec(placed[inst] ?? fileSpec(item));
  const next = { ...placed };
  for (const target of targets) {
    const { part } = splitTarget(target);
    const group = part != null ? partsOf(item).find((g) => String(g.userData.partId) === part) : undefined;
    if (!group || part == null) continue;
    const world = toSpec(m.clone().multiply(fromSpec(partSpec(item, group, placed))));
    const own = toSpec(m.clone().invert().multiply(fromSpec(apply(world, target))));
    const key = partKey(objectId, part);
    const file = partFileSpec(group);
    if (own.every((x, i) => Math.abs(x - file[i]) < 1e-9)) delete next[key];
    else next[key] = own;
  }
  for (const other of items(view)) {
    if (String(other.userData.objectId) !== objectId || !autoDrop(objectId)) continue;
    const k = keyOf(other);
    const shift = minZ(other, next);
    const sinkingBefore = minZ(other, placed) < SINKING;
    if (!(drop === 'move' ? shift > SINKING : !sinkingBefore || shift > SINKING) || Math.abs(shift) <= 1e-9) continue;
    const spec = next[k] ?? fileSpec(other);
    const lowered = [...spec.slice(0, 11), Number((spec[11] - shift).toFixed(9))];
    const fileItem = fileSpec(other);
    if (lowered.every((x, i) => Math.abs(x - fileItem[i]) < 1e-9)) delete next[k];
    else next[k] = lowered;
  }
  return next;
}

/** A part's transform as an instance shows it (W = M·C), and its own (C, in the instance's axes). */
export function partWorld(view: ViewerScene, placed: Placed, target: string): { world: Spec; own: Spec } | null {
  const { inst, part } = splitTarget(target);
  const item = items(view).find((i) => keyOf(i) === inst);
  const group = item && part != null ? partsOf(item).find((g) => String(g.userData.partId) === part) : undefined;
  if (!item || !group) return null;
  const own = partSpec(item, group, placed);
  return { world: toSpec(fromSpec(placed[inst] ?? fileSpec(item)).multiply(fromSpec(own))), own };
}

/** A rotation in World coordinates about X or Y, or one in Object coordinates that tilts the object, turns the other
 * instances too (Selection::rotate's requires_general_synchronization); a turn about the bed's Z leaves them be. */
export function rotationSync(cs: Coordinates, axis: 0 | 1 | 2, spec: Spec, turned: Spec): 'none' | 'general' {
  if (cs === 'world') return axis === 2 ? 'none' : 'general';
  const up = (s: Spec, v: THREE.Vector3) => v.clone().applyMatrix4(rotationOf(s)).z;
  return AXES.some((v) => Math.abs(up(spec, v) - up(turned, v)) > 1e-4) ? 'general' : 'none';
}
