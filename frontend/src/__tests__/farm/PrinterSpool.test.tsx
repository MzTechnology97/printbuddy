/**
 * Printfarm (fork): the spool set by hand on a Klipper printer without CFS, on its card in Home. Made-up data.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrinterSpoolLine } from '../../farm/PrinterSpool';

describe('PrinterSpoolLine', () => {
  let saved: unknown;
  let cleared: string | undefined;

  beforeEach(() => {
    saved = undefined;
    cleared = undefined;
    server.use(
      http.get('/api/v1/farm/printer-spools', () =>
        HttpResponse.json([
          { printer_id: 10, name: 'K1', model: 'K1', material: 'PETG-CF', color_name: 'Nero', color: '111111',
            updated_at: '2026-01-01T10:00:00' },
          { printer_id: 11, name: 'Sovol SV08', model: 'SV08', material: null, color_name: null, color: null,
            updated_at: null },
        ]),
      ),
      http.get('/api/v1/farm/plan/settings', () =>
        HttpResponse.json({ url: '', tolerance: 80, colors: { nero: '111111', rosso: 'ff0000' } }),
      ),
      http.put('/api/v1/farm/printer-spools/:id', async ({ params, request }) => {
        saved = { id: params.id, ...((await request.json()) as object) };
        return HttpResponse.json({});
      }),
      http.delete('/api/v1/farm/printer-spools/:id', ({ params }) => {
        cleared = String(params.id);
        return HttpResponse.json({});
      }),
    );
  });

  it('shows the spool on the card and changes it in a small editor', async () => {
    const user = userEvent.setup();
    render(<PrinterSpoolLine printerId={10} />);
    await user.click(await screen.findByRole('button', { name: 'Spool (set by hand): PETG-CF · Nero' }));
    const editor = screen.getByRole('dialog', { name: 'Spool loaded · K1' });
    const material = within(editor).getByLabelText('Material');
    expect(material).toHaveValue('PETG-CF');
    await user.clear(material);
    await user.type(material, 'PLA');
    await waitFor(() => expect(within(editor).getByRole('option', { name: 'Rosso' })).toBeInTheDocument());
    await user.selectOptions(within(editor).getByLabelText('Colour'), 'Rosso');
    await user.click(within(editor).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(saved).toEqual({ id: '10', material: 'PLA', color_name: 'Rosso' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('empties it', async () => {
    const user = userEvent.setup();
    render(<PrinterSpoolLine printerId={10} />);
    await user.click(await screen.findByRole('button', { name: /Spool \(set by hand\)/ }));
    await user.click(screen.getByRole('button', { name: 'Empty' }));
    await waitFor(() => expect(cleared).toBe('10'));
  });

  it('says when none is set, and shows nothing for a printer that reports its own (CFS, AMS)', async () => {
    const { unmount } = render(<PrinterSpoolLine printerId={11} />);
    expect(await screen.findByRole('button', { name: 'Spool (set by hand): not set' })).toBeInTheDocument();
    unmount();
    render(<PrinterSpoolLine printerId={3} />);
    await waitFor(() => expect(screen.queryByRole('button')).not.toBeInTheDocument());
  });
});
