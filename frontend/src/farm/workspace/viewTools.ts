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
    item.traverse((o) => {
      if (o instanceof THREE.Mesh) {
        volume += meshVolume(o.geometry);
        triangles += (o.geometry.getIndex()?.count ?? o.geometry.getAttribute('position').count) / 3;
      }
    });
    const size = new THREE.Box3().setFromObject(item).getSize(new THREE.Vector3());
    out.set(id, { size: [size.x, size.z, size.y], volume: Math.abs(volume), triangles, printable: item.userData.printable !== false });
  }
  return out;
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

const HIGHLIGHT = 'workspace-selection';

/** A white box round the selected object (as the slicer's selection corners), or none. */
export function highlight(view: ViewerScene, objectId: string | null) {
  const old = view.scene.getObjectByName(HIGHLIGHT);
  if (old) {
    view.scene.remove(old);
    (old as THREE.BoxHelper).geometry.dispose();
  }
  const item = itemOf(view, objectId);
  if (!item) return;
  const box = new THREE.BoxHelper(item, 0xffffff);
  box.name = HIGHLIGHT;
  view.scene.add(box);
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
