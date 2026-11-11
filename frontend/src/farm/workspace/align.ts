// Fork (printfarm): OrcaSlicer's align and distribute (Gizmos/GLGizmoAlignment.cpp, v2.5.0), the Move window's lower
// half and the object menu's Align/Distribute. Within the selection: the instances selected, or the parts of one
// instance (the parts selected with Alt, or a lone instance's model parts); to the parent: the plate holding the
// instances (X and Y only), or the instance holding the parts. Boxes are taken along the World axes, or the instance's
// own in Object coordinates (reference_basis); the moves are in the slicer's coordinates (mm).
import * as THREE from 'three';
import type { ViewerScene } from '../../components/ModelViewer';
import { type Placed, currentSpec } from './placement';
import { boxOf, rotationOf, targetKey } from './transforms';
import { toScene } from './transformGizmos';
import { plateOrigin } from './viewTools';

export type AlignKind = 'min' | 'center' | 'max' | 'distribute';
export interface AlignType {
  axis: 0 | 1 | 2;
  kind: AlignKind;
}

export interface AlignSelection {
  view: ViewerScene;
  placed: Placed;
  /** the instances selected (objectId:instance) */
  instances: string[];
  /** the parts selected with Alt (objectId:instance/partId): Orca's Volume mode */
  parts: string[];
  /** a model part, not a modifier, negative volume, support blocker or enforcer */
  modelPart: (objectId: string, partId: string) => boolean;
  /** Object coordinates: along the instance's own axes */
  objectCs: boolean;
  autoDrop: (objectId: string) => boolean;
  /** the plate holding an instance (its place among the plates), or -1 */
  plateOf: (instance: string) => number;
  plateCount: number;
}

/** The order of Orca's buttons and menu items, with their icons (GLGizmosManager's IC_ALIGN_* / IC_DISTRIBUTE_*). */
export const ALIGN_BUTTONS: { type: AlignType; label: string; icon: string }[] = [
  { type: { axis: 0, kind: 'min' }, label: 'Align left', icon: 'align_x_min' },
  { type: { axis: 0, kind: 'center' }, label: 'Align left-right center', icon: 'align_x_center' },
  { type: { axis: 0, kind: 'max' }, label: 'Align right', icon: 'align_x_max' },
  { type: { axis: 1, kind: 'min' }, label: 'Align front', icon: 'align_y_min' },
  { type: { axis: 1, kind: 'center' }, label: 'Align front-back center', icon: 'align_y_center' },
  { type: { axis: 1, kind: 'max' }, label: 'Align back', icon: 'align_y_max' },
  { type: { axis: 2, kind: 'min' }, label: 'Align bottom', icon: 'align_z_min' },
  { type: { axis: 2, kind: 'center' }, label: 'Align top-bottom center', icon: 'align_z_center' },
  { type: { axis: 2, kind: 'max' }, label: 'Align top', icon: 'align_z_max' },
];
export const DISTRIBUTE_BUTTONS: { type: AlignType; label: string; icon: string }[] = [
  { type: { axis: 0, kind: 'distribute' }, label: 'Distribute left-right', icon: 'distribute_x' },
  { type: { axis: 1, kind: 'distribute' }, label: 'Distribute front-back', icon: 'distribute_y' },
  { type: { axis: 2, kind: 'distribute' }, label: 'Distribute top-bottom', icon: 'distribute_z' },
];
/** " (-X)", " (X)", " (+X)" after the label, as Orca writes it */
export const alignSuffix = (t: AlignType) => ` (${t.kind === 'min' ? '-' : t.kind === 'max' ? '+' : ''}${'XYZ'[t.axis]})`;

const instanceOf = (target: string) => target.split('/')[0];

/** is_parts_selection: the parts of one instance; to the parent only the parts selected with Alt count. */
export const isPartsSelection = (sel: AlignSelection, toParent: boolean) => sel.parts.length > 0 || (!toParent && sel.instances.length === 1);

/** The model parts of an instance, as targets; the instance itself when its object has no parts of its own. */
export function modelTargets(view: ViewerScene, instance: string, modelPart: (objectId: string, partId: string) => boolean): string[] {
  const item = view.model.children.find((c) => c.userData.objectId != null && `${c.userData.objectId}:${Number(c.userData.instance ?? 0)}` === instance);
  if (!item) return [];
  const objectId = String(item.userData.objectId);
  const inst = Number(instance.split(':')[1]);
  const groups = item.children.filter((g) => g.userData.partId != null);
  if (!groups.length) return [instance];
  return groups.filter((g) => modelPart(objectId, String(g.userData.partId))).map((g) => targetKey(objectId, inst, String(g.userData.partId)));
}

const modelParts = (sel: AlignSelection, instance: string) => modelTargets(sel.view, instance, sel.modelPart).filter((t) => t.includes('/'));

/** get_items_count: what the operation moves (none, with the reason when Orca gives one). */
export function alignTargets(sel: AlignSelection, type: AlignType, toParent: boolean): { targets: string[]; why?: string } {
  if (isPartsSelection(sel, toParent)) return { targets: sel.parts.length ? sel.parts : modelParts(sel, sel.instances[0]) };
  if (!sel.instances.length) return { targets: [] };
  const z = type.axis === 2;
  // the plate gives no Z target, and aligning to its bottom would duplicate Drop
  if (z && toParent) return { targets: [], why: 'Aligning to the plate is available in X and Y only' };
  let plate = -1;
  for (const key of sel.instances) {
    // Auto-drop would put a raised object back on the bed and leave a lowered one sunk
    if (z && sel.autoDrop(key.split(':')[0])) return { targets: [], why: 'Turn off Auto-Drop for the selected objects to move them in Z' };
    if (toParent) {
      const p = sel.plateOf(key);
      if (p === -1 || (plate !== -1 && p !== plate)) return { targets: [], why: 'All selected objects must be on the same plate' };
      plate = p;
    }
  }
  return { targets: sel.instances };
}

/** can_align / can_distribute: two items to align within, one to align to the parent, three to distribute. */
export const canAlign = (sel: AlignSelection, type: AlignType, toParent: boolean) =>
  type.kind === 'distribute' ? alignTargets(sel, type, false).targets.length >= 3 : alignTargets(sel, type, toParent).targets.length >= (toParent ? 1 : 2);

/** reference_basis: the axes boxes are taken along (the plate's are the World's). */
export function alignBasis(sel: AlignSelection, toParent: boolean): THREE.Matrix4 {
  const first = sel.parts[0] ?? sel.instances[0];
  if (!sel.objectCs || !first || (toParent && !isPartsSelection(sel, true))) return new THREE.Matrix4();
  const spec = currentSpec(sel.view, sel.placed, instanceOf(first));
  return spec ? rotationOf(spec) : new THREE.Matrix4();
}

const boxIn = (sel: AlignSelection, targets: string[], basis: THREE.Matrix4) => boxOf(sel.view, sel.placed, targets, 'object', basis);

/** get_parent_box: the instance holding the parts, or the plate holding the instances (0.1 mm in from its edges, up to
 * their top). */
export function parentBox(sel: AlignSelection, basis: THREE.Matrix4): THREE.Box3 | null {
  if (isPartsSelection(sel, true)) return boxIn(sel, modelParts(sel, instanceOf(sel.parts[0])), basis);
  const plate = sel.instances.length ? sel.plateOf(sel.instances[0]) : -1;
  const top = boxOf(sel.view, sel.placed, sel.instances);
  if (plate < 0 || !top) return null;
  // shortcut: the bed from its origin, as the 3D view draws it; a printable_area off the origin would need reading here
  const o = plateOrigin(plate, sel.plateCount, sel.view.bed);
  const box = new THREE.Box3(new THREE.Vector3(o.x, -o.z, 0), new THREE.Vector3(o.x + sel.view.bed.x, -o.z + sel.view.bed.y, top.max.z));
  return box.expandByScalar(-0.1);
}

const coord = (b: THREE.Box3, axis: number, kind: AlignKind) =>
  kind === 'min' ? b.min.getComponent(axis) : kind === 'max' ? b.max.getComponent(axis) : (b.min.getComponent(axis) + b.max.getComponent(axis)) / 2;

/** The operation's moves: each target's, in the slicer's coordinates; null when it can't run. */
export function alignMoves(sel: AlignSelection, type: AlignType, toParent: boolean): Record<string, [number, number, number]> | null {
  const distribute = type.kind === 'distribute';
  const parent = toParent && !distribute;
  if (!canAlign(sel, type, parent)) return null;
  const { targets } = alignTargets(sel, type, parent);
  const basis = alignBasis(sel, parent);
  const { axis, kind } = type;
  const box = (ts: string[]) => boxIn(sel, ts, basis)!;
  const out: Record<string, [number, number, number]> = {};
  const put = (target: string, offset: number) => {
    if (Math.abs(offset) <= 1e-6) return;
    const d = new THREE.Vector3().setComponent(axis, offset).applyMatrix4(basis);
    out[target] = [d.x, d.y, d.z];
  };
  if (parent) {
    // the selection moves as one, its box to the parent's
    const to = parentBox(sel, basis);
    if (!to) return null;
    const offset = coord(to, axis, kind) - coord(box(targets), axis, kind);
    for (const t of targets) put(t, offset);
  } else if (kind === 'center') {
    const c = coord(box(targets), axis, 'center');
    for (const t of targets) put(t, c - coord(box([t]), axis, 'center'));
  } else if (!distribute) {
    const at = targets.map((t) => coord(box([t]), axis, kind));
    const ref = kind === 'min' ? Math.min(...at) : Math.max(...at);
    targets.forEach((t, i) => put(t, ref - at[i]));
  } else {
    // the outermost two stay; the centres between them evenly spaced
    const order = targets.map((t) => ({ t, b: box([t]) })).map((e) => ({ ...e, c: coord(e.b, axis, 'center') })).sort((a, b) => a.c - b.c);
    let first = order[0].c;
    let last = order[order.length - 1].c;
    if (isPartsSelection(sel, false)) {
      // parts: from the selection's box, by the outer parts' widths (distribute_objects_generic's parts branch)
      const all = box(targets);
      const width = (b: THREE.Box3) => b.max.getComponent(axis) - b.min.getComponent(axis);
      first = all.min.getComponent(axis) + width(order[0].b) / 2;
      last = all.max.getComponent(axis) - width(order[order.length - 1].b) / 2;
    }
    const step = (last - first) / (order.length - 1);
    for (let i = 1; i < order.length - 1; i++) put(order[i].t, first + i * step - order[i].c);
  }
  return out;
}

const NAME = 'workspace-align-preview';

function clear(view: ViewerScene) {
  const old = view.scene.getObjectByName(NAME);
  if (!old) return;
  old.traverse((o) => {
    if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
      o.geometry.dispose();
      (o.material as THREE.Material).dispose();
    }
  });
  view.scene.remove(old);
}

/** GLGizmoMove3D::render_align_preview while a button is hovered: the box the operation aligns to (teal) and a plane
 * across it where the items will line up; distributing shows up to six evenly spaced planes. None: cleared. */
export function drawAlignPreview(view: ViewerScene, sel: AlignSelection | null, type: AlignType | null, toParent: boolean) {
  clear(view);
  if (!sel || !type) return;
  const distribute = type.kind === 'distribute';
  const parent = toParent && !distribute;
  if (!canAlign(sel, type, parent)) return;
  const basis = alignBasis(sel, parent);
  const box = parent ? parentBox(sel, basis) : boxIn(sel, alignTargets(sel, type, false).targets, basis);
  if (!box) return;
  view.model.updateMatrixWorld(true);
  const at = (p: THREE.Vector3) => toScene(view, p.clone().applyMatrix4(basis));
  const group = new THREE.Group();
  group.name = NAME;
  const { min: a, max: b } = box;
  const corners = [0, 1, 2, 3, 4, 5, 6, 7].map((i) => at(new THREE.Vector3(i & 1 ? b.x : a.x, i & 2 ? b.y : a.y, i & 4 ? b.z : a.z)));
  const edges = [0, 1, 2, 3, 4, 5, 6, 7, 0, 2, 1, 3, 4, 6, 5, 7, 0, 4, 1, 5, 2, 6, 3, 7].map((i) => corners[i]);
  const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(edges), new THREE.LineBasicMaterial({ color: 0x009688, depthTest: false }));
  lines.renderOrder = 998;
  group.add(lines);
  const axis = type.axis;
  const size = box.getSize(new THREE.Vector3()).getComponent(axis);
  const from = a.getComponent(axis);
  const count = Math.min(alignTargets(sel, type, false).targets.length, 6);
  const positions = distribute
    ? Array.from({ length: count }, (_, i) => from + ((i + 1) * size) / (count + 1))
    : [from + (type.kind === 'min' ? 0 : type.kind === 'max' ? 1 : 0.5) * size];
  const u = (axis + 1) % 3;
  const v = (axis + 2) % 3;
  for (const p of positions) {
    const quad = [[0, 0], [1, 0], [1, 1], [0, 1]].map(([i, j]) => {
      const c = new THREE.Vector3();
      c.setComponent(axis, p);
      c.setComponent(u, (i ? b : a).getComponent(u));
      c.setComponent(v, (j ? b : a).getComponent(v));
      return at(c);
    });
    const mesh = new THREE.Mesh(
      new THREE.BufferGeometry().setFromPoints([quad[0], quad[1], quad[2], quad[0], quad[2], quad[3]]),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.5, side: THREE.DoubleSide, depthWrite: false }),
    );
    mesh.renderOrder = 997;
    group.add(mesh);
  }
  view.scene.add(group);
}
