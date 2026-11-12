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
import { applyPlateOps, plateAfterMove, withMoved } from '../../farm/workspace/plates';

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
    // 2 plates side by side, 307.2 mm apart (a fifth of the bed between them)
    expect(plateAfterMove(box(240, 280), 1, 2, BED)).toBe(1); // still over plate 1's edge
    expect(plateAfterMove(box(250, 320), 2, 2, BED)).toBe(2); // touches both: the one it was on
    expect(plateAfterMove(box(320, 360), 1, 2, BED)).toBe(2);
    expect(plateAfterMove(box(270, 300), 1, 2, BED)).toBe(0); // in the gap
    // 3 plates: the third starts the second row, to the front (y below 0)
    expect(plateAfterMove(box(10, 50, -100, -60), 1, 3, BED)).toBe(3);
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
