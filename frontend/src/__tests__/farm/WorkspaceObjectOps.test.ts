/**
 * Printfarm (fork): 3MF Workspace, batch F1. OrcaSlicer's Delete, Delete All, the plate's Delete All, Printable, Auto
 * Drop and Rename: the objects and plates as the operations leave them, Orca's name rule, the menus' entries, the
 * undo step.
 */
import { describe, it, expect } from 'vitest';
import type { WorkspaceObject, WorkspacePlate } from '../../farm/workspace/api';
import { draftHistory, type DraftHistory } from '../../farm/workspace/draft';
import { editMenu, type MenuActions, type MenuState } from '../../farm/workspace/menuBar';
import { multiMenu, objectMenu, plateMenu } from '../../farm/workspace/menus';
import { type ObjectOp, deleted, isDeleted, nameRefusal, plateDeletions, printableOf, withObjectOps } from '../../farm/workspace/objectOps';

const OBJECTS: WorkspaceObject[] = [
  { id: '2', name: 'Cube A', extruder: '1', parts: [{ id: '1', name: 'Cube A', type: 'normal_part' }], overrides: [] },
  {
    id: '4',
    name: 'Cube B',
    extruder: '1',
    auto_drop: true,
    parts: [
      { id: '3', name: 'Body', type: 'normal_part' },
      { id: '5', name: 'Box', type: 'modifier_part' },
    ],
    overrides: [],
  },
];
const PLATES: WorkspacePlate[] = [
  { index: 1, name: 'Left', object_ids: ['2'], instances: [{ object_id: '2', instance_id: '0' }] },
  {
    index: 2,
    name: null,
    object_ids: ['4'],
    instances: [
      { object_id: '4', instance_id: '0' },
      { object_id: '4', instance_id: '1' },
    ],
  },
];

describe('the objects and plates as the operations leave them (objectOps.ts)', () => {
  it('deletes an instance, and an object with none left', () => {
    const one = withObjectOps(OBJECTS, PLATES, [{ op: 'delete', object_id: '4', instance: 0 }]);
    expect(one.objects.map((o) => o.id)).toEqual(['2', '4']);
    expect(one.plates[1].instances).toEqual([{ object_id: '4', instance_id: '1' }]);
    const both = withObjectOps(OBJECTS, PLATES, [
      { op: 'delete', object_id: '4', instance: 0 },
      { op: 'delete', object_id: '4', instance: 1 },
    ]);
    expect(both.objects.map((o) => o.id)).toEqual(['2']);
    expect(both.plates[1].object_ids).toEqual([]);
    const whole = withObjectOps(OBJECTS, PLATES, [{ op: 'delete', object_id: '2' }]);
    expect(whole.objects.map((o) => o.id)).toEqual(['4']);
    expect(whole.plates[0]).toMatchObject({ object_ids: [], instances: [] });
    expect(isDeleted(deleted([{ op: 'delete', object_id: '2' }]), '2', 5)).toBe(true);
  });

  it('renames an object (its one part too, as update_name_in_model) or a part, and sets Auto Drop', () => {
    const { objects } = withObjectOps(OBJECTS, PLATES, [
      { op: 'rename', object_id: '2', value: 'Bracket' },
      { op: 'rename', object_id: '4', part_id: '5', value: 'Lid' },
      { op: 'auto_drop', object_id: '4', value: false },
    ]);
    expect(objects[0]).toMatchObject({ name: 'Bracket', parts: [{ name: 'Bracket' }] });
    expect(objects[1].parts.map((p) => p.name)).toEqual(['Body', 'Lid']);
    expect(objects[1].name).toBe('Cube B');
    expect(objects[1].auto_drop).toBe(false);
  });

  it("keeps each instance's printable flag, the last operation winning", () => {
    const ops: ObjectOp[] = [
      { op: 'printable', object_id: '4', value: false },
      { op: 'printable', object_id: '4', instance: 1, value: true },
    ];
    expect(printableOf(ops, '4', 0)).toBe(false);
    expect(printableOf(ops, '4', 1)).toBe(true);
    expect(printableOf(ops, '2', 0)).toBeUndefined();
  });

  it("refuses Orca's illegal characters and deletes what a plate holds", () => {
    expect(nameRefusal('a/b')).toBe('Invalid name, the following characters are not allowed:');
    expect(nameRefusal('Bracket v2')).toBeNull();
    expect(plateDeletions(PLATES[1])).toEqual([
      { op: 'delete', object_id: '4', instance: 0 },
      { op: 'delete', object_id: '4', instance: 1 },
    ]);
  });
});

describe('the menus', () => {
  const tr = (s: string) => s;

  it("gives the object's and the selection's Delete, Printable and Auto Drop an action", () => {
    const calls: string[] = [];
    const mark = (name: string) => () => void calls.push(name);
    const obj = objectMenu(tr, 'Del', false, {
      select: () => {},
      zoom: () => {},
      info: () => {},
      settings: () => {},
      remove: mark('remove'),
      printable: mark('printable'),
      autoDrop: { checked: false, toggle: mark('autoDrop') },
    });
    for (const label of ['Delete', 'Printable', 'Auto Drop']) obj.find((i) => i.label === label)!.onClick!();
    expect(calls).toEqual(['remove', 'printable', 'autoDrop']);
    expect(obj.find((i) => i.label === 'Auto Drop')?.checked).toBe(false);
    const multi = multiMenu(tr, 'Del', true, { zoom: () => {}, deselect: () => {}, filaments: [{ label: 'Filament 1' }] });
    expect(multi.find((i) => i.label === 'Set Filament for selected items')?.items).toEqual([{ label: 'Filament 1' }]);
    expect(multi.find((i) => i.label === 'Delete')?.onClick).toBeUndefined(); // greyed without an action
    const plate = plateMenu(tr, { selectAll: () => {}, settings: () => {}, deleteAll: mark('deleteAll') });
    plate.find((i) => i.label === 'Delete All')!.onClick!();
    expect(calls.at(-1)).toBe('deleteAll');
  });

  it("enables the Edit menu's Delete Selected and Delete All", () => {
    const state = { canDelete: true, canDeleteAll: false } as MenuState;
    const actions = { deleteSelected: () => {}, deleteAll: () => {} } as unknown as MenuActions;
    const menu = editMenu(tr, { phase: () => 'later', later: 'later' }, state, actions, { resetAll: 'Reset' });
    expect(menu.find((i) => i.label === 'Delete Selected')?.onClick).toBeDefined();
    expect(menu.find((i) => i.label === 'Delete All')?.onClick).toBeUndefined();
  });
});

describe('the undo history', () => {
  it('makes each object operation one step', () => {
    let h: DraftHistory = { draft: {}, past: [], future: [], last: null };
    h = draftHistory(h, { type: 'objects', objectOps: [{ op: 'delete', object_id: '2' }] });
    h = draftHistory(h, {
      type: 'objects',
      objectOps: [
        { op: 'delete', object_id: '2' },
        { op: 'rename', object_id: '4', value: 'X' },
      ],
    });
    h = draftHistory(h, { type: 'undo' });
    expect(h.objectOps).toEqual([{ op: 'delete', object_id: '2' }]);
  });
});
