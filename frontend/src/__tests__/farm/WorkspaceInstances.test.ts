/**
 * Printfarm (fork): 3MF Workspace, batch F2. OrcaSlicer's instances and copies: where a paste lands (paste.ts, Orca's
 * get_empty_cells quirk included), the instances and objects the operations add, the menus' entries, the undo step.
 */
import { describe, it, expect } from 'vitest';
import type { WorkspaceObject } from '../../farm/workspace/api';
import { draftHistory, type DraftHistory } from '../../farm/workspace/draft';
import { editMenu, type MenuActions, type MenuState } from '../../farm/workspace/menuBar';
import { instanceMenu, multiMenu, objectMenu, partMenu } from '../../farm/workspace/menus';
import { type ObjectOp, addedItems, nextNewId, withObjectOps } from '../../farm/workspace/objectOps';
import { type Box2, type Pt, convexHull, emptyCells, hullContains, nearestEmptyCell, pastePlacements } from '../../farm/workspace/paste';

const PLATE: Box2 = { min: [0, 0], max: [256, 256] };
const square = (cx: number, cy: number, half: number): Pt[] => [
  [cx - half, cy - half],
  [cx + half, cy - half],
  [cx + half, cy + half],
  [cx - half, cy + half],
];
const at = (x: number, y: number, z = 5) => [1, 0, 0, 0, 1, 0, 0, 0, 1, x, y, z];

describe('where Orca pastes a copy (paste.ts)', () => {
  it('works out a hull and what it holds', () => {
    // a cube's corners (x, y, z flat) and a point inside: the hull is the square
    const hull = convexHull([0, 0, 0, 10, 0, 0, 10, 10, 0, 0, 10, 0, 5, 5, 3, 0, 0, 9]);
    expect(hull).toHaveLength(4);
    expect(hullContains(hull, [5, 5])).toBe(true);
    expect(hullContains(hull, [11, 5])).toBe(false);
  });

  it('puts the copy of an object alone on the plate one cell away', () => {
    const clip = [{ hull: square(50, 50, 5), box: { min: [45, 45], max: [55, 55] } as Box2, specs: [at(50, 50)] }];
    const [placed] = pastePlacements(clip, PLATE, [square(50, 50, 5)], 12.8);
    expect(placed).toEqual([at(39, 50)]); // the cells 11 mm away, the grid's first
  });

  it("keeps Orca's get_empty_cells: a cell any instance leaves free counts, so with two the copy lands on its original", () => {
    const hulls = [square(50, 50, 5), square(200, 200, 5)];
    expect(emptyCells([50, 50], [11, 11], PLATE, hulls)[0]).toEqual([50, 50]);
    const clip = [{ hull: square(50, 50, 5), box: { min: [45, 45], max: [55, 55] } as Box2, specs: [at(50, 50)] }];
    expect(pastePlacements(clip, PLATE, hulls, 12.8)[0]).toEqual([at(50, 50)]);
  });

  it('falls back to the start moved by the offset when the plate has no cell', () => {
    expect(nearestEmptyCell([5, 5], [11, 11], { min: [0, 0], max: [10, 10] }, [], 12.8)).toEqual([17.8, 17.8]);
  });

  it("moves several objects together by the selection's size, every instance to its object's place", () => {
    const clip = [
      { hull: square(5, 10, 5), box: { min: [0, 0], max: [10, 20] } as Box2, specs: [at(5, 10), at(100, 100)] },
      { hull: square(25, 10, 5), box: { min: [20, 0], max: [30, 20] } as Box2, specs: [at(25, 10)] },
    ];
    const out = pastePlacements(clip, PLATE, [square(5, 10, 5), square(25, 10, 5)], 12.8);
    // the selection is 30 x 20: moved by its smaller side's size along Y; each instance of an object at one place
    expect(out).toEqual([[at(5, 30), at(5, 30)], [at(25, 30)]]);
  });
});

describe('the instances and objects the operations add (objectOps.ts)', () => {
  const ops: ObjectOp[] = [
    { op: 'add_instance', object_id: '2', matrix: at(90, 50) },
    { op: 'copy', object_id: '4', new_id: '46', matrices: [at(10, 10), at(20, 20)] },
    { op: 'copy', object_id: '46', new_id: '47', matrices: [at(30, 30)] },
    { op: 'add_instance', object_id: '46', matrix: at(40, 40) },
    { op: 'rename', object_id: '47', value: 'Third' },
  ];

  it("numbers them after the file's and shows a copy of a copy with the first object's meshes", () => {
    const counts: Record<string, number> = { '2': 1, '4': 2 };
    const added = addedItems(ops, (id) => counts[id] ?? 0);
    expect(added.map((a) => [a.key, a.source])).toEqual([
      ['2:1', '2'],
      ['46:0', '4'],
      ['46:1', '4'],
      ['47:0', '4'],
      ['46:2', '4'],
    ]);
    expect(nextNewId(ops, 46)).toBe(48);
    expect(nextNewId([], 46)).toBe(46);
  });

  it('lists a copy as its object under its new id, changed apart afterwards', () => {
    const objects: WorkspaceObject[] = [
      { id: '4', name: 'Cube B', extruder: '1', parts: [{ id: '3', name: 'Body', type: 'normal_part' }], overrides: [] },
    ];
    const { objects: now } = withObjectOps(objects, [], ops);
    expect(now.map((o) => [o.id, o.name])).toEqual([
      ['4', 'Cube B'],
      ['46', 'Cube B'],
      ['47', 'Third'],
    ]);
  });
});

describe('the menus', () => {
  const tr = (s: string) => s;
  const base = { select: () => {}, zoom: () => {}, info: () => {}, settings: () => {} };

  it("fills the object menu's Clone and Instances, greying what can't be done", () => {
    const calls: string[] = [];
    const items = objectMenu(tr, 'Del', true, {
      ...base,
      clone: () => calls.push('clone'),
      instances: { add: () => calls.push('add'), setNumber: () => calls.push('number'), split: () => calls.push('split'), full: true },
    });
    items.find((i) => i.label === 'Clone')!.onClick!();
    const sub = items.find((i) => i.label === 'Instances')!.items!;
    expect(sub.map((i) => i.label)).toEqual([
      'Add instance',
      'Remove instance',
      'Set number of instances...',
      'Fill bed with instances...',
      'Set as Individual Objects',
    ]);
    expect(sub.map((i) => !!i.onClick)).toEqual([true, false, true, false, true]);
    sub[0].onClick!();
    sub[4].onClick!();
    expect(calls).toEqual(['clone', 'add', 'split']);
    // without the actions (a file that can't take new objects): greyed, as before
    expect(objectMenu(tr, 'Del', true, base).find((i) => i.label === 'Clone')?.onClick).toBeUndefined();
  });

  it("fills Transform with Orca's Mirror along X / Y / Z for an object and a part (batch F3)", () => {
    const axes: number[] = [];
    const mirror = (axis: 0 | 1 | 2) => axes.push(axis);
    const obj = objectMenu(tr, 'Del', true, { ...base, mirror }).find((i) => i.label === 'Transform')!.items!;
    expect(obj.map((i) => [i.label, !!i.onClick])).toEqual([
      ['Mirror:', false],
      ['Along X Axis', true],
      ['Along Y Axis', true],
      ['Along Z Axis', true],
      ['Scale to build volume', false],
    ]);
    obj.slice(1, 4).forEach((i) => i.onClick!());
    const part = partMenu(tr, 'Del', { select: () => {}, zoom: () => {}, settings: () => {}, mirror }).find((i) => i.label === 'Transform')!;
    part.items![2].onClick!();
    expect(axes).toEqual([0, 1, 2, 1]);
    // without the action: greyed, as before
    expect(objectMenu(tr, 'Del', true, base).find((i) => i.label === 'Transform')).toEqual({ label: 'Transform', submenu: true });
  });

  it("gives the object menu's Center and Drop their moves, greyed when there is none to make", () => {
    const calls: string[] = [];
    const items = objectMenu(tr, 'Del', true, { ...base, center: () => calls.push('center'), drop: () => calls.push('drop') });
    items.find((i) => i.label === 'Center')!.onClick!();
    items.find((i) => i.label === 'Drop')!.onClick!();
    expect(calls).toEqual(['center', 'drop']);
    const none = objectMenu(tr, 'Del', true, base);
    expect([none.find((i) => i.label === 'Center')?.onClick, none.find((i) => i.label === 'Drop')?.onClick]).toEqual([undefined, undefined]);
  });

  it('gives Copy / Paste Process Settings their actions in the object, part and multi-selection menus', () => {
    const calls: string[] = [];
    const settings = { copySettings: () => calls.push('copy'), pasteSettings: () => calls.push('paste') };
    const pick = (items: { label: string; onClick?: () => void }[], label: string) => items.find((i) => i.label === label)!;
    const obj = objectMenu(tr, 'Del', true, { ...base, ...settings });
    pick(obj, 'Copy Process Settings').onClick!();
    pick(obj, 'Paste Process Settings').onClick!();
    const part = partMenu(tr, 'Del', { select: () => {}, zoom: () => {}, settings: () => {}, ...settings });
    pick(part, 'Paste Process Settings').onClick!();
    const multi = multiMenu(tr, 'Del', true, { zoom: () => {}, deselect: () => {}, pasteSettings: settings.pasteSettings });
    expect(pick(multi, 'Copy Process Settings').onClick).toBeUndefined(); // Copy takes one object or part
    pick(multi, 'Paste Process Settings').onClick!();
    expect(calls).toEqual(['copy', 'paste', 'paste', 'paste']);
    expect(pick(objectMenu(tr, 'Del', true, base), 'Paste Process Settings').onClick).toBeUndefined();
  });

  it("gives an instance's Set as An Individual Object its action", () => {
    let split = false;
    instanceMenu(tr, 1, () => (split = true))[0].onClick!();
    expect(split).toBe(true);
    expect(instanceMenu(tr, 1)[0].onClick).toBeUndefined();
  });

  it("enables the Edit menu's Cut, Copy, Clone Selected and, with something copied, Paste", () => {
    const actions = { cut: () => {}, copy: () => {}, paste: () => {}, clone: () => {} } as unknown as MenuActions;
    const menu = (canPaste: boolean) =>
      editMenu(tr, { phase: () => 'later', later: 'later' }, { canCopy: true, canPaste } as MenuState, actions, { resetAll: 'Reset' });
    const on = (m: ReturnType<typeof menu>, label: string) => !!m.find((i) => i.label === label)?.onClick;
    expect(['Cut', 'Copy', 'Paste', 'Clone Selected'].map((l) => on(menu(false), l))).toEqual([true, true, false, true]);
    expect(on(menu(true), 'Paste')).toBe(true);
  });
});

describe('the undo history', () => {
  it("makes an addition one step with its plates, places and the copy's own settings", () => {
    let h: DraftHistory = { draft: {}, past: [], future: [], last: null };
    h = draftHistory(h, {
      type: 'objects',
      objectOps: [{ op: 'copy', object_id: '4', new_id: '46', matrices: [at(10, 10)] }],
      onPlate: { '46:0': 1 },
      placed: { '46/3': at(1, 1) },
      own: { '46': { wall_loops: '4' } },
    });
    expect([h.onPlate, h.placed, h.own]).toEqual([{ '46:0': 1 }, { '46/3': at(1, 1) }, { '46': { wall_loops: '4' } }]);
    h = draftHistory(h, { type: 'undo' });
    expect(h.objectOps ?? []).toEqual([]);
    expect(h.onPlate ?? {}).toEqual({});
    expect(h.own ?? {}).toEqual({});
  });
});
