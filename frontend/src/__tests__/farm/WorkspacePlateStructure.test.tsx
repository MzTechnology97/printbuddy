/**
 * Printfarm (fork): 3MF Workspace, batch E4b. The plates' structure as OrcaSlicer changes it: Add plate, Remove
 * the current plate (if not last one), Move plate to the front, Lock, Edit plate name. The plates the workspace
 * shows, their own settings numbered again, one undo step each, and the plate's buttons. Made-up data.
 */
import { describe, it, expect, vi } from 'vitest';
import { act, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import type { WorkspacePlate } from '../../farm/workspace/api';
import { useSettingsDraft } from '../../farm/workspace/draft';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';
import { PlateNameDialog } from '../../farm/workspace/PlateSettings';
import { applyPlateOps, remapPlateEdits } from '../../farm/workspace/plates';
import { PlateButtons } from '../../farm/workspace/Viewport';

const LAYOUT: OrcaLayout = {
  tag: 'test',
  source: '',
  pages: { process: [], filament: [], printer: [] },
  options: {},
  it: {},
  object_keys: [],
  plate_keys: {},
};
const plate = (index: number, name: string, objects: string[]): WorkspacePlate => ({
  index,
  name,
  object_ids: objects,
  instances: objects.map((object_id) => ({ object_id, instance_id: '0' })),
  metadata: { plater_id: String(index), plater_name: name, locked: 'false' },
});
const PLATES = [plate(1, 'Left', ['2']), plate(2, 'Middle', ['4']), plate(3, 'Right', ['6'])];
const shown = (ops: Parameters<typeof applyPlateOps>[1]) =>
  applyPlateOps(PLATES, ops).map((p) => `${p.index}:${p.name ?? 'new'}:${p.source}:${p.object_ids.join(',')}:${p.metadata?.plater_id}`);

describe('the plates after adds, removals and moves', () => {
  it('numbers them again, each keeping its objects and the file plate it is', () => {
    expect(shown([{ op: 'front', plate: 3 }])).toEqual(['1:Right:3:6:1', '2:Left:1:2:2', '3:Middle:2:4:3']);
    // a removed plate's objects are on no plate: the plates no longer list them
    expect(shown([{ op: 'remove', plate: 1 }])).toEqual(['1:Middle:2:4:1', '2:Right:3:6:2']);
    expect(shown([{ op: 'add' }, { op: 'remove', plate: 2 }])).toEqual(['1:Left:1:2:1', '2:Right:3:6:2', '3:new:null::3']);
  });

  it("numbers the plates' own settings edited as the plates are, dropping a removed plate's", () => {
    const edits = {
      'plate:1': { locked: 'true' },
      'plate:2': { bed_type: 'Cool Plate' },
      'plate:3': { plater_name: 'R' },
      '4': { wall_loops: '2' },
    };
    expect(remapPlateEdits(edits, { op: 'remove', plate: 2 })).toEqual({
      'plate:1': { locked: 'true' },
      'plate:2': { plater_name: 'R' },
      '4': { wall_loops: '2' },
    });
    expect(remapPlateEdits(edits, { op: 'front', plate: 3 })).toEqual({
      'plate:1': { plater_name: 'R' },
      'plate:2': { locked: 'true' },
      'plate:3': { bed_type: 'Cool Plate' },
      '4': { wall_loops: '2' },
    });
    expect(remapPlateEdits(edits, { op: 'add' })).toBe(edits);
  });

  it('makes each change one undo step, with the edits it renumbers; Undo all clears them', () => {
    const { result } = renderHook(() => useSettingsDraft([]));
    act(() => result.current.setOwnTargets({ 'plate:2': { locked: 'true' } }));
    act(() => result.current.setPlates([{ op: 'front', plate: 2 }], {}, { 'plate:1': { locked: 'true' } }));
    expect(result.current.plateOps).toEqual([{ op: 'front', plate: 2 }]);
    expect(result.current.own).toEqual({ 'plate:1': { locked: 'true' } });
    expect(result.current.count).toBe(2); // the move and the lock
    act(() => result.current.undo());
    expect(result.current.plateOps).toEqual([]);
    expect(result.current.own).toEqual({ 'plate:2': { locked: 'true' } });
    act(() => result.current.redo());
    act(() => result.current.resetAll());
    expect(result.current.plateOps).toEqual([]);
  });
});

describe("the plate's buttons and its name", () => {
  const actions = { onRemove: vi.fn(), onLock: vi.fn(), onName: vi.fn(), onFront: vi.fn() };
  const button = (name: RegExp) => screen.getByRole('button', { name });

  it('remove only with another plate left, to the front only from the second, lock shows unlock when locked', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<PlateButtons layout={LAYOUT} onSettings={() => {}} plate={{ index: 1, count: 1, locked: false, ...actions }} />);
    expect(button(/^Remove current plate/)).toBeDisabled();
    expect(button(/^Move plate to the front/)).toBeDisabled();
    expect(button(/^Auto orient objects/)).toBeDisabled(); // with the objects' arranging, later
    await user.click(button(/^Lock current plate/));
    expect(actions.onLock).toHaveBeenCalled();
    rerender(<PlateButtons layout={LAYOUT} onSettings={() => {}} plate={{ index: 2, count: 2, locked: true, ...actions }} />);
    await user.click(button(/^Remove current plate/));
    await user.click(button(/^Move plate to the front/));
    expect(actions.onRemove).toHaveBeenCalled();
    expect(actions.onFront).toHaveBeenCalled();
    expect(button(/^Unlock current plate/)).toHaveAttribute('aria-pressed', 'true');
    // a file that can't be saved: only the plate's settings
    rerender(<PlateButtons layout={LAYOUT} onSettings={() => {}} />);
    expect(button(/^Lock current plate/)).toBeDisabled();
    expect(button(/^Customize current plate/)).toBeEnabled();
  });

  it('renames the plate on Enter, at most 250 characters', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<PlateNameDialog layout={LAYOUT} name="Left" onApply={onApply} onClose={() => {}} />);
    const box = screen.getByRole('textbox', { name: 'Plate name' });
    expect(box).toHaveAttribute('maxLength', '250');
    await user.clear(box);
    await user.type(box, 'Front{Enter}');
    expect(onApply).toHaveBeenCalledWith('Front');
  });
});
