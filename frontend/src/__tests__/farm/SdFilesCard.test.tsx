/**
 * Fork (printfarm): Bambu printers that keep their files on the SD or USB stick, set in Settings.
 */

import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SdFilesCard } from '../../farm/SdFilesCard';

describe('SdFilesCard', () => {
  it('turns keeping the files on per Bambu printer, warning about ghost prints', async () => {
    let saved: unknown;
    server.use(
      http.get('/api/v1/farm/sd-files', () =>
        HttpResponse.json({
          keep_days: 14,
          printers: [
            { id: 4, name: 'A1 uno', model: 'A1', keep: false, ghost_print_risk: true },
            { id: 8, name: 'H2D', model: 'H2D', keep: true, ghost_print_risk: false },
          ],
        }),
      ),
      http.put('/api/v1/farm/sd-files', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ keep_days: 21, printers: [] });
      }),
    );
    const user = userEvent.setup();
    render(<SdFilesCard />);
    expect(await screen.findByText('Files on the printer (Bambu)')).toBeInTheDocument();
    const a1 = screen.getByRole('switch', { name: /A1 uno.*Keep the file on the printer storage \(SD or USB stick\)/ });
    expect(screen.getByRole('switch', { name: /H2D/ })).toHaveAttribute('aria-checked', 'true');
    expect(a1).toHaveAttribute('aria-checked', 'false');
    expect(screen.queryByText(/ghost print/)).not.toBeInTheDocument();
    await user.click(a1);
    // On a model that may restart the last print on power-up: the warning shows.
    expect(a1).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('alert')).toHaveTextContent('while its file is on the SD or USB stick (ghost print)');
    const days = screen.getByLabelText('Delete unused files after (days)');
    await user.clear(days);
    await user.type(days, '21');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(saved).toEqual({ keep_days: 21, keep_printer_ids: [8, 4] }));
  });

  it('turns it off again', async () => {
    let saved: unknown;
    server.use(
      http.get('/api/v1/farm/sd-files', () =>
        HttpResponse.json({ keep_days: 14, printers: [{ id: 8, name: 'H2D', model: 'H2D', keep: true, ghost_print_risk: false }] }),
      ),
      http.put('/api/v1/farm/sd-files', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ keep_days: 14, printers: [] });
      }),
    );
    const user = userEvent.setup();
    render(<SdFilesCard />);
    await user.click(await screen.findByRole('switch', { name: /H2D/ }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(saved).toEqual({ keep_days: 14, keep_printer_ids: [] }));
  });

  it('shows nothing without Bambu printers', async () => {
    let asked = false;
    server.use(
      http.get('/api/v1/farm/sd-files', () => {
        asked = true;
        return HttpResponse.json({ keep_days: 14, printers: [] });
      }),
    );
    render(<SdFilesCard />);
    await waitFor(() => expect(asked).toBe(true));
    expect(screen.queryByText('Files on the printer (Bambu)')).not.toBeInTheDocument();
  });
});
