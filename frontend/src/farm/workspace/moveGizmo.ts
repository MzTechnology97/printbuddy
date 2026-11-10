// Fork (printfarm): OrcaSlicer's Move gizmo (Gizmos/GLGizmoMove.cpp, v2.5.0) in the workspace's 3D view: an arrow
// per axis (X red, Y green, Z blue) from the selection's box centre to 20 pixels beyond the box, dragged along
// its axis. The drag is Orca's calc_projection (the pointer ray's point nearest the grab, along the axis) and
// snaps to 1 mm with Shift. The page shows the move while dragging and makes it one undo step on release.
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import type { ViewerScene } from '../../components/ModelViewer';

export type Axis = 0 | 1 | 2;
const COLORS = [0xbf0000, 0x00bf00, 0x0000bf]; // ColorRGBA::X(), Y(), Z(): 0.75 of each
const HOVER = [0xe60000, 0x00e600, 0x0000e6]; // AXES_HOVER_COLOR: 1.2 times
const SNAP = 1; // GLGizmoMove3D::m_snap_step, mm
const SPACE_PX = 20; // space_size: 20 pixels beyond the box
const NAME = 'workspace-move-gizmo';

const items = (view: ViewerScene) => view.model.children.filter((c) => c.userData.objectId != null);
const keyOf = (item: THREE.Object3D) => `${item.userData.objectId}:${Number(item.userData.instance ?? 0)}`;

/** The instances' box in the model group's frame (x: the slicer's X, y: its Z, z: its Y); null without any. */
export function selectionBox(view: ViewerScene, keys: string[]): THREE.Box3 | null {
  const box = new THREE.Box3();
  const v = new THREE.Vector3();
  const toModel = new THREE.Matrix4();
  view.model.updateMatrixWorld(true);
  const fromWorld = view.model.matrixWorld.clone().invert();
  for (const item of items(view)) {
    // a target is an instance (objectId:instance) or a part of it (objectId:instance/partId)
    const mine = keys.filter((k) => k.split('/')[0] === keyOf(item));
    if (!mine.length) continue;
    const parts = mine.some((k) => !k.includes('/')) ? null : new Set(mine.map((k) => k.split('/')[1]));
    item.traverse((o) => {
      if (parts && !parts.has(String(o.parent?.userData.partId))) return;
      if (!(o instanceof THREE.Mesh)) return;
      const pos = (o.geometry as THREE.BufferGeometry).getAttribute('position');
      toModel.copy(fromWorld).multiply(o.matrixWorld);
      for (let i = 0; i < pos.count; i++) box.expandByPoint(v.fromBufferAttribute(pos, i).applyMatrix4(toModel));
    });
  }
  return box.isEmpty() ? null : box;
}

// the slicer's X, Y, Z in the model group's frame
const LOCAL_AXES = [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 1), new THREE.Vector3(0, 1, 0)];

/** The slicer's axis (turned by ``orient``, the instance's rotation, in Object coordinates) as a direction in the scene. */
export function axisInScene(view: ViewerScene, axis: Axis, orient?: THREE.Matrix4): THREE.Vector3 {
  const d = new THREE.Vector3().setComponent(axis, 1);
  if (orient) d.applyMatrix4(orient);
  return LOCAL_AXES[0].clone().multiplyScalar(d.x).add(LOCAL_AXES[1].clone().multiplyScalar(d.y)).add(LOCAL_AXES[2].clone().multiplyScalar(d.z))
    .transformDirection(view.model.matrixWorld);
}

/** GLGizmoMove3D::calc_projection: how far along ``dir`` the pointer ray has taken the grab from ``start``. */
export function projection(ray: THREE.Ray, start: THREE.Vector3, dir: THREE.Vector3, snap: boolean): number {
  const d = ray.direction.clone().normalize();
  const inters = ray.origin.clone().addScaledVector(d, start.clone().sub(ray.origin).dot(d));
  const p = inters.sub(start).dot(dir);
  return snap ? SNAP * Math.round(p / SNAP) : p;
}

function worldPerPixel(view: ViewerScene, at: THREE.Vector3): number {
  const h = view.renderer.domElement.clientHeight || 1;
  const dist = view.camera.position.distanceTo(at);
  return (2 * dist * Math.tan(THREE.MathUtils.degToRad(view.camera.fov) / 2)) / h;
}

function clear(view: ViewerScene) {
  const old = view.scene.getObjectByName(NAME);
  if (!old) return;
  old.traverse((o) => {
    if (o instanceof THREE.Mesh || o instanceof THREE.Line) {
      o.geometry.dispose();
      (o.material as THREE.Material).dispose();
    }
  });
  view.scene.remove(old);
}

/** The arrows on the selection's box (none when it has no box): their grabbers named "grabber-<axis>". */
export function drawMoveGizmo(view: ViewerScene, keys: string[], hover: Axis | null, orient?: THREE.Matrix4): THREE.Group | null {
  clear(view);
  const box = selectionBox(view, keys);
  if (!box) return null;
  const group = new THREE.Group();
  group.name = NAME;
  const center = view.model.localToWorld(box.getCenter(new THREE.Vector3()));
  const px = worldPerPixel(view, center);
  const half = box.getSize(new THREE.Vector3()).multiplyScalar(0.5);
  const reach = [half.x, half.z, half.y]; // to the box's side on each slicer axis
  for (const axis of [0, 1, 2] as Axis[]) {
    const dir = axisInScene(view, axis, orient);
    const tip = center.clone().addScaledVector(dir, reach[axis] + SPACE_PX * px);
    const color = axis === hover ? HOVER[axis] : COLORS[axis];
    // the stem (Orca's grabber connection) and the arrow head, a few pixels wide whatever the zoom
    const length = center.distanceTo(tip);
    const line = new THREE.Mesh(
      new THREE.CylinderGeometry(1.2 * px, 1.2 * px, length, 8),
      new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true }),
    );
    line.position.copy(center).addScaledVector(dir, length / 2);
    line.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
    line.renderOrder = 999;
    const cone = new THREE.Mesh(
      new THREE.ConeGeometry(6 * px, 16 * px, 16),
      new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true }),
    );
    cone.position.copy(tip);
    cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
    cone.name = `grabber-${axis}`;
    cone.userData = { axis, tip: tip.clone(), center: center.clone() };
    cone.renderOrder = 1000;
    group.add(line, cone);
  }
  view.scene.add(group);
  return group;
}

export interface MoveHandlers {
  /** Object coordinates: the instance's rotation, the arrows' axes (the move is then on those axes) */
  orient?: () => THREE.Matrix4 | undefined;
  /** while dragging: the move so far on the slicer's axes (mm); release: ``done`` */
  onMove: (delta: [number, number, number], done: boolean) => void;
  /** the grabber under the pointer while dragging, for the tooltip ("X: 12.34") */
  onDrag?: (axis: Axis | null, at: { x: number; y: number } | null) => void;
}

/** The gizmo on ``keys`` (none: hidden). A press on a grabber is the gizmo's: the orbit and the click don't see it. */
export function useMoveGizmo(view: ViewerScene | null, keys: string[], handlers: MoveHandlers) {
  const live = useRef(handlers);
  live.current = handlers;
  const keysKey = keys.join('|');
  useEffect(() => {
    if (!view || !keys.length) return;
    const canvas = view.renderer.domElement;
    const host = canvas.parentElement ?? canvas;
    const ray = new THREE.Raycaster();
    let hover: Axis | null = null;
    let drag: { axis: Axis; start: THREE.Vector3; dir: THREE.Vector3; moved: number } | null = null;
    const redraw = () => drawMoveGizmo(view, keys, drag?.axis ?? hover, live.current.orient?.());
    let gizmo = redraw();
    const rayAt = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), view.camera);
      return ray;
    };
    const grabberAt = (e: PointerEvent): THREE.Object3D | null => {
      if (!gizmo) return null;
      const hit = rayAt(e).intersectObjects(gizmo.children.filter((c) => c.name.startsWith('grabber-')), false)[0];
      return hit?.object ?? null;
    };
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return;
      const g = grabberAt(e);
      if (!g) return;
      e.stopPropagation(); // not an orbit, not a click on the object
      e.preventDefault();
      const axis = g.userData.axis as Axis;
      drag = { axis, start: (g.userData.tip as THREE.Vector3).clone(), dir: axisInScene(view, axis, live.current.orient?.()), moved: 0 };
      view.controls.enabled = false;
      live.current.onDrag?.(axis, { x: e.clientX, y: e.clientY });
    };
    const move = (e: PointerEvent) => {
      if (!drag) {
        const g = grabberAt(e);
        const next = g ? (g.userData.axis as Axis) : null;
        if (next !== hover) {
          hover = next;
          canvas.style.cursor = next == null ? '' : 'pointer';
          gizmo = redraw();
        }
        return;
      }
      drag.moved = projection(rayAt(e).ray, drag.start, drag.dir, e.shiftKey);
      const delta: [number, number, number] = [0, 0, 0];
      delta[drag.axis] = drag.moved;
      live.current.onMove(delta, false); // the page moves the instances now: the arrows follow
      gizmo = redraw();
      live.current.onDrag?.(drag.axis, { x: e.clientX, y: e.clientY });
    };
    const up = () => {
      if (!drag) return;
      const delta: [number, number, number] = [0, 0, 0];
      delta[drag.axis] = drag.moved;
      drag = null;
      view.controls.enabled = true;
      live.current.onDrag?.(null, null);
      live.current.onMove(delta, true);
      gizmo = redraw();
    };
    const zoom = () => {
      if (!drag) gizmo = redraw(); // its grabbers stay 20 pixels beyond the box
    };
    host.addEventListener('pointerdown', down, true);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    view.controls.addEventListener('change', zoom);
    return () => {
      host.removeEventListener('pointerdown', down, true);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      view.controls.removeEventListener('change', zoom);
      view.controls.enabled = true;
      canvas.style.cursor = '';
      clear(view);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the same instances: ``keysKey``
  }, [view, keysKey]);
  /** Draw it again where the instances are now (after a move). */
  return () => {
    if (view && keys.length) drawMoveGizmo(view, keys, null, live.current.orient?.());
  };
}
