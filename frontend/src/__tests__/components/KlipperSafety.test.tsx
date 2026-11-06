/**
 * Klipper (fork): the card's Klipper error / runout banner, emergency stop and firmware restart. Made-up data.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { KlipperAlert, KlipperSafetyControls } from '../../components/KlipperSafety';
import { api, ApiError } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return { ...actual, api: { ...actual.api, klipperEmergencyStop: vi.fn(), klipperFirmwareRestart: vi.fn() } };
});

const showToast = vi.fn();

describe('KlipperSafety', () => {
  beforeEach(() => {
    vi.mocked(api.klipperEmergencyStop).mockReset().mockResolvedValue({ success: true });
    vi.mocked(api.klipperFirmwareRestart).mockReset().mockResolvedValue({ success: true });
  });

  it('shows nothing while Klipper is ready', () => {
    render(<KlipperAlert printerId={1} status={{ klipper: { state: 'ready', message: null, filament_runout: false } }} showToast={showToast} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a shutdown with its message and restarts after the confirms', async () => {
    const user = userEvent.setup();
    vi.mocked(api.klipperFirmwareRestart).mockRejectedValueOnce(
      new ApiError('The restart ends the print', 409, 'restart_confirm_while_printing'),
    );
    render(
      <KlipperAlert
        printerId={4}
        status={{ klipper: { state: 'shutdown', message: "MCU 'mcu' shutdown: Timer too close", filament_runout: false } }}
        showToast={showToast}
      />,
    );
    expect(screen.getByText('Klipper shutdown')).toBeInTheDocument();
    expect(screen.getByText("MCU 'mcu' shutdown: Timer too close")).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /restart firmware/i }));
    await user.click(screen.getAllByRole('button', { name: /restart firmware/i }).at(-1)!);
    await waitFor(() => expect(api.klipperFirmwareRestart).toHaveBeenCalledWith(4, false));
    // Printing: the backend asks for an explicit confirm, then the restart goes with it.
    expect(await screen.findByText(/the restart ends the print/i)).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: /restart firmware/i }).at(-1)!);
    await waitFor(() => expect(api.klipperFirmwareRestart).toHaveBeenLastCalledWith(4, true));
  });

  it('shows a pause for filament runout', () => {
    render(<KlipperAlert printerId={1} status={{ klipper: { state: 'ready', message: null, filament_runout: true } }} showToast={showToast} />);
    expect(screen.getByRole('alert')).toHaveTextContent(/filament runout/i);
  });

  it('sends the emergency stop only after its confirm', async () => {
    const user = userEvent.setup();
    render(<KlipperSafetyControls printerId={2} showToast={showToast} />);
    await user.click(screen.getByRole('button', { name: /emergency stop/i }));
    expect(api.klipperEmergencyStop).not.toHaveBeenCalled();
    await user.click(screen.getAllByRole('button', { name: /emergency stop/i }).at(-1)!);
    await waitFor(() => expect(api.klipperEmergencyStop).toHaveBeenCalledWith(2));
  });
});
