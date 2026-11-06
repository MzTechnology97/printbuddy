/**
 * Klipper (fork): flow, pressure advance, bed mesh and versions in the manual controls. Made-up data.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { KlipperTuning } from '../../components/KlipperTuning';
import { api } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      klipperSetFlow: vi.fn(),
      klipperSetPressureAdvance: vi.fn(),
      klipperBedMesh: vi.fn(),
      klipperCalibrateBedMesh: vi.fn(),
      klipperVersions: vi.fn(),
    },
  };
});

const showToast = vi.fn();
const tuning = { flow: 100, pressure_advance: 0.044, smooth_time: 0.04 };

describe('KlipperTuning', () => {
  beforeEach(() => {
    vi.mocked(api.klipperSetFlow).mockReset().mockResolvedValue({ success: true });
    vi.mocked(api.klipperSetPressureAdvance).mockReset().mockResolvedValue({ success: true });
    vi.mocked(api.klipperCalibrateBedMesh).mockReset().mockResolvedValue({ success: true });
    vi.mocked(api.klipperBedMesh).mockReset().mockResolvedValue({
      profile_name: 'default',
      probed_matrix: [
        [0.012, -0.034],
        [0.051, 0.0],
      ],
      profiles: ['default'],
    });
    vi.mocked(api.klipperVersions)
      .mockReset()
      .mockResolvedValue({ klipper: 'v0.13.0-733', moonraker: 'v0.10.0-31', api: '1.5.0', hostname: 'printer' });
  });

  it('sets flow and pressure advance, showing the live values', async () => {
    const user = userEvent.setup();
    render(<KlipperTuning printerId={5} status={{ klipper_tuning: tuning, state: 'RUNNING' }} showToast={showToast} />);
    const flow = screen.getByLabelText('Flow');
    expect(flow).toHaveAttribute('placeholder', '100');
    await user.type(flow, '105');
    await user.click(screen.getAllByRole('button', { name: 'Set' })[0]);
    await waitFor(() => expect(api.klipperSetFlow).toHaveBeenCalledWith(5, 105));

    await user.type(screen.getByLabelText('Pressure advance'), '0.05');
    await user.click(screen.getAllByRole('button', { name: 'Set' })[1]);
    await waitFor(() => expect(api.klipperSetPressureAdvance).toHaveBeenCalledWith(5, 0.05));
    expect(await screen.findByText(/Klipper v0.13.0-733 · Moonraker v0.10.0-31/)).toBeInTheDocument();
  });

  it('reads the bed mesh only when asked, and calibrates only when idle', async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <KlipperTuning printerId={5} status={{ klipper_tuning: tuning, state: 'RUNNING' }} showToast={showToast} />,
    );
    expect(api.klipperBedMesh).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Calibrate' })).toBeDisabled(); // printing

    await user.click(screen.getByRole('button', { name: 'Show' }));
    expect(await screen.findByText(/range 0.085 mm/)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Bed mesh' }).children).toHaveLength(4);

    rerender(<KlipperTuning printerId={5} status={{ klipper_tuning: tuning, state: 'IDLE' }} showToast={showToast} />);
    await user.click(screen.getByRole('button', { name: 'Calibrate' }));
    await user.click(screen.getAllByRole('button', { name: 'Calibrate' }).at(-1)!);
    await waitFor(() => expect(api.klipperCalibrateBedMesh).toHaveBeenCalledWith(5));
  });
});
