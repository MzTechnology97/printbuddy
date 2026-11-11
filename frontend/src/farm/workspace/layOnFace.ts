// Fork (printfarm): OrcaSlicer's Lay on Face (Gizmos/GLGizmoFlatten.cpp and libslic3r/LayOnFace.cpp, v2.5.0): the
// faces of the convex hull of an instance's model parts that it can rest on, drawn over it; a click on one turns the
// instance so that face points down (the object's other instances turned alike) and puts it on the bed.
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { ConvexHull } from 'three/examples/jsm/math/ConvexHull.js';
import type { ViewerScene } from '../../components/ModelViewer';
import { type Spec, fromSpec, toSpec } from './placement';
import { toScene } from './transformGizmos';

/** A face to rest on: its outward normal and its outline (shrunk a little, raised 0.1 mm) in the object's
 * coordinates (its parts placed, the instance not), its area in mm² as the instance has it. */
export interface FacePlane {
  normal: THREE.Vector3;
  area: number;
  outline: THREE.Vector3[];
}

const MIN_AREA = 5; // mm²
const MIN_SIDE = 1; // mm
const MIN_ANGLE = 1; // degrees
const MAX_PLANES = 254; // the picking pass's limit

/** The instance's rotation and scale, without its place. */
const linearOf = (spec: Spec) => fromSpec(spec).setPosition(0, 0, 0);

/** 2D convex hull on x, y (Andrew's monotone chain), counter-clockwise, collinear points dropped; z kept. */
function hull2d(ps: THREE.Vector3[]): THREE.Vector3[] {
  const p = [...ps].sort((a, b) => a.x - b.x || a.y - b.y);
  if (p.length < 3) return p;
  const cross = (o: THREE.Vector3, a: THREE.Vector3, b: THREE.Vector3) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const half = (pts: THREE.Vector3[]) => {
    const out: THREE.Vector3[] = [];
    for (const q of pts) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], q) <= 1e-12) out.pop();
      out.push(q);
    }
    out.pop();
    return out;
  };
  return [...half(p), ...half([...p].reverse())];
}

/** lay_on_face_planes: ``points`` the model parts' vertices in object coordinates (flat xyz), ``instance`` the
 * instance's transform. Faces of the hull sharing a normal (within 0.001) and an edge make one; ones under 5 mm², a
 * triangle with a side under 1 mm or a corner under 1° are dropped; the largest 254 kept, largest first. */
export function facePlanes(points: ArrayLike<number>, instance: Spec): FacePlane[] {
  const vs: THREE.Vector3[] = [];
  for (let i = 0; i + 2 < points.length; i += 3) vs.push(new THREE.Vector3(points[i], points[i + 1], points[i + 2]));
  if (vs.length < 4) return [];
  const hull = new ConvexHull().setFromPoints(vs);
  const l = linearOf(instance);
  const normalMatrix = new THREE.Matrix3().setFromMatrix4(l).invert().transpose();
  const visited = new Set<unknown>();
  const planes: FacePlane[] = [];
  const cosMin = Math.cos(THREE.MathUtils.degToRad(MIN_ANGLE));
  for (const seed of hull.faces) {
    if (visited.has(seed)) continue;
    visited.add(seed);
    const normal = seed.normal.clone();
    const verts: THREE.Vector3[] = [];
    const queue = [seed];
    while (queue.length) {
      const face = queue.pop()!;
      let edge = face.edge;
      for (let k = 0; k < 3; k++) {
        verts.push(edge.head().point.clone().applyMatrix4(l));
        const next = edge.twin?.face;
        if (next && !visited.has(next) && Math.abs(next.normal.x - normal.x) < 0.001 && Math.abs(next.normal.y - normal.y) < 0.001 && Math.abs(next.normal.z - normal.z) < 0.001) {
          visited.add(next);
          queue.push(next);
        }
        edge = edge.next;
      }
    }
    if (verts.length === 3 && (verts[0].distanceTo(verts[1]) < MIN_SIDE || verts[0].distanceTo(verts[2]) < MIN_SIDE || verts[1].distanceTo(verts[2]) < MIN_SIDE)) continue;
    // into the plane's frame, where the face is horizontal
    const q = new THREE.Quaternion().setFromUnitVectors(normal.clone().applyMatrix3(normalMatrix).normalize(), new THREE.Vector3(0, 0, 1));
    const polygon = hull2d(verts.map((v) => v.applyQuaternion(q)));
    let area = 0;
    for (let i = 0; i < polygon.length; i++) {
      const a = polygon[i], b = polygon[(i + 1) % polygon.length];
      area += a.x * b.y - b.x * a.y;
    }
    area = Math.abs(area) / 2;
    if (area < MIN_AREA) continue;
    const sharp = polygon.some((c, i) => {
      const prev = polygon[(i + polygon.length - 1) % polygon.length], next = polygon[(i + 1) % polygon.length];
      return prev.clone().sub(c).normalize().dot(next.clone().sub(c).normalize()) > cosMin;
    });
    if (sharp) continue;
    // drawn a little inside its edges and 0.1 mm above the surface, back in object coordinates
    const centroid = polygon.reduce((s, v) => s.add(v), new THREE.Vector3()).divideScalar(polygon.length);
    const back = l.clone().invert().multiply(new THREE.Matrix4().makeRotationFromQuaternion(q.clone().invert()));
    const outline = polygon.map((v) => v.clone().multiplyScalar(0.9).addScaledVector(centroid, 0.1).add(new THREE.Vector3(0, 0, 0.1)).applyMatrix4(back));
    planes.push({ normal, area, outline });
  }
  return planes.sort((a, b) => b.area - a.area).slice(0, MAX_PLANES);
}

/** lay_on_face_rotation applied (Selection::flattening_rotate): the instance turned so ``normal`` (object coordinates)
 * points down, about its own place; a face pointing straight up is turned over about X, keeping its footprint. */
export function laidOnFace(spec: Spec, normal: THREE.Vector3): Spec {
  const l = linearOf(spec);
  const n = normal.clone().applyMatrix3(new THREE.Matrix3().setFromMatrix4(l).invert().transpose()).normalize();
  const down = new THREE.Vector3(0, 0, -1);
  const r = n.dot(down) < -1 + 1e-9
    ? new THREE.Matrix4().makeRotationX(Math.PI)
    : new THREE.Matrix4().makeRotationFromQuaternion(new THREE.Quaternion().setFromUnitVectors(n, down));
  return toSpec(r.multiply(l).setPosition(spec[9], spec[10], spec[11]));
}

const NAME = 'workspace-flatten-gizmo';
const COLOR = new THREE.Color(0.96, 0.93, 0.93); // GLGizmoBase::FLATTEN_COLOR, alpha 0.5
const HOVER = new THREE.Color(1, 1, 1); // FLATTEN_HOVER_COLOR, alpha 0.75

function clear(view: ViewerScene) {
  const old = view.scene.getObjectByName(NAME);
  if (!old) return;
  old.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      o.geometry.dispose();
      (o.material as THREE.Material).dispose();
    }
  });
  view.scene.remove(old);
}

/** The faces over the instance (``instance``: its transform now), the hovered one lighter; drawn over everything, as
 * Orca clears the depth before them. */
function draw(view: ViewerScene, planes: FacePlane[], instance: Spec, hover: number | null): THREE.Group {
  clear(view);
  view.model.updateMatrixWorld(true);
  const m = fromSpec(instance);
  const group = new THREE.Group();
  group.name = NAME;
  planes.forEach((plane, i) => {
    const pts = plane.outline.map((v) => toScene(view, v.clone().applyMatrix4(m)));
    const fan: THREE.Vector3[] = [];
    for (let k = 1; k + 1 < pts.length; k++) fan.push(pts[0], pts[k], pts[k + 1]);
    const mesh = new THREE.Mesh(
      new THREE.BufferGeometry().setFromPoints(fan),
      new THREE.MeshBasicMaterial({ color: i === hover ? HOVER : COLOR, transparent: true, opacity: i === hover ? 0.75 : 0.5, side: THREE.DoubleSide, depthTest: false, depthWrite: false }),
    );
    mesh.userData.plane = i;
    mesh.renderOrder = 996;
    group.add(mesh);
  });
  view.scene.add(group);
  return group;
}

/** The gizmo while ``planes`` are given (none: hidden). A press on a face is the gizmo's (``onLay``): the orbit and
 * the click don't see it; a press elsewhere selects as usual. */
export function useLayOnFace(view: ViewerScene | null, planes: FacePlane[] | null, instance: Spec | null, onLay: (normal: THREE.Vector3) => void) {
  const live = useRef({ instance, onLay });
  live.current = { instance, onLay };
  useEffect(() => {
    if (!view || !planes?.length || !live.current.instance) return;
    const canvas = view.renderer.domElement;
    const host = canvas.parentElement ?? canvas;
    const ray = new THREE.Raycaster();
    let hover: number | null = null;
    let gizmo = draw(view, planes, live.current.instance, hover);
    const at = (e: PointerEvent): number | null => {
      const r = canvas.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), view.camera);
      // the faces are drawn over the scene: the nearest one under the pointer, whatever is in front of it
      const hit = ray.intersectObjects(gizmo.children, false)[0];
      return hit ? (hit.object.userData.plane as number) : null;
    };
    const move = (e: PointerEvent) => {
      const next = at(e);
      if (next === hover) return;
      hover = next;
      canvas.style.cursor = next == null ? '' : 'pointer';
      if (live.current.instance) gizmo = draw(view, planes, live.current.instance, hover);
    };
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return;
      const i = at(e);
      if (i == null) return;
      e.stopPropagation(); // the canvas never sees the press: no orbit, and its release is no selection click
      e.preventDefault();
      live.current.onLay(planes[i].normal);
    };
    host.addEventListener('pointerdown', down, true);
    canvas.addEventListener('pointermove', move);
    return () => {
      host.removeEventListener('pointerdown', down, true);
      canvas.removeEventListener('pointermove', move);
      canvas.style.cursor = '';
      clear(view);
    };
  }, [view, planes, instance]);
}
