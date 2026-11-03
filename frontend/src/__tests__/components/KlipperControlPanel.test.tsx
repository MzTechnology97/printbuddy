import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { KlipperControlPanel } from '../../components/KlipperControlPanel';
import { api } from '../../api/client';
import type { Printer } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      axisJog: vi.fn(),
      homeAxes: vi.fn(),
      extrude: vi.fn(),
      klipperHome: vi.fn(),
      klipperExtrude: vi.fn(),
      setNozzleTemperature: vi.fn(),
      setBedTemperature: vi.fn(),
      disableSteppers: vi.fn(),
      setChamberTemperature: vi.fn(),
      klipperMacros: vi.fn(),
      klipperRunMacro: vi.fn(),
      klipperConsole: vi.fn(),
      klipperConsoleSend: vi.fn(),
    },
  };
});

const prusaPrinter = {
  id: 3,
  name: 'Prusa MK4S',
  ip_address: '192.168.1.246',
  serial_number: 'PRUSA-3',
  access_code: '',
  model: 'Prusa MK4S',
  provider: 'prusalink',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'brass',
  location: null,
  auto_archive: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
} as Printer;

const octoPrintPrinter = {
  ...prusaPrinter,
  id: 9,
  name: 'Ender 3',
  model: 'Creality Ender 3',
  provider: 'octoprint',
} as Printer;

const fluiddPrinter = {
  ...prusaPrinter,
  id: 7,
  name: 'Creality K2 Plus',
  model: 'Creality K2 Plus',
  provider: 'fluidd',
} as Printer;

describe('KlipperControlPanel PrusaLink controls', () => {
  beforeEach(() => {
    vi.mocked(api.axisJog).mockResolvedValue({ success: true, message: 'ok' });
    vi.mocked(api.homeAxes).mockResolvedValue({ success: true, message: 'ok' });
    vi.mocked(api.extrude).mockResolvedValue({ success: true, message: 'ok' });
    vi.mocked(api.klipperHome).mockResolvedValue({ success: true });
    vi.mocked(api.klipperExtrude).mockResolvedValue({ success: true });
    vi.mocked(api.disableSteppers).mockResolvedValue({ success: true, message: 'ok' });
    vi.mocked(api.klipperMacros).mockResolvedValue({ macros: [] });
    vi.mocked(api.klipperConsole).mockResolvedValue({ lines: [] });
    vi.clearAllMocks();
  });

  it('uses provider-neutral home endpoint for PrusaLink home controls', async () => {
    const user = userEvent.setup();

    render(<KlipperControlPanel printer={prusaPrinter} status={{ connected: true }} showToast={vi.fn()} />);

    await user.click(screen.getByTitle('Home All'));

    await waitFor(() => expect(api.homeAxes).toHaveBeenCalledWith(3, 'all'));
    expect(api.klipperHome).not.toHaveBeenCalled();
  });

  it('uses provider-neutral extrude endpoint for PrusaLink extrusion controls', async () => {
    const user = userEvent.setup();

    render(<KlipperControlPanel printer={prusaPrinter} status={{ connected: true }} showToast={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /extrude/i }));

    await waitFor(() => expect(api.extrude).toHaveBeenCalledWith(3, 10, 300));
    expect(api.klipperExtrude).not.toHaveBeenCalled();
  });

  it('uses provider-neutral home and extrude endpoints for OctoPrint printers', async () => {
    const user = userEvent.setup();

    render(<KlipperControlPanel printer={octoPrintPrinter} status={{ connected: true }} showToast={vi.fn()} />);

    await user.click(screen.getByTitle('Home All'));
    await user.click(screen.getByRole('button', { name: /extrude/i }));

    await waitFor(() => expect(api.homeAxes).toHaveBeenCalledWith(9, 'all'));
    await waitFor(() => expect(api.extrude).toHaveBeenCalledWith(9, 10, 300));
    expect(api.klipperHome).not.toHaveBeenCalled();
    expect(api.klipperExtrude).not.toHaveBeenCalled();
  });

  it('shows generic Klipper manual controls and disables steppers for Fluidd printers', async () => {
    const user = userEvent.setup();

    render(<KlipperControlPanel printer={fluiddPrinter} status={{ connected: true }} showToast={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /disable steppers/i }));

    await waitFor(() => expect(api.disableSteppers).toHaveBeenCalledWith(7));
  });
});

describe('KlipperControlPanel chamber, macros and console', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.setChamberTemperature).mockResolvedValue({ success: true, message: 'ok' });
    vi.mocked(api.klipperMacros).mockResolvedValue({ macros: ['START_PRINT', 'set_case_leds_on'] });
    vi.mocked(api.klipperRunMacro).mockResolvedValue({ success: true });
    vi.mocked(api.klipperConsole).mockResolvedValue({
      lines: [
        { message: 'QUERY_ENDSTOPS', time: 1700000000, type: 'command' },
        { message: '!! Unknown command:"FOO"', time: 1700000001, type: 'response' },
      ],
    });
    vi.mocked(api.klipperConsoleSend).mockResolvedValue({ success: true });
  });

  it('sets the chamber only on printers whose chamber heater reports a max temperature', async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <KlipperControlPanel printer={fluiddPrinter} status={{ connected: true, temperatures: { nozzle: 25, bed: 25 } }} showToast={vi.fn()} />,
    );
    expect(screen.queryByLabelText('Chamber temperature target')).not.toBeInTheDocument();
    unmount();

    render(
      <KlipperControlPanel
        printer={fluiddPrinter}
        status={{ connected: true, temperatures: { nozzle: 25, bed: 25, chamber: 33, chamber_max: 80 } }}
        showToast={vi.fn()}
      />,
    );
    await user.type(screen.getByLabelText('Chamber temperature target'), '50{Enter}');
    await waitFor(() => expect(api.setChamberTemperature).toHaveBeenCalledWith(7, 50));
  });

  it('runs a macro only after the confirm', async () => {
    const user = userEvent.setup();
    render(<KlipperControlPanel printer={fluiddPrinter} status={{ connected: true }} showToast={vi.fn()} />);

    await user.click(await screen.findByRole('button', { name: 'set_case_leds_on' }));
    expect(api.klipperRunMacro).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Run' }));
    await waitFor(() => expect(api.klipperRunMacro).toHaveBeenCalledWith(7, 'set_case_leds_on'));
  });

  it('shows the console lines and sends a command', async () => {
    const user = userEvent.setup();
    render(<KlipperControlPanel printer={fluiddPrinter} status={{ connected: true }} showToast={vi.fn()} />);

    expect(await screen.findByText('!! Unknown command:"FOO"')).toBeInTheDocument();
    await user.type(screen.getByLabelText('G-code command'), 'M115{Enter}');
    await waitFor(() => expect(api.klipperConsoleSend).toHaveBeenCalledWith(7, 'M115'));
    await waitFor(() => expect(screen.getByLabelText('G-code command')).toHaveValue(''));
  });
});
