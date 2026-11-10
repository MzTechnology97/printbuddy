// Fork (printfarm): OrcaSlicer's Rotate and Scale gizmos (Gizmos/GLGizmoRotate.cpp, GLGizmoScale.cpp, v2.5.0) in the
// workspace's 3D view, next to the Move one (moveGizmo.ts).
// Rotate: a ring per axis (X red, Y green, Z blue) of the selection's bounding sphere's radius + 5 mm about its
//   centre, with a grabber 15% beyond it; dragged, the angle is the pointer's in the ring's plane, snapped to 45°
//   between a third and two thirds of the radius and to 5° on the ring's scale (GLGizmoRotate::on_dragging).
// Scale: the selection's box with grabbers on its bottom face, X and Y at the sides' middles, Z at the bottom and top
//   centres, and four uniform ones (cyan) at the bottom corners. The ratio is calc_ratio's (from the bottom centre,
//   along the grab), in 5% steps with Shift; Ctrl scales one side only, from the opposite grabber.
// Both show the change while dragging; the page makes it one undo step on release.
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import type { ViewerScene } from '../../components/ModelViewer';

export type Axis = 0 | 1 | 2;
const COLORS = [0xbf0000, 0x00bf00, 0x0000bf]; // ColorRGBA::X(), Y(), Z()
const HOVER = [0xe60000, 0x00e600, 0x0000e6]; // AXES_HOVER_COLOR
const UNIFORM = 0x00ffff; // GRABBER_UNIFORM_COL
const UNIFORM_HOVER = 0x00b3b3; // GRABBER_UNIFORM_HOVER_COL
const BOX = 0xffffff;
const NAME = 'workspace-transform-gizmo';
const ROTATE_OFFSET = 5; // GLGizmoRotate::Offset, mm
const GRABBER_OFFSET = 0.15; // GLGizmoRotate::GrabberOffset
const COARSE = 8; // SnapRegionsCount
const FINE = 72; // ScaleStepsCount
const SCALE_SNAP = 0.05; // GLGizmoScale3D::m_snap_step

/** A point in the slicer's coordinates (mm) in the scene, and a direction. */
export const toScene = (view: ViewerScene, p: THREE.Vector3) => view.model.localToWorld(new THREE.Vector3(p.x, p.z, p.y));
export const dirToScene = (view: ViewerScene, d: THREE.Vector3) => new THREE.Vector3(d.x, d.z, d.y).transformDirection(view.model.matrixWorld);

export function worldPerPixel(view: ViewerScene, at: THREE.Vector3): number {
  const h = view.renderer.domElement.clientHeight || 1;
  return (2 * view.camera.position.distanceTo(at) * Math.tan(THREE.MathUtils.degToRad(view.camera.fov) / 2)) / h;
}

export function clearGizmo(view: ViewerScene) {
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

const basic = (color: number) => new THREE.MeshBasicMaterial({ color, depthTest: false, transparent: true });
const line = (points: THREE.Vector3[], color: number, loop = false) => {
  const l = new (loop ? THREE.LineLoop : THREE.Line)(
    new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true }),
  );
  l.renderOrder = 999;
  return l;
};

// ---- Rotate ----------------------------------------------------------------------------------------------------

/** The ring's plane for each axis: u and v with u × v = the axis, so a turn from u towards v is a positive one. */
const RING_AXES: [THREE.Vector3, THREE.Vector3][] = [
  [new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1)],
  [new THREE.Vector3(0, 0, 1), new THREE.Vector3(1, 0, 0)],
  [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0)],
];

export interface RotateFrame {
  /** the bounding sphere's centre and radius, in the slicer's coordinates */
  center: THREE.Vector3;
  radius: number;
}

/** GLGizmoRotate::on_dragging: the angle (radians, 0 to 2π) of a point of the ring's plane (slicer coordinates). */
export function ringAngle(frame: RotateFrame, axis: Axis, p: THREE.Vector3): number {
  const r = frame.radius + ROTATE_OFFSET;
  const d = p.clone().sub(frame.center);
  const [u, v] = RING_AXES[axis];
  const x = d.dot(u), y = d.dot(v);
  let theta = Math.atan2(y, x);
  if (theta < 0) theta += 2 * Math.PI;
  const len = Math.hypot(x, y);
  if (r / 3 <= len && len <= (2 * r) / 3) theta = ((2 * Math.PI) / COARSE) * Math.round(theta / ((2 * Math.PI) / COARSE));
  else if (r <= len && len <= r * 1.1) theta = ((2 * Math.PI) / FINE) * Math.round(theta / ((2 * Math.PI) / FINE));
  return theta >= 2 * Math.PI - 1e-12 ? 0 : theta;
}

function drawRotate(view: ViewerScene, frame: RotateFrame, hover: Axis | null, drag: { axis: Axis; angle: number } | null) {
  clearGizmo(view);
  const group = new THREE.Group();
  group.name = NAME;
  const r = frame.radius + ROTATE_OFFSET;
  const center = toScene(view, frame.center);
  const px = worldPerPixel(view, center);
  for (const axis of [0, 1, 2] as Axis[]) {
    if (drag && drag.axis !== axis) continue; // the other rings hide while one is dragged, as Orca's
    const [u, v] = RING_AXES[axis];
    const at = (a: number, rr = r) => toScene(view, frame.center.clone().addScaledVector(u, Math.cos(a) * rr).addScaledVector(v, Math.sin(a) * rr));
    const color = axis === hover || drag?.axis === axis ? HOVER[axis] : COLORS[axis];
    group.add(line(Array.from({ length: 64 }, (_, i) => at((i / 64) * 2 * Math.PI)), color, true));
    if (drag) {
      // the scale (a tooth every 5°, a long one every 10°) and the snap radii, while dragging
      for (let i = 0; i < FINE; i++) {
        const a = (i / FINE) * 2 * Math.PI;
        group.add(line([at(a, r), at(a, r * (i % 2 === 0 ? 1.1 : 1.05))], color));
      }
      for (let i = 0; i < COARSE; i++) {
        const a = (i / COARSE) * 2 * Math.PI;
        group.add(line([at(a, r / 3), at(a, (2 * r) / 3)], color));
      }
      group.add(line([center, at(drag.angle, r * (1 + GRABBER_OFFSET))], 0xffffff));
    }
    const grabber = new THREE.Mesh(new THREE.SphereGeometry(5 * px, 12, 8), basic(color));
    grabber.position.copy(at(drag?.axis === axis ? drag.angle : 0, r * (1 + GRABBER_OFFSET)));
    grabber.name = `grabber-${axis}`;
    grabber.userData = { axis };
    grabber.renderOrder = 1000;
    group.add(grabber);
  }
  view.scene.add(group);
  return group;
}

export interface RotateHandlers {
  /** where the rings are: the selection's bounding sphere now */
  frame: () => RotateFrame | null;
  /** while dragging: the turn about the axis so far (degrees) about ``pivot`` (the sphere's centre at the grab);
   * release: ``done`` */
  onRotate: (axis: Axis, degrees: number, done: boolean, pivot: THREE.Vector3) => void;
  onDrag?: (text: string | null, at: { x: number; y: number } | null) => void;
}

// ---- Scale -----------------------------------------------------------------------------------------------------

export interface ScaleFrame {
  /** the box, in its own axes (``orient``: the slicer's, or the instance's rotation) */
  box: THREE.Box3;
  orient: THREE.Matrix4;
}

/** The grabbers' places on the box (GLGizmoScale3D::update_grabbers_data), in the box's own axes about its centre. */
export function scaleGrabbers(half: THREE.Vector3): THREE.Vector3[] {
  const { x, y, z } = half;
  return [
    new THREE.Vector3(-x, 0, -z), new THREE.Vector3(x, 0, -z),
    new THREE.Vector3(0, -y, -z), new THREE.Vector3(0, y, -z),
    new THREE.Vector3(0, 0, -z), new THREE.Vector3(0, 0, z),
    new THREE.Vector3(-x, -y, -z), new THREE.Vector3(x, -y, -z), new THREE.Vector3(x, y, -z), new THREE.Vector3(-x, y, -z),
  ];
}
const CONSTRAINT = [1, 0, 3, 2, 5, 4, 8, 9, 6, 7]; // constraint_id: the grabber across

/** GLGizmoScale3D::calc_ratio, in the slicer's coordinates. ``pivot``: the bottom centre (or, Ctrl, the grabber across). */
export function scaleRatio(ray: THREE.Ray, drag: THREE.Vector3, pivot: THREE.Vector3, normal: THREE.Vector3, top: boolean, snap: boolean): number {
  const start = drag.clone().sub(pivot);
  const len = start.length();
  if (len === 0) return 1;
  const dir = ray.direction.clone().normalize();
  let n = normal.clone();
  if (top) n = dir.clone().cross(normal).cross(normal); // the Z grabber on top moves in a plane facing the pointer
  n.normalize();
  const angle = THREE.MathUtils.radToDeg(Math.acos(Math.max(-1, Math.min(1, n.dot(dir)))));
  if (Math.abs(angle) < 95 && Math.abs(angle) > 85) return 1;
  const t = drag.clone().sub(ray.origin).dot(n) / dir.dot(n);
  const inters = ray.origin.clone().addScaledVector(dir, t);
  let ratio = (len + inters.sub(drag).dot(start.normalize())) / len;
  if (snap) ratio = SCALE_SNAP * Math.round(ratio / SCALE_SNAP);
  return ratio;
}

function drawScale(view: ViewerScene, frame: ScaleFrame, hover: number | null) {
  clearGizmo(view);
  const group = new THREE.Group();
  group.name = NAME;
  const c = frame.box.getCenter(new THREE.Vector3());
  const half = frame.box.getSize(new THREE.Vector3()).multiplyScalar(0.5);
  const place = (local: THREE.Vector3) => toScene(view, local.clone().add(c).applyMatrix4(frame.orient));
  const corners = [-1, 1].flatMap((sx) => [-1, 1].flatMap((sy) => [-1, 1].map((sz) => place(new THREE.Vector3(sx * half.x, sy * half.y, sz * half.z)))));
  for (const [a, b] of [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]])
    group.add(line([corners[a], corners[b]], BOX));
  const px = worldPerPixel(view, place(new THREE.Vector3()));
  scaleGrabbers(half).forEach((g, i) => {
    const color = i >= 6 ? (hover === i ? UNIFORM_HOVER : UNIFORM) : hover === i ? HOVER[Math.floor(i / 2)] : COLORS[Math.floor(i / 2)];
    const m = new THREE.Mesh(new THREE.BoxGeometry(8 * px, 8 * px, 8 * px), basic(color));
    m.position.copy(place(g));
    m.name = `grabber-${i}`;
    m.userData = { grabber: i };
    m.renderOrder = 1000;
    group.add(m);
  });
  view.scene.add(group);
  return group;
}

export interface ScaleHandlers {
  frame: () => ScaleFrame | null;
  /** while dragging: the scale on the box's axes and, Ctrl, the move that keeps the other side (in the box's axes,
   * like the scale); release: ``done`` */
  onScale: (scale: [number, number, number], offset: THREE.Vector3, done: boolean) => void;
  onDrag?: (grabber: number | null, at: { x: number; y: number } | null) => void;
}

// ---- the pointer -----------------------------------------------------------------------------------------------

/** A pointer drag on a gizmo's grabbers: a press on one is the gizmo's alone (neither the orbit nor the click sees it). */
function useGrab(
  view: ViewerScene | null,
  active: boolean,
  hooks: {
    draw: (hover: number | null) => THREE.Group | null;
    start: (grabber: THREE.Object3D, ray: THREE.Ray, e: PointerEvent) => boolean;
    move: (ray: THREE.Ray, e: PointerEvent) => void;
    end: () => void;
  },
) {
  const live = useRef(hooks);
  live.current = hooks;
  useEffect(() => {
    if (!view || !active) return;
    const canvas = view.renderer.domElement;
    const host = canvas.parentElement ?? canvas;
    const caster = new THREE.Raycaster();
    let hover: number | null = null;
    let dragging = false;
    let gizmo = live.current.draw(null);
    const rayAt = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      caster.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), view.camera);
      return caster;
    };
    const grabberAt = (e: PointerEvent) =>
      gizmo ? (rayAt(e).intersectObjects(gizmo.children.filter((c) => c.name.startsWith('grabber-')), false)[0]?.object ?? null) : null;
    const id = (o: THREE.Object3D | null) => (o ? Number(o.name.slice('grabber-'.length)) : null);
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return;
      const g = grabberAt(e);
      if (!g || !live.current.start(g, rayAt(e).ray, e)) return;
      e.stopPropagation();
      e.preventDefault();
      dragging = true;
      view.controls.enabled = false;
    };
    const move = (e: PointerEvent) => {
      if (!dragging) {
        const next = id(grabberAt(e));
        if (next !== hover) {
          hover = next;
          canvas.style.cursor = next == null ? '' : 'pointer';
          gizmo = live.current.draw(hover);
        }
        return;
      }
      live.current.move(rayAt(e).ray, e);
    };
    const up = () => {
      if (!dragging) return;
      dragging = false;
      view.controls.enabled = true;
      live.current.end();
      gizmo = live.current.draw(null);
    };
    const zoom = () => {
      if (!dragging) gizmo = live.current.draw(hover);
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
      clearGizmo(view);
    };
  }, [view, active]);
  /** Draw it again where the selection is now (after a change from the window, Undo...). */
  return () => {
    if (view && active) live.current.draw(null);
  };
}

/** The scene's ray in the slicer's coordinates. */
function slicerRay(view: ViewerScene, ray: THREE.Ray): THREE.Ray {
  const toSlicer = view.model.matrixWorld.clone().invert();
  const o = ray.origin.clone().applyMatrix4(toSlicer), d = ray.direction.clone().transformDirection(toSlicer);
  return new THREE.Ray(new THREE.Vector3(o.x, o.z, o.y), new THREE.Vector3(d.x, d.z, d.y).normalize());
}

export function useRotateGizmo(view: ViewerScene | null, active: boolean, handlers: RotateHandlers) {
  const h = useRef(handlers);
  h.current = handlers;
  const state = useRef<{ axis: Axis; frame: RotateFrame; angle: number } | null>(null);
  return useGrab(view, active, {
    draw: (hover) => {
      if (!view) return null;
      const frame = state.current?.frame ?? h.current.frame();
      return frame ? drawRotate(view, frame, hover as Axis | null, state.current) : null;
    },
    start: (g) => {
      const frame = h.current.frame();
      if (!frame) return false;
      state.current = { axis: g.userData.axis as Axis, frame, angle: 0 };
      return true;
    },
    move: (ray, e) => {
      const s = state.current;
      if (!view || !s) return;
      const r = slicerRay(view, ray);
      const n = new THREE.Vector3().setComponent(s.axis, 1);
      const hit = r.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n, s.frame.center), new THREE.Vector3());
      if (!hit) return;
      s.angle = ringAngle(s.frame, s.axis, hit);
      const degrees = THREE.MathUtils.radToDeg(s.angle);
      h.current.onRotate(s.axis, degrees, false, s.frame.center);
      drawRotate(view, s.frame, null, s);
      h.current.onDrag?.(`${'XYZ'[s.axis]}: ${degrees.toFixed(2)}`, { x: e.clientX, y: e.clientY });
    },
    end: () => {
      const s = state.current;
      state.current = null;
      h.current.onDrag?.(null, null);
      if (s) h.current.onRotate(s.axis, THREE.MathUtils.radToDeg(s.angle), true, s.frame.center);
    },
  });
}

export function useScaleGizmo(view: ViewerScene | null, active: boolean, handlers: ScaleHandlers) {
  const h = useRef(handlers);
  h.current = handlers;
  const state = useRef<{ grabber: number; frame: ScaleFrame; drag: THREE.Vector3; pivot: THREE.Vector3; ctrl: boolean } | null>(null);
  const last = useRef<{ scale: [number, number, number]; offset: THREE.Vector3 }>({ scale: [1, 1, 1], offset: new THREE.Vector3() });
  return useGrab(view, active, {
    draw: (hover) => {
      if (!view) return null;
      const frame = h.current.frame();
      return frame ? drawScale(view, frame, hover) : null;
    },
    start: (g, _ray, e) => {
      const frame = h.current.frame();
      if (!frame) return false;
      const c = frame.box.getCenter(new THREE.Vector3());
      const half = frame.box.getSize(new THREE.Vector3()).multiplyScalar(0.5);
      const grabbers = scaleGrabbers(half);
      const i = g.userData.grabber as number;
      const at = (local: THREE.Vector3) => local.clone().add(c).applyMatrix4(frame.orient);
      const ctrl = e.ctrlKey || e.metaKey;
      state.current = { grabber: i, frame, drag: at(grabbers[i]), pivot: ctrl && i < 6 ? at(grabbers[CONSTRAINT[i]]) : at(grabbers[4]), ctrl };
      last.current = { scale: [1, 1, 1], offset: new THREE.Vector3() };
      return true;
    },
    move: (ray, e) => {
      const s = state.current;
      if (!view || !s) return;
      const normal = new THREE.Vector3(0, 0, 1).transformDirection(s.frame.orient);
      const ratio = scaleRatio(slicerRay(view, ray), s.drag, s.pivot, normal, s.grabber === 5, e.shiftKey);
      if (ratio <= 0) return;
      const size = s.frame.box.getSize(new THREE.Vector3());
      const scale: [number, number, number] = [1, 1, 1];
      const offset = new THREE.Vector3();
      if (s.grabber < 6) {
        const axis = Math.floor(s.grabber / 2);
        scale[axis] = ratio;
        if (s.ctrl && Math.abs(ratio - 1) > 0.001) offset.setComponent(axis, 0.5 * (ratio - 1) * size.getComponent(axis) * (s.grabber === 2 * axis ? -1 : 1));
      } else {
        scale[0] = scale[1] = ratio;
        scale[2] = s.ctrl && Math.abs(ratio - 1) > 0.001 ? 1 : ratio;
        if (s.ctrl && Math.abs(ratio - 1) > 0.001) {
          const sx = [-1, 1, 1, -1][s.grabber - 6], sy = [-1, -1, 1, 1][s.grabber - 6];
          offset.set(sx * 0.5 * (ratio - 1) * size.x, sy * 0.5 * (ratio - 1) * size.y, 0);
        }
      }
      last.current = { scale, offset };
      h.current.onScale(scale, offset, false);
      h.current.onDrag?.(s.grabber, { x: e.clientX, y: e.clientY });
    },
    end: () => {
      const s = state.current;
      state.current = null;
      h.current.onDrag?.(null, null);
      if (s) h.current.onScale(last.current.scale, last.current.offset, true);
    },
  });
}
