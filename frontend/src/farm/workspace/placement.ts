// Fork (printfarm): where the workspace puts the instances (OrcaSlicer's Move; Rotate and Scale come next).
// A placement is an instance's new build item ``transform``: 3MF's 12 numbers, which the saved copy writes as
// they are (backend farm/workspace/placement.py). The 3D view shows it without rebuilding the scene: its item
// groups hold vertices already placed by the file's transform, in the viewer's Y-up frame, so an instance
// placed anew is its group moved by the change from the file's transform to the new one.
import * as THREE from 'three';
import type { ViewerScene } from '../../components/ModelViewer';

/** Instance -> its 12 numbers, for the instances placed anew. */
export type Placed = Record<string, number[]>;
export type Spec = number[];

export const IDENTITY: Spec = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0];

export const itemKey = (objectId: string, instance: number) => `${objectId}:${instance}`;

/** 3MF's numbers as a matrix on column vectors: they are stored by columns of the 4x3 matrix, as OrcaSlicer's
 * bbs_get_transform_from_3mf_specs_string reads them. */
export function fromSpec(v: Spec): THREE.Matrix4 {
  return new THREE.Matrix4().set(v[0], v[3], v[6], v[9], v[1], v[4], v[7], v[10], v[2], v[5], v[8], v[11], 0, 0, 0, 1);
}

export function toSpec(m: THREE.Matrix4): Spec {
  const e = m.elements; // column-major
  const round = (x: number) => (Math.abs(x) < 1e-12 ? 0 : Number(x.toFixed(9)));
  return [e[0], e[1], e[2], e[4], e[5], e[6], e[8], e[9], e[10], e[12], e[13], e[14]].map(round);
}

/** The instance's translation in the slicer's coordinates (its position in Orca's Move window). */
export const positionOf = (v: Spec): [number, number, number] => [v[9], v[10], v[11]];

/** ``v`` moved by ``d`` (mm, the slicer's axes): Orca's Move in world coordinates. */
export function moved(v: Spec, d: [number, number, number]): Spec {
  const out = [...v];
  for (let i = 0; i < 3; i++) out[9 + i] = Number((v[9 + i] + d[i]).toFixed(9));
  return out;
}

// the viewer swaps the slicer's Y and Z (ModelViewer createGeometryFromMesh): the same matrix both ways
const SWAP = new THREE.Matrix4().set(1, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1);

const items = (view: ViewerScene) => view.model.children.filter((c) => c.userData.objectId != null);
const keyOf = (item: THREE.Object3D) => itemKey(String(item.userData.objectId), Number(item.userData.instance ?? 0));

/** The build item's transform in the file (ModelViewer keeps it on the item's group). */
export function fileSpec(item: THREE.Object3D): Spec {
  return (item.userData.transform as Spec | undefined) ?? IDENTITY;
}

/** Each item group moved from where the file put it to its placement (or back, when it has none). */
export function applyPlacements(view: ViewerScene, placed: Placed) {
  for (const item of items(view)) {
    const spec = placed[keyOf(item)];
    item.matrixAutoUpdate = false;
    if (!spec) item.matrix.identity();
    else {
      const change = fromSpec(spec).multiply(fromSpec(fileSpec(item)).invert());
      item.matrix.copy(SWAP).multiply(change).multiply(SWAP);
    }
    item.matrixWorldNeedsUpdate = true;
  }
  view.model.updateMatrixWorld(true);
}

/** A move in the scene (world, from ``a`` to ``b``) as a move on the slicer's axes. */
export function sceneToSlicer(view: ViewerScene, a: THREE.Vector3, b: THREE.Vector3): [number, number, number] {
  const d = view.model.worldToLocal(b.clone()).sub(view.model.worldToLocal(a.clone())); // the slicer's x, z, y
  return [d.x, d.z, d.y];
}

/** The instances' lowest point above the bed (mm, the slicer's Z; below it: negative); null without any. */
export function heightAboveBed(view: ViewerScene, keys: string[]): number | null {
  let low = Infinity;
  const v = new THREE.Vector3();
  const toModel = new THREE.Matrix4();
  view.model.updateMatrixWorld(true);
  const fromWorld = view.model.matrixWorld.clone().invert();
  for (const item of items(view)) {
    if (!keys.includes(keyOf(item))) continue;
    item.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const pos = (o.geometry as THREE.BufferGeometry).getAttribute('position');
      toModel.copy(fromWorld).multiply(o.matrixWorld);
      for (let i = 0; i < pos.count; i++) low = Math.min(low, v.fromBufferAttribute(pos, i).applyMatrix4(toModel).y);
    });
  }
  return Number.isFinite(low) ? low : null;
}

/** The instances a selection moves: one instance, or every instance of the objects selected. */
export function selectedItems(view: ViewerScene | null, objectIds: string[], instance: number | null | undefined): string[] {
  if (!view) return [];
  return items(view)
    .filter((i) => objectIds.includes(String(i.userData.objectId)) && (instance == null || Number(i.userData.instance ?? 0) === instance))
    .map(keyOf);
}

/** The instance's transform now: its placement, else the file's (null: not in the scene). */
export function currentSpec(view: ViewerScene, placed: Placed, key: string): Spec | null {
  const item = items(view).find((i) => keyOf(i) === key);
  return item ? (placed[key] ?? fileSpec(item)) : null;
}

/** ``placed`` with the instances ``keys`` moved by ``d``; one back where the file has it is no placement any more. */
export function withMoved(view: ViewerScene, placed: Placed, keys: string[], d: [number, number, number]): Placed {
  const next = { ...placed };
  for (const item of items(view)) {
    const key = keyOf(item);
    if (!keys.includes(key)) continue;
    const file = fileSpec(item);
    const spec = moved(placed[key] ?? file, d);
    if (spec.every((x, i) => Math.abs(x - file[i]) < 1e-9)) delete next[key];
    else next[key] = spec;
  }
  return next;
}

/** The placements to save: each instance placed anew, as the copy takes it. */
export function placementsToSave(placed: Placed): { object_id: string; instance: number; matrix: number[] }[] {
  return Object.entries(placed).map(([key, matrix]) => {
    const [object_id, instance] = key.split(':');
    return { object_id, instance: Number(instance), matrix };
  });
}
