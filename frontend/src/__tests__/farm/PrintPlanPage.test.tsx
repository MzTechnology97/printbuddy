/**
 * Printfarm (fork): "What to print" page. All data is made up.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintPlanPage } from '../../farm/PrintPlanPage';

const plate = (id: number, model: string, score: number, sliced: boolean, printers: object[]) => ({
  plate_id: id,
  library_file_id: id,
  file: `Stampa 3D/Truffle/${model}-truffle.3mf`,
  plate: 1,
  printer_model: model,
  material: 'PLA',
  yield: 15,
  print_time_seconds: 3 * 3600,
  filament_grams: 150,
  sliced,
  score,
  printers,
});

const base = { stock: 0, known: true, in_queue: 0 };
const plan = {
  fetched_at: '2026-01-01T10:00:00Z',
  error: 'Seller Hub did not answer (ConnectError: refused)',
  tolerance: 80,
  components: [
    {
      ...base,
      code: 'C-0121',
      name: 'Tartufo sopra – Marrone',
      status: 'esaurito',
      to_print: 30,
      need: 30,
      days_left: 0,
      color: 'Marrone',
      color_hex: '6b4226',
      shape: 'Tartufo sopra',
      shape_id: 1,
      missing: 30,
      plates: [
        plate(1, 'A1', 2, true, [{ id: 1, name: 'A1 uno', idle: true, material_ok: true, color_ok: true }]),
        plate(2, 'P1S', 1, false, [{ id: 3, name: 'P1S', idle: true, material_ok: true, color_ok: false }]),
      ],
    },
    {
      ...base,
      code: 'C-0004',
      name: 'Supporto Velux',
      status: 'urgente',
      to_print: 32,
      need: 32,
      days_left: 3,
      color: 'Nero',
      color_hex: '111111',
      shape: 'Supporto Velux',
      shape_id: 2,
      missing: 32,
      plates: [],
    },
  ],
};

describe('PrintPlanPage', () => {
  let savedSettings: unknown;
  let createdJobs: unknown;
  let cancelled: string | undefined;
  let jobs: object[];

  beforeEach(() => {
    savedSettings = undefined;
    createdJobs = undefined;
    cancelled = undefined;
    jobs = [];
    server.use(
      http.get('/api/v1/farm/jobs', () => HttpResponse.json(jobs)),
      http.post('/api/v1/farm/jobs', async ({ request }) => {
        createdJobs = await request.json();
        return HttpResponse.json([{ id: 1 }]);
      }),
      http.delete('/api/v1/farm/jobs/:id', ({ params }) => {
        cancelled = String(params.id);
        return HttpResponse.json({ id: Number(params.id), state: 'cancelled' });
      }),
      http.get('/api/v1/farm/plan', () => HttpResponse.json(plan)),
      http.get('/api/v1/farm/plan/settings', () =>
        HttpResponse.json({ url: 'http://hub.test:8000', tolerance: 80, colors: { nero: '111111' } }),
      ),
      http.put('/api/v1/farm/plan/settings', async ({ request }) => {
        savedSettings = await request.json();
        return HttpResponse.json(savedSettings);
      }),
    );
  });

  it('lists components by urgency with the proposed plate and printer readiness', async () => {
    render(<PrintPlanPage />);
    const first = (await screen.findByText('Tartufo sopra – Marrone')).closest('li')!;
    expect(within(first).getByText('out of stock')).toBeInTheDocument();
    expect(within(first).getByText(/Missing 30/)).toBeInTheDocument();
    expect(within(first).getByText('PROPOSED')).toBeInTheDocument();
    expect(within(first).getByText(/A1-truffle\.3mf/)).toBeInTheDocument();
    expect(within(first).getByText(/to slice/)).toBeInTheDocument(); // the P1S plate
    expect(within(first).getByText(/colour not loaded/)).toBeInTheDocument();

    const second = screen.getByText('Supporto Velux').closest('li')!;
    expect(within(second).getByText('urgent')).toBeInTheDocument();
    expect(within(second).getByRole('link', { name: 'Confirm plates' })).toHaveAttribute(
      'href',
      '/farm/products?tab=plates',
    );

    // Seller Hub down: the cached copy is shown with a warning.
    expect(screen.getByText(/showing the copy of 2026-01-01T10:00:00Z/)).toBeInTheDocument();
    expect(screen.getByText(/2 components · 62 pieces missing/)).toBeInTheDocument();
  });

  it('queues a component after a summary, with all its plates as alternatives', async () => {
    const user = userEvent.setup();
    render(<PrintPlanPage />);
    const first = (await screen.findByText('Tartufo sopra – Marrone')).closest('li')!;
    // Nothing is created before the summary is confirmed.
    await user.click(within(first).getByRole('button', { name: /Queue/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Queue: summary' });
    expect(createdJobs).toBeUndefined();
    expect(within(dialog).getByText(/One print: 15 pieces with the proposed plate \(30 missing\)/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() => expect(createdJobs).toEqual({ jobs: [{ component_code: 'C-0121', plate_ids: [1, 2] }] }));

    // The component without plates can't be selected.
    expect(screen.getByLabelText('Select C-0004')).toBeDisabled();
  });

  it('lists jobs with their state and cancels a waiting one', async () => {
    jobs = [
      { id: 7, component_code: 'C-0121', color: 'Marrone', state: 'waiting', note: 'A1 uno: no Marrone PLA loaded',
        plate_ids: [1], plate_id: null, printer_id: null, printer: null, queue_item_id: null, pieces: 15,
        created_at: '' },
      { id: 8, component_code: 'C-0121', color: 'Marrone', state: 'pending', note: null, plate_ids: [1], plate_id: 1,
        printer_id: 1, printer: 'A1 due', queue_item_id: 3, pieces: 15, created_at: '' },
    ];
    const user = userEvent.setup();
    render(<PrintPlanPage />);
    expect(await screen.findByText('waiting for a printer')).toBeInTheDocument();
    expect(screen.getByText('A1 uno: no Marrone PLA loaded')).toBeInTheDocument();
    expect(screen.getByText('in the queue (confirm the plate is clear)')).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: 'Cancel' })[0]);
    await waitFor(() => expect(cancelled).toBe('7'));
  });

  it('saves the Seller Hub address and tolerance', async () => {
    const user = userEvent.setup();
    render(<PrintPlanPage />);
    await screen.findByText('Supporto Velux');
    await user.click(screen.getByRole('button', { name: /Settings/ }));
    const url = await screen.findByLabelText('Seller Hub address');
    await user.clear(url);
    await user.type(url, 'http://hub2.test:8000');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(savedSettings).toEqual({ url: 'http://hub2.test:8000', tolerance: 80, colors: { nero: '111111' } }),
    );
  });
});
