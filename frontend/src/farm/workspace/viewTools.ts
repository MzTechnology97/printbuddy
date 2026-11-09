// Fork (printfarm): the 3D view's tools for the workspace, on the scene ModelViewer hands over (ViewerScene):
// picking an object under the pointer, its size / volume / triangles (OrcaSlicer's object info), zoom to it,
// the slicer's view presets, the selection box and the measuring line. They only look: nothing here changes
// the project (moving, rotating... are phase 2, with a model_settings writer).
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import type { ViewerScene } from '../../components/ModelViewer';

export type ViewPreset = 'iso' | 'top' | 'bottom' | 'front' | 'rear' | 'left' | 'right';

export interface ObjectStats {
  /** X × Y × Z in mm, as the slicer's axes */
  size: [number, number, number];
  /** mm³ */
  volume: number;
  triangles: number;
  printable: boolean;
  /** edges not shared by exactly two triangles: the mesh is not closed (OrcaSlicer's mesh errors) */
  openEdges: number;
  /** the centre of mass, in the scene */
  centroid: THREE.Vector3;
}

const items = (view: ViewerScene) => view.model.children.filter((c) => c.userData.objectId != null);

/** ModelViewer swaps the 3MF's Y and Z, which mirrors the scene front to back; flipping the model back about
 * the bed's centre line gives the slicer's own view (front of the bed towards the default camera). */
export function unmirror(view: ViewerScene) {
  if (view.model.userData.unmirrored) return;
  view.model.scale.z = -1;
  view.model.position.z = view.bed.y - view.model.position.z;
  view.model.userData.unmirrored = true;
  view.model.updateMatrixWorld(true);
}

/** The signed volume of a mesh's triangles (divergence theorem); its size is the object's volume. */
export function meshVolume(geometry: THREE.BufferGeometry): number {
  const pos = geometry.getAttribute('position');
  const index = geometry.getIndex();
  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();
  const count = index ? index.count : pos.count;
  let sum = 0;
  for (let i = 0; i + 2 < count; i += 3) {
    const [ia, ib, ic] = index ? [index.getX(i), index.getX(i + 1), index.getX(i + 2)] : [i, i + 1, i + 2];
    a.fromBufferAttribute(pos, ia);
    b.fromBufferAttribute(pos, ib);
    c.fromBufferAttribute(pos, ic);
    sum += a.dot(b.cross(c));
  }
  return sum / 6;
}

/** Each object's info (its first instance on the plate shown). */
export function objectStats(view: ViewerScene): Map<string, ObjectStats> {
  const out = new Map<string, ObjectStats>();
  for (const item of items(view)) {
    const id = String(item.userData.objectId);
    if (out.has(id)) continue;
    let volume = 0;
    let triangles = 0;
    let open = 0;
    const weighted = new THREE.Vector3();
    item.updateMatrixWorld(true);
    item.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        const v = meshVolume(o.geometry);
        volume += v;
        triangles += (o.geometry.getIndex()?.count ?? o.geometry.getAttribute('position').count) / 3;
        open += openEdges(o.geometry);
        o.geometry.computeBoundingBox();
        weighted.addScaledVector(o.geometry.boundingBox!.getCenter(new THREE.Vector3()).applyMatrix4(o.matrixWorld), Math.abs(v));
      }
    });
    const box = new THREE.Box3().setFromObject(item);
    const size = box.getSize(new THREE.Vector3());
    out.set(id, {
      size: [size.x, size.z, size.y],
      volume: Math.abs(volume),
      triangles,
      printable: item.userData.printable !== false,
      openEdges: open,
      // shortcut: the volume-weighted centre of each mesh's bounding box, not the true centre of mass
      centroid: volume ? weighted.divideScalar(Math.abs(volume)) : box.getCenter(new THREE.Vector3()),
    });
  }
  return out;
}

/** Edges of an indexed mesh not shared by exactly two triangles (0: a closed, manifold mesh). */
export function openEdges(geometry: THREE.BufferGeometry): number {
  const index = geometry.getIndex();
  if (!index) return 0;
  const count = new Map<string, number>();
  for (let i = 0; i + 2 < index.count; i += 3) {
    const t = [index.getX(i), index.getX(i + 1), index.getX(i + 2)];
    for (let e = 0; e < 3; e++) {
      const [a, b] = [t[e], t[(e + 1) % 3]];
      const key = a < b ? `${a}_${b}` : `${b}_${a}`;
      count.set(key, (count.get(key) ?? 0) + 1);
    }
  }
  let open = 0;
  for (const n of count.values()) if (n !== 2) open++;
  return open;
}

/** The object and the point under the pointer (client coordinates), or null on the empty plate. */
export function pick(view: ViewerScene, clientX: number, clientY: number): { objectId: string; point: THREE.Vector3 } | null {
  const rect = view.renderer.domElement.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
  const ray = new THREE.Raycaster();
  ray.setFromCamera(ndc, view.camera);
  const hit = ray.intersectObjects(items(view), true)[0];
  if (!hit) return null;
  let o: THREE.Object3D | null = hit.object;
  while (o && o.userData.objectId == null) o = o.parent;
  return o ? { objectId: String(o.userData.objectId), point: hit.point.clone() } : null;
}

function itemOf(view: ViewerScene, objectId: string | null): THREE.Object3D | undefined {
  return objectId == null ? undefined : items(view).find((c) => String(c.userData.objectId) === objectId);
}

function boxOf(view: ViewerScene, objectId: string | null): THREE.Box3 {
  const item = itemOf(view, objectId);
  const box = new THREE.Box3().setFromObject(item ?? view.model);
  if (box.isEmpty()) box.set(new THREE.Vector3(0, 0, 0), new THREE.Vector3(view.bed.x, 10, view.bed.y));
  return box;
}

const DIRECTIONS: Record<ViewPreset, [number, number, number]> = {
  iso: [0.7, 0.5, 0.7],
  top: [0, 1, 0.001], // a hair towards the front: the bed's front edge at the bottom of the screen
  bottom: [0, -1, 0.001],
  front: [0, 0, 1],
  rear: [0, 0, -1],
  left: [-1, 0, 0],
  right: [1, 0, 0],
};

function look(view: ViewerScene, box: THREE.Box3, direction: THREE.Vector3) {
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const radius = Math.max(size.length() / 2, 5);
  const distance = radius / Math.sin(THREE.MathUtils.degToRad(view.camera.fov / 2)) * 1.1;
  view.camera.position.copy(center).addScaledVector(direction.normalize(), distance);
  view.controls.target.copy(center);
  view.controls.update();
}

/** Zoom to one object (or to everything shown), keeping the direction the camera looks from. */
export function fitTo(view: ViewerScene, objectId: string | null) {
  const direction = view.camera.position.clone().sub(view.controls.target);
  look(view, boxOf(view, objectId), direction.lengthSq() ? direction : new THREE.Vector3(...DIRECTIONS.iso));
}

/** One of the slicer's view presets, on the whole bed. */
export function setView(view: ViewerScene, preset: ViewPreset) {
  const bed = new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(view.bed.x, Math.min(view.bed.z, 40), view.bed.y));
  look(view, bed.union(boxOf(view, null)), new THREE.Vector3(...DIRECTIONS[preset]));
}

/** Remove a helper group added by name, freeing its geometry. */
function clear(view: ViewerScene, name: string) {
  const old = view.scene.getObjectByName(name);
  if (!old) return;
  view.scene.remove(old);
  old.traverse((o) => {
    if (o instanceof THREE.Mesh || o instanceof THREE.Line) o.geometry.dispose();
  });
}

const HIGHLIGHT = 'workspace-selection';

/** The selected objects: a white box round each (the slicer's selection corners) and, with ``outline``, their
 * orange outline (the canvas menu's "Outline"). */
export function highlight(view: ViewerScene, objectIds: string[], outline = true) {
  clear(view, HIGHLIGHT);
  const group = new THREE.Group();
  group.name = HIGHLIGHT;
  view.model.updateMatrixWorld(true);
  for (const item of items(view).filter((c) => objectIds.includes(String(c.userData.objectId)))) {
    group.add(new THREE.BoxHelper(item, 0xffffff));
    if (!outline) continue;
    item.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 40), new THREE.LineBasicMaterial({ color: 0xff9800 }));
      edges.applyMatrix4(o.matrixWorld);
      group.add(edges);
    });
  }
  view.scene.add(group);
}

/** Light up the object under the pointer (none: null). */
export function hover(view: ViewerScene, objectId: string | null) {
  for (const item of items(view)) {
    const on = String(item.userData.objectId) === objectId;
    item.traverse((o) => {
      if (o instanceof THREE.Mesh && o.material instanceof THREE.MeshPhongMaterial) o.material.emissive.setHex(on ? 0x303030 : 0x000000);
    });
  }
}

/** The objects whose centre falls in a screen rectangle (client coordinates), as Shift+drag selects. */
export function inRectangle(view: ViewerScene, from: { x: number; y: number }, to: { x: number; y: number }): string[] {
  const rect = view.renderer.domElement.getBoundingClientRect();
  const [x0, x1, y0, y1] = [Math.min(from.x, to.x), Math.max(from.x, to.x), Math.min(from.y, to.y), Math.max(from.y, to.y)];
  const out = new Set<string>();
  for (const item of items(view)) {
    const p = new THREE.Box3().setFromObject(item).getCenter(new THREE.Vector3()).project(view.camera);
    const x = rect.left + ((p.x + 1) / 2) * rect.width;
    const y = rect.top + ((1 - p.y) / 2) * rect.height;
    if (x >= x0 && x <= x1 && y >= y0 && y <= y1) out.add(String(item.userData.objectId));
  }
  return [...out];
}

/** Where a scene point shows, in pixels from the canvas's top left (null: behind the camera). */
export function toScreen(view: ViewerScene, point: THREE.Vector3): { x: number; y: number } | null {
  const p = point.clone().project(view.camera);
  if (p.z > 1) return null;
  const canvas = view.renderer.domElement;
  return { x: ((p.x + 1) / 2) * canvas.clientWidth, y: ((1 - p.y) / 2) * canvas.clientHeight };
}

/** OrcaSlicer's canvas menu options that change how the scene is drawn. */
export interface Display {
  overhangs: boolean;
  wireframe: boolean;
  xray: boolean;
  realistic: boolean;
  perspective: boolean;
  axes: boolean;
  centerOfMass: boolean;
}

/** Apply the canvas menu's options. ``overhangAngle``: faces looking down steeper than this from horizontal. */
export function applyDisplay(view: ViewerScene, d: Display, stats: Map<string, ObjectStats>, overhangAngle = 45) {
  view.model.updateMatrixWorld(true);
  for (const item of items(view)) {
    item.traverse((o) => {
      if (!(o instanceof THREE.Mesh) || !(o.material instanceof THREE.MeshPhongMaterial)) return;
      Object.assign(o.material, { wireframe: d.wireframe, transparent: d.xray, opacity: d.xray ? 0.35 : 1, depthWrite: !d.xray });
      o.material.shininess = d.realistic ? 80 : 30;
      o.material.needsUpdate = true;
    });
  }
  view.scene.traverse((o) => {
    if (o instanceof THREE.AmbientLight) o.intensity = d.realistic ? 0.35 : 0.6;
  });
  clear(view, 'workspace-overhangs');
  if (d.overhangs) {
    const group = new THREE.Group();
    group.name = 'workspace-overhangs';
    const limit = -Math.cos(THREE.MathUtils.degToRad(overhangAngle));
    const material = new THREE.MeshBasicMaterial({ color: 0xff3b30, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1 });
    const [a, b, c] = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()];
    for (const item of items(view)) {
      item.traverse((o) => {
        if (!(o instanceof THREE.Mesh)) return;
        const pos = o.geometry.getAttribute('position');
        const index = o.geometry.getIndex();
        const count = index ? index.count : pos.count;
        const out: number[] = [];
        for (let i = 0; i + 2 < count; i += 3) {
          const ids = index ? [index.getX(i), index.getX(i + 1), index.getX(i + 2)] : [i, i + 1, i + 2];
          a.fromBufferAttribute(pos, ids[0]).applyMatrix4(o.matrixWorld);
          b.fromBufferAttribute(pos, ids[1]).applyMatrix4(o.matrixWorld);
          c.fromBufferAttribute(pos, ids[2]).applyMatrix4(o.matrixWorld);
          // the outward normal: the unmirrored model (scale z -1) reverses the triangles' winding
          const outward = new THREE.Vector3().subVectors(c, a).cross(new THREE.Vector3().subVectors(b, a)).normalize();
          if (outward.y < limit && Math.min(a.y, b.y, c.y) > 0.2) out.push(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z);
        }
        if (!out.length) return;
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.Float32BufferAttribute(out, 3));
        group.add(new THREE.Mesh(g, material));
      });
    }
    view.scene.add(group);
  }
  clear(view, 'workspace-axes');
  if (d.axes) {
    // X red, Y green, Z blue from the bed's origin (front left), as the slicer's
    const group = new THREE.Group();
    group.name = 'workspace-axes';
    const origin = new THREE.Vector3(0, 0.2, view.bed.y);
    const length = Math.max(view.bed.x, view.bed.y) / 4;
    group.add(new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), origin, length, 0xe53935));
    group.add(new THREE.ArrowHelper(new THREE.Vector3(0, 0, -1), origin, length, 0x43a047));
    group.add(new THREE.ArrowHelper(new THREE.Vector3(0, 1, 0), origin, length, 0x1e88e5));
    view.scene.add(group);
  }
  clear(view, 'workspace-mass');
  if (d.centerOfMass) {
    const group = new THREE.Group();
    group.name = 'workspace-mass';
    const material = new THREE.MeshBasicMaterial({ color: 0xffeb3b, depthTest: false });
    for (const s of stats.values()) {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(1.2, 12, 8), material);
      dot.position.copy(s.centroid);
      dot.renderOrder = 10;
      group.add(dot);
    }
    view.scene.add(group);
  }
  setPerspective(view, d.perspective);
}

/** Perspective, or (nearly) orthographic: a narrow field of view from further away, framing the same. */
export function setPerspective(view: ViewerScene, on: boolean) {
  const fov = on ? 45 : 1.5;
  if (view.camera.fov === fov) return;
  const direction = view.camera.position.clone().sub(view.controls.target);
  const scale = Math.tan(THREE.MathUtils.degToRad(view.camera.fov / 2)) / Math.tan(THREE.MathUtils.degToRad(fov / 2));
  view.camera.fov = fov;
  view.camera.far = Math.max(view.camera.far, direction.length() * scale * 4);
  view.camera.position.copy(view.controls.target).addScaledVector(direction, scale);
  view.camera.updateProjectionMatrix();
  view.controls.update();
}

/** The section view: the model cut by a plane facing ``normal`` at ``ratio`` (0 to 1) of its extent; null: none. */
export function setSection(view: ViewerScene, section: { normal: THREE.Vector3; ratio: number } | null) {
  view.renderer.localClippingEnabled = !!section;
  let plane: THREE.Plane | null = null;
  if (section) {
    const n = section.normal.clone().normalize();
    const box = new THREE.Box3().setFromObject(view.model);
    const corners = [0, 1, 2, 3, 4, 5, 6, 7].map((i) =>
      new THREE.Vector3(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z).dot(n),
    );
    const [lo, hi] = [Math.min(...corners), Math.max(...corners)];
    plane = new THREE.Plane(n.clone().negate(), lo + (hi - lo) * section.ratio); // keeps the side behind the plane
  }
  for (const item of items(view)) {
    item.traverse((o) => {
      if (o instanceof THREE.Mesh && o.material instanceof THREE.Material) {
        o.material.clippingPlanes = plane ? [plane] : null;
        o.material.side = plane ? THREE.DoubleSide : THREE.FrontSide;
        o.material.needsUpdate = true;
      }
    });
  }
}

/** Whether the pointer (client coordinates) is over the bed: a right click there opens the plate's menu. */
export function onBed(view: ViewerScene, clientX: number, clientY: number): boolean {
  const rect = view.renderer.domElement.getBoundingClientRect();
  const ray = new THREE.Raycaster();
  ray.setFromCamera(new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1), view.camera);
  const p = ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
  return !!p && p.x >= 0 && p.x <= view.bed.x && p.z >= 0 && p.z <= view.bed.y;
}

/** The direction the camera looks from (the section's "Set viewing angle"). */
export function viewDirection(view: ViewerScene): THREE.Vector3 {
  return view.camera.position.clone().sub(view.controls.target).normalize();
}

const MEASURE = 'workspace-measure';

/** The measuring line from ``a`` to ``b`` (a dot when only ``a``); none with no points. */
export function drawMeasure(view: ViewerScene, a: THREE.Vector3 | null, b: THREE.Vector3 | null) {
  const old = view.scene.getObjectByName(MEASURE);
  if (old) {
    view.scene.remove(old);
    old.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.Line) o.geometry.dispose();
    });
  }
  if (!a) return;
  const group = new THREE.Group();
  group.name = MEASURE;
  const material = new THREE.MeshBasicMaterial({ color: 0xffeb3b, depthTest: false });
  for (const p of b ? [a, b] : [a]) {
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.8, 12, 8), material);
    dot.position.copy(p);
    dot.renderOrder = 10;
    group.add(dot);
  }
  if (b) {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), new THREE.LineBasicMaterial({ color: 0xffeb3b, depthTest: false }));
    line.renderOrder = 10;
    group.add(line);
  }
  view.scene.add(group);
}

/** Left and right clicks on the 3D view that are not drags (a right drag pans, a left drag orbits). */
export function useCanvasClicks(view: ViewerScene | null, onClick: (e: PointerEvent) => void, onContextMenu: (e: PointerEvent) => void) {
  const handlers = useRef({ onClick, onContextMenu });
  handlers.current = { onClick, onContextMenu };
  useEffect(() => {
    const canvas = view?.renderer.domElement;
    if (!canvas) return;
    let down: { x: number; y: number; button: number } | null = null;
    const press = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY, button: e.button };
    };
    const release = (e: PointerEvent) => {
      if (!down || down.button !== e.button) return;
      const still = Math.hypot(e.clientX - down.x, e.clientY - down.y) < 5;
      down = null;
      if (!still) return;
      if (e.button === 0) handlers.current.onClick(e);
      else if (e.button === 2) handlers.current.onContextMenu(e);
    };
    const noMenu = (e: Event) => e.preventDefault(); // the browser's own menu
    canvas.addEventListener('pointerdown', press);
    canvas.addEventListener('pointerup', release);
    canvas.addEventListener('contextmenu', noMenu);
    return () => {
      canvas.removeEventListener('pointerdown', press);
      canvas.removeEventListener('pointerup', release);
      canvas.removeEventListener('contextmenu', noMenu);
    };
  }, [view]);
}

// MainFrame's View menu: Ctrl+0..7 (Shortcuts.cpp); 7 frames the current plate
export const VIEW_KEYS: Record<string, ViewPreset | 'plate'> = {
  '0': 'iso',
  '1': 'top',
  '2': 'bottom',
  '3': 'front',
  '4': 'rear',
  '5': 'left',
  '6': 'right',
  '7': 'plate',
};

/** The canvas menu's switches (GLCanvas3D.cpp, its order). */
export interface CanvasOptions {
  navigator: boolean;
  zoomButton: boolean;
  overhangs: boolean;
  outline: boolean;
  wireframe: boolean;
  xray: boolean;
  realistic: boolean;
  perspective: boolean;
  axes: boolean;
  grid: boolean;
  focus: boolean;
  labels: boolean;
  centerOfMass: boolean;
}
export const CANVAS_DEFAULTS: CanvasOptions = {
  navigator: true,
  zoomButton: true,
  overhangs: false,
  outline: true,
  wireframe: false,
  xray: false,
  realistic: false,
  perspective: true,
  axes: true,
  grid: true,
  focus: true,
  labels: false,
  centerOfMass: false,
};
