// Fork (printfarm): the 3D view's tools for the workspace, on the scene ModelViewer hands over (ViewerScene):
// picking an object under the pointer, its size / volume / triangles (OrcaSlicer's object info), zoom to it,
// the slicer's view presets, the selection box and the measuring line. They only look: nothing here changes
// the project (moving, rotating... are phase 2, with a model_settings writer).
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import type { ViewerScene } from '../../components/ModelViewer';
import type { WorkspaceObject } from './api';
import type { OwnEdits } from './draft';
import { filamentOf, printedFilament } from './objects';

export type ViewPreset = 'iso' | 'top' | 'bottom' | 'front' | 'rear' | 'left' | 'right';
export type Vec3 = [number, number, number];

// the navigator cube's faces: in the scene their normal and the directions of their own right (u) and up (v)
const NAV_FACES: Record<string, { n: Vec3; u: Vec3; v: Vec3 }> = {
  front: { n: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0] },
  rear: { n: [0, 0, -1], u: [-1, 0, 0], v: [0, 1, 0] },
  right: { n: [1, 0, 0], u: [0, 0, -1], v: [0, 1, 0] },
  left: { n: [-1, 0, 0], u: [0, 0, 1], v: [0, 1, 0] },
  top: { n: [0, 1, 0], u: [1, 0, 0], v: [0, 0, -1] },
  bottom: { n: [0, -1, 0], u: [1, 0, 0], v: [0, 0, 1] },
};

/** Where a click on a navigator face looks from: the face, or near its border the edge (two faces) or the corner
 * (three) there. ``x`` and ``y`` from 0 to 1 across the face, from its top left. */
export function navigatorView(face: ViewPreset, x: number, y: number): ViewPreset | Vec3 {
  const f = NAV_FACES[face];
  const [cu, cv] = [x * 2 - 1, 1 - y * 2];
  const edge = 0.6; // the outer fifth of each side
  if (!f || (Math.abs(cu) < edge && Math.abs(cv) < edge)) return face;
  const su = Math.abs(cu) < edge ? 0 : Math.sign(cu);
  const sv = Math.abs(cv) < edge ? 0 : Math.sign(cv);
  return f.n.map((n, i) => n + su * f.u[i] + sv * f.v[i]) as Vec3;
}

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
  /** some of it below the bed (the object list's sinking column) */
  sinking: boolean;
  /** painted supports / colours (the object list's paint columns) */
  paint: { supports: boolean; color: boolean };
  /** a variable layer height profile (the object list's height column) */
  variableLayers: boolean;
}

/** What the 3D view selects: a whole object (every instance), one instance of it, or one part of an instance. */
export interface Target {
  objectId: string;
  /** the instance's number among the object's build items (from 0); none: all of them */
  instance?: number | null;
  /** the part's id (model_settings.config); none: the whole instance */
  partId?: string | null;
}

// one raycaster for every pick (the pointer moves often)
const RAY = new THREE.Raycaster();

const items = (view: ViewerScene) => view.model.children.filter((c) => c.userData.objectId != null);

/** The scene nodes a target covers: instances, or the part's group in each. */
function nodesOf(view: ViewerScene, t: Target): THREE.Object3D[] {
  return items(view)
    .filter((i) => String(i.userData.objectId) === t.objectId && (t.instance == null || i.userData.instance === t.instance))
    .flatMap((i) => (t.partId ? i.children.filter((c) => c.userData.partId === t.partId) : [i]));
}

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

/** The info of one scene node (an instance, or a part's group in it). */
function statsOf(node: THREE.Object3D): ObjectStats {
  let volume = 0;
  let triangles = 0;
  let open = 0;
  const weighted = new THREE.Vector3();
  node.updateMatrixWorld(true);
  node.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      const v = meshVolume(o.geometry) * o.matrixWorld.determinant(); // scaled as the mesh is
      volume += v;
      triangles += (o.geometry.getIndex()?.count ?? o.geometry.getAttribute('position').count) / 3;
      open += openEdges(o.geometry);
      o.geometry.computeBoundingBox();
      weighted.addScaledVector(o.geometry.boundingBox!.getCenter(new THREE.Vector3()).applyMatrix4(o.matrixWorld), Math.abs(v));
    }
  });
  // Plater.cpp's size: the instance's (or part's) transformed hull, boxed on the bed's axes (precise: its vertices)
  const box = new THREE.Box3().setFromObject(node, true);
  const size = box.getSize(new THREE.Vector3());
  let item: THREE.Object3D | null = node;
  while (item && item.userData.objectId == null) item = item.parent;
  return {
    size: [size.x, size.z, size.y],
    volume: Math.abs(volume),
    triangles,
    printable: item?.userData.printable !== false,
    openEdges: open,
    // shortcut: the volume-weighted centre of each mesh's bounding box, not the true centre of mass
    centroid: volume ? weighted.divideScalar(Math.abs(volume)) : box.getCenter(new THREE.Vector3()),
    sinking: box.min.y < -0.001, // OrcaSlicer's SINKING_Z_THRESHOLD
    paint: { supports: !!item?.userData.paint?.supports, color: !!item?.userData.paint?.color },
    variableLayers: !!item?.userData.variableLayers,
  };
}

/** Each object's info (its first instance shown), sinking when any of its instances is. */
export function objectStats(view: ViewerScene): Map<string, ObjectStats> {
  const out = new Map<string, ObjectStats>();
  for (const item of items(view)) {
    const id = String(item.userData.objectId);
    const s = out.get(id);
    if (!s) out.set(id, statsOf(item));
    else if (!s.sinking && new THREE.Box3().setFromObject(item).min.y < -0.001) s.sinking = true;
  }
  return out;
}

/** The info of one instance or part (the info box with a part selected); undefined when not shown. */
export function targetStats(view: ViewerScene, target: Target): ObjectStats | undefined {
  const node = nodesOf(view, target)[0];
  return node && statsOf(node);
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

/** The object (its instance and part) and the point under the pointer (client coordinates), or null on the
 * empty plate. */
export function pick(
  view: ViewerScene,
  clientX: number,
  clientY: number,
): { objectId: string; instance: number; partId: string | null; point: THREE.Vector3 } | null {
  const rect = view.renderer.domElement.getBoundingClientRect();
  if (!rect.width || !rect.height || view.model.userData.readOnly) return null; // a duplicated plate's (WorkspacePage)
  const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
  RAY.setFromCamera(ndc, view.camera);
  // the raycaster sees hidden meshes too: a part the draft deletes (hidden) is not there to pick
  const shown = (o: THREE.Object3D | null): boolean => !o || (o.visible && shown(o.parent));
  const hit = RAY.intersectObjects(items(view), true).find((h) => shown(h.object));
  if (!hit) return null;
  let o: THREE.Object3D | null = hit.object;
  let partId: string | null = null;
  while (o && o.userData.objectId == null) {
    partId ??= o.userData.partId ?? null;
    o = o.parent;
  }
  return o ? { objectId: String(o.userData.objectId), instance: Number(o.userData.instance ?? 0), partId, point: hit.point.clone() } : null;
}

/** Each model part in the colour of the filament it prints with (its own, else its object's: the Fila. column),
 * the unsaved edits made. Painted objects, modifiers and the other kinds keep what the file shows: a painted
 * part's colours are its paint's, merged per filament in more than one mesh. */
export function colourFilaments(view: ViewerScene, objects: WorkspaceObject[], edits: OwnEdits, colours: string[]) {
  const byId = new Map(objects.map((o) => [o.id ?? '', o]));
  for (const item of items(view)) {
    const obj = byId.get(String(item.userData.objectId));
    if (!obj || item.userData.paint?.color) continue;
    for (const group of item.children) {
      const partId = (group.userData.partId as string | null | undefined) ?? null;
      const part = partId ? obj.parts.find((p) => p.id === partId) : undefined;
      if ((part?.type ?? 'normal_part') !== 'normal_part') continue;
      const meshes = group.children.filter((c): c is THREE.Mesh => c instanceof THREE.Mesh);
      if (meshes.length !== 1 || !(meshes[0].material instanceof THREE.MeshPhongMaterial)) continue;
      const hex = colours[(part?.id ? printedFilament(obj, part.id, edits) : filamentOf(obj, null, edits)) - 1];
      if (hex) meshes[0].material.color.set(hex.slice(0, 7));
    }
  }
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

/** One of the slicer's view presets (or a direction to look from), on the whole bed (every plate shown). */
export function setView(view: ViewerScene, preset: ViewPreset | THREE.Vector3) {
  const bed = new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(view.bed.x, Math.min(view.bed.z, 40), view.bed.y));
  const plates = view.scene.getObjectByName(PLATES);
  if (plates) bed.union(new THREE.Box3().setFromObject(plates));
  look(view, bed.union(boxOf(view, null)), preset instanceof THREE.Vector3 ? preset.clone() : new THREE.Vector3(...DIRECTIONS[preset]));
}

/** The view preset's direction (the navigator's faces, edges and corners add them up). */
export function presetDirection(preset: ViewPreset): THREE.Vector3 {
  return new THREE.Vector3(...DIRECTIONS[preset]).normalize();
}

/** Turn the camera round its target (the navigator's drag): ``azimuth`` about the vertical, ``polar`` up/down. */
export function orbit(view: ViewerScene, azimuth: number, polar: number) {
  const offset = view.camera.position.clone().sub(view.controls.target);
  const s = new THREE.Spherical().setFromVector3(offset);
  s.theta -= azimuth;
  s.phi = THREE.MathUtils.clamp(s.phi - polar, 0.001, Math.PI - 0.001);
  view.camera.position.copy(view.controls.target).add(new THREE.Vector3().setFromSpherical(s));
  view.controls.update();
}

/** Remove a helper group added by name, freeing its geometries and materials (those kept for reuse, with
 * ``userData.keep``, stay). */
function clear(view: ViewerScene, name: string) {
  const old = view.scene.getObjectByName(name);
  if (!old) return;
  view.scene.remove(old);
  old.traverse((o) => {
    if (!(o instanceof THREE.Mesh || o instanceof THREE.Line)) return;
    if (!o.geometry.userData.keep) o.geometry.dispose();
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) if (!m.userData.keep) m.dispose();
  });
}

// the selection's outline: one material, and each mesh's edges worked out once (freed with the mesh's geometry)
const OUTLINE = new THREE.LineBasicMaterial({ color: 0xff9800 });
OUTLINE.userData.keep = true;
const EDGES = new WeakMap<THREE.BufferGeometry, THREE.EdgesGeometry>();
function edgesOf(geometry: THREE.BufferGeometry): THREE.EdgesGeometry {
  let edges = EDGES.get(geometry);
  if (!edges) {
    const made = new THREE.EdgesGeometry(geometry, 40);
    made.userData.keep = true;
    geometry.addEventListener('dispose', () => {
      made.dispose();
      EDGES.delete(geometry);
    });
    EDGES.set(geometry, (edges = made));
  }
  return edges;
}

const HIGHLIGHT = 'workspace-selection';

/** The selection (objects, instances or parts): a white box round each (the slicer's selection corners) and,
 * with ``outline``, their orange outline (the canvas menu's "Outline"). */
export function highlight(view: ViewerScene, targets: Target[], outline = true) {
  clear(view, HIGHLIGHT);
  const group = new THREE.Group();
  group.name = HIGHLIGHT;
  view.model.updateMatrixWorld(true);
  for (const item of targets.flatMap((t) => nodesOf(view, t))) {
    group.add(new THREE.BoxHelper(item, 0xffffff));
    if (!outline) continue;
    item.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const edges = new THREE.LineSegments(edgesOf(o.geometry), OUTLINE);
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

/** The section view, as GLCanvas3D::_get_section_view_plane: a plane facing ``normal`` (towards the camera when
 * it was set) sweeping the bounding sphere of the current plate's objects (volumes_bounding_box(true); all of
 * them when it has none) from the camera side (ratio 0: nothing cut) to the far side (1: all cut); what lies on
 * the camera side of it is cut away. null or ratio 0: no section. */
export function setSection(view: ViewerScene, section: { normal: THREE.Vector3; ratio: number } | null, plate: number | null = null) {
  const on = !!section && section.ratio > 0;
  view.renderer.localClippingEnabled = on;
  let plane: THREE.Plane | null = null;
  const box = new THREE.Box3();
  for (const item of items(view)) if (plate != null && item.userData.plateId === plate) box.expandByObject(item);
  if (box.isEmpty()) box.setFromObject(view.model);
  if (section && on && !box.isEmpty()) {
    const n = section.normal.clone().normalize();
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const r = sphere.radius;
    plane = new THREE.Plane(n.clone().negate(), n.dot(sphere.center) + r - 2 * r * section.ratio); // keeps n·p <= that
  }
  for (const item of items(view)) {
    item.traverse((o) => {
      if (o instanceof THREE.Mesh && o.material instanceof THREE.Material) {
        // the cut shows the inside (both sides drawn); without it the material's own side again
        o.material.userData.side ??= o.material.side;
        o.material.clippingPlanes = plane ? [plane] : null;
        o.material.side = plane ? THREE.DoubleSide : o.material.userData.side;
        o.material.needsUpdate = true;
      }
    });
  }
}

/** Whether the pointer (client coordinates) is over the bed: a right click there opens the plate's menu. */
export function onBed(view: ViewerScene, clientX: number, clientY: number): boolean {
  const rect = view.renderer.domElement.getBoundingClientRect();
  RAY.setFromCamera(new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1), view.camera);
  const p = RAY.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
  return !!p && p.x >= 0 && p.x <= view.bed.x && p.z >= 0 && p.z <= view.bed.y;
}

/** The direction the camera looks from (the section's "Set viewing angle"). */
export function viewDirection(view: ViewerScene): THREE.Vector3 {
  return view.camera.position.clone().sub(view.controls.target).normalize();
}

const PLATES = 'workspace-plates';

/** OrcaSlicer's place of plate ``i`` (from 0) among ``count`` (PartPlateList::compute_shape_position): columns
 * of round(sqrt(count)) (one more when sqrt is above it), each plate 1.2 beds from the last, rows going to the
 * front; in the scene's x / z (the bed's front edge at z = bed depth). */
export function plateOrigin(i: number, count: number, bed: { x: number; y: number }): { x: number; z: number } {
  const root = Math.sqrt(count);
  const cols = root > Math.round(root) ? Math.round(root) + 1 : Math.round(root);
  return { x: (i % cols) * bed.x * 1.2, z: Math.floor(i / cols) * bed.y * 1.2 };
}

/** The other plates' beds beside the first, as OrcaSlicer shows every plate when focus mode is off (none: 0 or 1). */
export function drawPlates(view: ViewerScene, count: number) {
  clear(view, PLATES);
  if (count < 2) return;
  const group = new THREE.Group();
  group.name = PLATES;
  const { x: w, y: d } = view.bed;
  const fill = new THREE.MeshBasicMaterial({ color: 0x00ae42, transparent: true, opacity: 0.08, side: THREE.DoubleSide });
  const edge = new THREE.LineBasicMaterial({ color: 0x5a5a60 });
  for (let i = 1; i < count; i++) {
    const o = plateOrigin(i, count, view.bed);
    const plate = new THREE.Mesh(new THREE.PlaneGeometry(w, d), fill);
    plate.rotation.x = -Math.PI / 2;
    plate.position.set(o.x + w / 2, -0.5, o.z + d / 2);
    const grid = new THREE.GridHelper(Math.max(w, d), Math.round(Math.max(w, d) / 10), 0x3e3e45, 0x34343a);
    grid.scale.set(w / Math.max(w, d), 1, d / Math.max(w, d));
    grid.position.set(o.x + w / 2, -0.45, o.z + d / 2);
    const corners = [0, 0, w, 0, w, d, 0, d, 0, 0].map((v, k) => v + (k % 2 ? o.z : o.x));
    const outlinePoints = [0, 1, 2, 3, 4].map((k) => new THREE.Vector3(corners[2 * k], -0.4, corners[2 * k + 1]));
    group.add(plate, grid, new THREE.Line(new THREE.BufferGeometry().setFromPoints(outlinePoints), edge));
  }
  view.scene.add(group);
}

/** A printer's collision volumes (``bed_exclude_volumes``: "XxY, XxY, ..." full height, "ZMIN..ZMAX;XxY, ..."
 * limited, several split by "|"), in the slicer's mm; unreadable ones left out. */
export function parseVolumes(text: string, height: number): { points: [number, number][]; zmin: number; zmax: number }[] {
  return text
    .split('|')
    .map((v) => {
      const [range, polygon] = v.includes(';') ? v.split(';', 2) : [null, v];
      const [zmin, zmax] = range ? range.split('..').map(Number) : [0, height];
      const points = polygon
        .split(',')
        .map((p) => p.trim().split('x').map(Number))
        .filter((p): p is [number, number] => p.length === 2 && p.every(Number.isFinite));
      return { points, zmin, zmax };
    })
    .filter((v) => v.points.length >= 3 && Number.isFinite(v.zmin) && Number.isFinite(v.zmax) && v.zmax > v.zmin);
}

const COLLISION = 'workspace-collision';

/** "Show all collision volumes" (Ctrl+Shift+K): the printer's collision volumes at their full height. */
export function drawCollisionVolumes(view: ViewerScene, volumes: { points: [number, number][]; zmin: number; zmax: number }[]) {
  clear(view, COLLISION);
  if (!volumes.length) return;
  const group = new THREE.Group();
  group.name = COLLISION;
  const material = new THREE.MeshBasicMaterial({ color: 0xe53935, transparent: true, opacity: 0.18, depthWrite: false, side: THREE.DoubleSide });
  for (const v of volumes) {
    // the slicer's (x, y) is the scene's (x, bed depth - y); the shape is drawn in x / -z and stood up
    const shape = new THREE.Shape(v.points.map(([x, y]) => new THREE.Vector2(x, y - view.bed.y)));
    const solid = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: v.zmax - v.zmin, bevelEnabled: false }), material);
    const prism = new THREE.Group();
    prism.add(solid, new THREE.LineSegments(new THREE.EdgesGeometry(solid.geometry), new THREE.LineBasicMaterial({ color: 0xe53935 })));
    prism.rotation.x = -Math.PI / 2;
    prism.position.y = v.zmin;
    group.add(prism);
  }
  view.scene.add(group);
}

const MEASURE = 'workspace-measure';

/** The measuring line from ``a`` to ``b`` (a dot when only ``a``); none with no points. */
export function drawMeasure(view: ViewerScene, a: THREE.Vector3 | null, b: THREE.Vector3 | null) {
  clear(view, MEASURE);
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
