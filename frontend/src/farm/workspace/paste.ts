// Fork (printfarm): where OrcaSlicer pastes a copy (Selection::paste_objects_from_clipboard, GLCanvas3D::get_empty_cells
// and get_nearest_empty_cell), ported as Orca 2.5.0 has it. Pure functions, in the slicer's X / Y (mm).
//
// get_empty_cells keeps a cell for every instance whose hull doesn't hold it (not only the cells no instance holds):
// with two instances or more, nearly every cell is kept and the nearest is the copy's own place. Orca's Clone then
// relies on "Auto arrange plate after cloning". Ported as it is.
export type Pt = [number, number];
export interface Box2 {
  min: Pt;
  max: Pt;
}

/** The convex hull of points (x, y, z flat), counter-clockwise (Andrew's monotone chain). */
export function convexHull(points: ArrayLike<number>): Pt[] {
  const pts: Pt[] = [];
  for (let i = 0; i + 1 < points.length; i += 3) pts.push([points[i], points[i + 1]]);
  pts.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const half = (list: Pt[]) => {
    const out: Pt[] = [];
    for (const p of list) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop();
      out.push(p);
    }
    out.pop();
    return out;
  };
  return [...half(pts), ...half([...pts].reverse())];
}

/** Polygon::contains: whether ``p`` is inside the (convex, counter-clockwise) hull. */
export function hullContains(hull: Pt[], p: Pt): boolean {
  if (hull.length < 3) return false;
  for (let i = 0; i < hull.length; i++) {
    const a = hull[i];
    const b = hull[(i + 1) % hull.length];
    if ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) < 0) return false;
  }
  return true;
}

/** GLCanvas3D::get_empty_cells: the plate's cells of ``step`` on a grid through ``start``, each kept once per instance
 * whose hull doesn't hold it, nearest to ``start`` first (the plate's middle when ``start`` is below and left of 0). */
export function emptyCells(start: Pt, step: Pt, plate: Box2, hulls: Pt[][]): Pt[] {
  const minX = start[0] - step[0] * Math.trunc((start[0] - plate.min[0]) / step[0]);
  const minY = start[1] - step[1] * Math.trunc((start[1] - plate.min[1]) / step[1]);
  const cells: Pt[] = [];
  for (let x = minX; x < plate.max[0] - step[0] / 2; x += step[0]) {
    for (let y = minY; y < plate.max[1] - step[1] / 2; y += step[1]) cells.push([x, y]);
  }
  const out: Pt[] = [];
  for (const hull of hulls) for (const c of cells) if (!hullContains(hull, c)) out.push(c);
  const from: Pt = start[0] < 0 && start[1] < 0 ? [(plate.min[0] + plate.max[0]) / 2, (plate.min[1] + plate.max[1]) / 2] : start;
  const d = (c: Pt) => Math.hypot(c[0] - from[0], c[1] - from[1]);
  // cells as far: in the grid's order (X, then Y); Orca's std::sort leaves their order open
  return out.sort((a, b) => d(a) - d(b));
}

/** get_nearest_empty_cell: the first empty cell, else ``start`` moved by ``offset`` on both axes. */
export function nearestEmptyCell(start: Pt, step: Pt, plate: Box2, hulls: Pt[][], offset: number): Pt {
  return emptyCells(start, step, plate, hulls)[0] ?? [start[0] + offset, start[1] + offset];
}

/** One object on the clipboard: its first instance's hull and box (where it is now) and its instances' transforms. */
export interface Clip {
  hull: Pt[];
  box: Box2;
  specs: number[][];
}

/** Selection::paste_objects_from_clipboard: each clipboard object's instances, all at the place Orca picks (an empty
 * cell near the original on the current plate, else near the plate's middle); several objects keep their spread and
 * move together by the selection's size. ``hulls``: every instance on the model now (the copies' are added to it, for
 * the next paste). Z is the original's: changed_objects puts a copy back on the bed. */
export function pastePlacements(clip: Clip[], plate: Box2, hulls: Pt[][], offset: number): number[][][] {
  const intersects = (b: Box2) => b.min[0] <= plate.max[0] && b.max[0] >= plate.min[0] && b.min[1] <= plate.max[1] && b.max[1] >= plate.min[1];
  const middle: Pt = [(plate.min[0] + plate.max[0]) / 2, (plate.min[1] + plate.max[1]) / 2];
  let shiftAll: Pt = [0, 0];
  if (clip.length > 1) {
    const min: Pt = [Math.min(...clip.map((c) => c.box.min[0])), Math.min(...clip.map((c) => c.box.min[1]))];
    const max: Pt = [Math.max(...clip.map((c) => c.box.max[0])), Math.max(...clip.map((c) => c.box.max[1]))];
    const size: Pt = [max[0] - min[0], max[1] - min[1]];
    shiftAll = size[0] < size[1] ? [size[0], 0] : [0, size[1]];
  }
  let cellAll: Pt = [0, 0];
  const out: number[][][] = [];
  clip.forEach((c, i) => {
    const center: Pt = [(c.box.min[0] + c.box.max[0]) / 2, (c.box.min[1] + c.box.max[1]) / 2];
    const step: Pt = [c.box.max[0] - c.box.min[0] + 1, c.box.max[1] - c.box.min[1] + 1];
    const inCurrent = intersects(c.box);
    const start = inCurrent ? center : middle;
    const first = c.specs[0];
    const startOffset: Pt = inCurrent ? [first[9], first[10]] : middle;
    let at: Pt;
    if (shiftAll[0] !== 0 || shiftAll[1] !== 0) {
      if (i === 0) cellAll = nearestEmptyCell(start, step, plate, hulls, offset);
      const shift: Pt = [first[9] - clip[0].specs[0][9], first[10] - clip[0].specs[0][10]];
      at = [shiftAll[0] + cellAll[0] + shift[0], shiftAll[1] + cellAll[1] + shift[1]];
    } else {
      const cell = nearestEmptyCell(start, step, plate, hulls, offset);
      at = [cell[0] + startOffset[0] - start[0], cell[1] + startOffset[1] - start[1]];
    }
    // every instance at that offset (inst->set_offset(displacement)), its rotation and scale kept
    out.push(c.specs.map((s) => [...s.slice(0, 9), at[0], at[1], s[11]]));
    hulls.push(c.hull.map(([x, y]) => [x + at[0] - first[9], y + at[1] - first[10]] as Pt));
  });
  return out;
}
