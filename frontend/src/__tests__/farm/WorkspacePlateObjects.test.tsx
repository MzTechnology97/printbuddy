/**
 * Printfarm (fork): 3MF Workspace, batch E4c. Objects between plates as OrcaSlicer moves them: an instance moved onto
 * another plate is on it (notify_instance_update), and Duplicate Current Plate copies a plate's objects onto a new one.
 * Made-up data.
 */
import { describe, it, expect, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { WorkspacePlate } from '../../farm/workspace/api';
import { useSettingsDraft } from '../../farm/workspace/draft';
import { type MenuActions, type MenuState, editMenu } from '../../farm/workspace/menuBar';
import { applyPlateOps, landing, plateNow, withAdded, withMoved } from '../../farm/workspace/plates';

const plate = (index: number, objects: [string, string][]): WorkspacePlate => ({
  index,
  name: null,
  object_ids: [...new Set(objects.map(([o]) => o))],
  instances: objects.map(([object_id, instance_id]) => ({ object_id, instance_id })),
  metadata: { plater_id: String(index) },
});
const PLATES = [plate(1, [['2', '0']]), plate(2, [['4', '0'], ['4', '1']])];
const BED = { x: 256, y: 256 };
const box = (x0: number, x1: number, y0 = 10, y1 = 50) => ({ min: { x: x0, y: y0 }, max: { x: x1, y: y1 } });

describe('an instance moved onto another plate', () => {
  it('stays on its plate while it touches it, else is on the first plate it touches, else on none', () => {
    const at = (b: ReturnType<typeof box>, current: number, count = 2) =>
      landing(b, current, count, PLATES.slice(0, count), BED);
    // 2 plates side by side, 307.2 mm apart (a fifth of the bed between them)
    expect(at(box(240, 280), 1)).toBe(1); // still over plate 1's edge
    expect(at(box(250, 320), 2)).toBe(2); // touches both: the one it was on
    expect(at(box(320, 360), 1)).toBe(2);
    expect(at(box(270, 300), 1)).toBe(0); // in the gap
    // 3 plates: the third starts the second row, to the front (y below 0)
    expect(landing(box(10, 50, -100, -60), 1, 3, [...PLATES, plate(3, [])], BED)).toBe(3);
  });

  it('is listed on the plate it lies on, its object on both while it has instances on both', () => {
    const moved = withMoved(PLATES, { '4:1': 1 });
    expect(moved.map((p) => [p.object_ids, p.instances.map((i) => `${i.object_id}:${i.instance_id}`)])).toEqual([
      [['2', '4'], ['2:0', '4:1']],
      [['4'], ['4:0']],
    ]);
    expect(withMoved(PLATES, { '2:0': 0 })[0].object_ids).toEqual([]); // on no plate
    expect(withMoved(PLATES, {})).toBe(PLATES);
  });

  it('lands on a plate added before the copy is saved, kept by the change that added it', () => {
    // a third plate added: the second row, to the front; then plate 1 removed (the added one is plate 2 now)
    const ops = [{ op: 'add' as const }, { op: 'remove' as const, plate: 1 }];
    const added = applyPlateOps(PLATES, [ops[0]]);
    const ref = landing(box(10, 50, -100, -60), 1, 2, added, BED);
    expect(ref).toEqual({ op: 0 });
    expect(landing(box(20, 60, -100, -60), ref, 2, added, BED)).toEqual({ op: 0 }); // it stays while it touches it
    const after = withAdded(applyPlateOps(withMoved(PLATES, { '2:0': ref }), ops), { '2:0': ref });
    expect(after.map((p) => [p.index, p.object_ids])).toEqual([
      [1, ['4']],
      [2, ['2']],
    ]);
    expect(plateNow(ref, after)).toBe(2);
    expect(plateNow(ref, applyPlateOps(PLATES, [ops[0], { op: 'remove', plate: 3 }]))).toBe(0); // removed again
    expect(plateNow(2, after)).toBe(1); // a file plate: its number now
  });

  it('moves with its placement in one undo step', () => {
    const { result } = renderHook(() => useSettingsDraft([]));
    act(() => result.current.place({ '2:0': [1, 0, 0, 0, 1, 0, 0, 0, 1, 400, 30, 0] }, { '2:0': 2 }));
    expect(result.current.onPlate).toEqual({ '2:0': 2 });
    act(() => result.current.undo());
    expect(result.current.onPlate).toEqual({});
    expect(result.current.placed).toEqual({});
  });
});

describe('Duplicate Current Plate', () => {
  it("is a new plate after the last showing its plate's objects, which it lists apart (they are copied on save)", () => {
    const plates = applyPlateOps(PLATES, [{ op: 'duplicate', plate: 2, objects: ['4'], instances: [] }]);
    expect(plates.map((p) => [p.index, p.source, p.object_ids, p.copies ?? null])).toEqual([
      [1, 1, ['2'], null],
      [2, 2, ['4'], null],
      [3, 2, [], ['4']],
    ]);
    expect(plates[2].added).toBe(0); // the change that made it
    expect(plates[2].metadata).toEqual({ plater_name: '', locked: 'false', plater_id: '3' }); // none of its settings
    // a new plate, with nothing on it, duplicates into another empty one
    const empty = applyPlateOps(PLATES, [{ op: 'add' }, { op: 'duplicate', plate: 3, objects: [], instances: [] }]);
    expect(empty[3]).toMatchObject({ source: null, copies: undefined });
  });

  it('is in the Edit menu, greyed with the reason when the copy could not take it', () => {
    const state = { duplicatePlate: true } as MenuState;
    const actions = { duplicatePlate: vi.fn() } as unknown as MenuActions;
    const tr = (s: string) => s;
    const hints = { phase: (n: number) => `phase ${n}`, later: 'later' };
    const item = (s: MenuState) => editMenu(tr, hints, s, actions, { resetAll: 'Reset' }).find((i) => i.label === 'Duplicate Current Plate')!;
    item(state).onClick!();
    expect(actions.duplicatePlate).toHaveBeenCalled();
    expect(item({ ...state, duplicatePlate: 'too large' })).toMatchObject({ onClick: undefined, hint: 'too large' });
  });
});
