/**
 * Printfarm (fork): 3MF Workspace. OrcaSlicer's Clone dialog (CloneDialog.cpp): "Auto arrange plate after cloning", on
 * as Orca's preference starts, goes with the number of copies; Set number of instances never arranges.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { CopiesDialog } from '../../farm/workspace/Dialogs';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';

const LAYOUT = { tag: 'v', source: 's', pages: { process: [], filament: [], printer: [] }, options: {}, object_keys: [], plate_keys: {}, it: {} } as unknown as OrcaLayout;

describe('the Clone dialog', () => {
  it('arranges the plate after cloning unless unticked', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const { unmount } = render(<CopiesDialog layout={LAYOUT} value={1} clone onApply={onApply} onClose={() => {}} />);
    const box = screen.getByRole('checkbox', { name: 'Auto arrange plate after cloning' });
    expect(box).toBeChecked();
    expect(box).toBeEnabled();
    const count = screen.getByRole('spinbutton', { name: 'Number of copies:' });
    await user.clear(count);
    await user.type(count, '3{Enter}');
    expect(onApply).toHaveBeenLastCalledWith(3, true);
    unmount();

    render(<CopiesDialog layout={LAYOUT} value={1} clone onApply={onApply} onClose={() => {}} />);
    await user.click(screen.getByRole('checkbox', { name: 'Auto arrange plate after cloning' }));
    await user.click(screen.getByRole('button', { name: 'OK' }));
    expect(onApply).toHaveBeenLastCalledWith(1, false);
  });

  it('never arranges for Set number of instances', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<CopiesDialog layout={LAYOUT} value={2} onApply={onApply} onClose={() => {}} />);
    expect(screen.queryByRole('checkbox')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'OK' }));
    expect(onApply).toHaveBeenCalledWith(2, false);
  });
});
