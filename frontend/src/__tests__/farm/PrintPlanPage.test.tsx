/**
 * Printfarm (fork): "What to print" page. All data is made up.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { AccodaSummary, PrintPlanPage } from '../../farm/PrintPlanPage';

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

const base = { stock: 0, known: true, in_queue: 0, products: [] };
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
      products: [{ project_id: 65, name: 'Tartufo – Marrone', has_cover: true }],
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
  let savedSpool: unknown;

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
      http.get('/api/v1/farm/printer-spools', () =>
        HttpResponse.json([
          { printer_id: 10, name: 'K1', model: 'K1', material: null, color_name: null, color: null, updated_at: null },
        ]),
      ),
      http.put('/api/v1/farm/printer-spools/:id', async ({ params, request }) => {
        savedSpool = { id: params.id, ...((await request.json()) as object) };
        return HttpResponse.json({});
      }),
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
    // The product's photo (its colour variant) and the proposed plate's preview, opening the 3D viewer.
    expect(within(first).getByText('In: Tartufo – Marrone')).toBeInTheDocument();
    expect(within(first).getByRole('img', { name: 'Photo of Tartufo – Marrone' }).getAttribute('src')).toContain(
      '/projects/65/cover',
    );
    expect(within(first).getByRole('button', { name: 'Open in the 3D viewer Plate 1' })).toBeInTheDocument();

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

    // A chosen printer keeps only the plates of its model and goes with the job.
    await user.click(within(first).getByRole('button', { name: /Queue/ }));
    const again = await screen.findByRole('dialog', { name: 'Queue: summary' });
    await user.selectOptions(within(again).getByLabelText('Printer'), 'P1S · free');
    expect(within(again).queryByText(/A1-truffle\.3mf/)).not.toBeInTheDocument();
    await user.click(within(again).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() =>
      expect(createdJobs).toEqual({ jobs: [{ component_code: 'C-0121', plate_ids: [2], printer_id: 3 }] }),
    );

    // A chosen 3MF goes alone.
    await user.click(within(first).getByRole('button', { name: /Queue/ }));
    const third = await screen.findByRole('dialog', { name: 'Queue: summary' });
    await user.selectOptions(within(third).getByLabelText('3MF and plate'), '1');
    await user.click(within(third).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() => expect(createdJobs).toEqual({ jobs: [{ component_code: 'C-0121', plate_ids: [1] }] }));

    // The component without plates can't be selected.
    expect(screen.getByLabelText('Select C-0004')).toBeDisabled();
  });

  it('warns about requested components no active printer can print', async () => {
    const onlyX1C = {
      ...plan.components[0],
      code: 'C-0300',
      name: 'Gancio',
      plates: [plate(5, 'X1C', 1, true, [])], // a plate, but for a model with no active printer
    };
    const enough = { ...plan.components[1], code: 'C-0301', name: 'Coperchio', to_print: 0 }; // not asked for
    server.use(
      http.get('/api/v1/farm/plan', () =>
        HttpResponse.json({ ...plan, components: [...plan.components, onlyX1C, enough] }),
      ),
    );
    render(<PrintPlanPage />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(
      '2 components Seller Hub asks for have no plate ready for any active printer:C-0004 Supporto Velux · C-0300 Gancio',
    );
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

  it('asks what a print outside the queue printed, with the proposal from its name', async () => {
    const assigned: unknown[] = [];
    server.use(
      http.get('/api/v1/farm/products', () =>
        HttpResponse.json({
          generated_at: '',
          products: [
            { project_id: 4, name: 'Gancio', skus: [], asins: [], updated_at: '',
              parts: [{ component_code: 'C-0200', name: 'Gancio', quantity: 1, plates: [] }] },
            { project_id: 5, name: 'Tartufo', skus: [], asins: [], updated_at: '',
              parts: [
                { component_code: 'C-0121', name: 'Sopra', quantity: 1, plates: [] },
                { component_code: 'C-0122', name: 'Sotto', quantity: 1, plates: [] },
              ] },
          ],
        }),
      ),
      http.post('/api/v1/farm/jobs/:id/assign', async ({ params, request }) => {
        assigned.push({ id: params.id, ...((await request.json()) as object) });
        return HttpResponse.json([]);
      }),
    );
    const outsideJob = { component_code: '', color: null, state: 'to_assign', note: null, plate_ids: [],
      plate_id: null, printer_id: 1, printer: 'A1 uno', queue_item_id: null, pieces: 0, created_at: '',
      finished_at: '2026-01-01T10:00:00', outside_queue: true };
    jobs = [
      { ...outsideJob, id: 9, print_name: 'Sopra plate 2.3mf',
        proposal: [{ component_code: 'C-0121', name: 'Sopra', pieces: 15, color: 'Nero', plate_id: 1 }] },
      { ...outsideJob, id: 10, print_name: 'Regalo.3mf', proposal: null },
    ];
    const user = userEvent.setup();
    render(<PrintPlanPage />);
    expect(await screen.findByText('2 prints outside the queue to assign')).toBeInTheDocument();
    expect(screen.getByText('From its name it looks like: C-0121 Sopra ×15')).toBeInTheDocument();
    // The proposed product is chosen, with the proposed pieces; the user adds the bottoms.
    expect(await screen.findByLabelText('Pieces C-0121')).toHaveValue(15);
    await user.type(screen.getByLabelText('Pieces C-0122'), '14');
    await user.click(screen.getAllByRole('button', { name: 'Save the pieces' })[0]);
    await user.click(screen.getAllByRole('button', { name: 'Not production' })[1]);
    await waitFor(() =>
      expect(assigned).toEqual([
        { id: '9', parts: [{ component_code: 'C-0121', pieces: 15 }, { component_code: 'C-0122', pieces: 14 }] },
        { id: '10', not_production: true },
      ]),
    );
    // Nothing proposed: the product is the user's pick, and nothing can be saved before it.
    expect(screen.getAllByRole('button', { name: 'Save the pieces' })[1]).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Product of Regalo.3mf'), 'Gancio');
    expect(screen.getAllByLabelText('Pieces C-0200')[0]).toHaveValue(null);
  });

  it('sets the spool loaded on a Klipper printer without CFS', async () => {
    const user = userEvent.setup();
    render(<PrintPlanPage />);
    await user.type(await screen.findByLabelText('Material K1'), 'PETG');
    await waitFor(() => expect(screen.getByRole('option', { name: 'Nero' })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText('Colour K1'), 'Nero');
    await user.click(screen.getByRole('button', { name: 'Save K1' }));
    await waitFor(() => expect(savedSpool).toEqual({ id: '10', material: 'PETG', color_name: 'Nero' }));
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

  it('prints the ticked components with their picture, slots chosen by hand and a manual start', async () => {
    server.use(
      http.get('/api/v1/farm/plates/:id/filaments', ({ params }) =>
        HttpResponse.json({
          plate_id: Number(params.id),
          sliced: true,
          // The 3MF says yellow: the proposal still looks for the component's brown.
          filaments: [{ slot_id: 1, type: 'PLA', color: '#FFC800', used_grams: 150, used_meters: 50 }],
        }),
      ),
      http.get('/api/v1/printers/1/status', () =>
        HttpResponse.json({
          connected: true,
          state: 'IDLE',
          ams: [{ id: 0, humidity: null, temp: null, tray: [
            { id: 0, tray_type: 'PLA', tray_color: 'FFFFFFFF' },
            { id: 1, tray_type: 'PLA', tray_color: '6B4226FF' },
          ] }],
          vt_tray: [],
        }),
      ),
      http.get('/api/v1/spool-assignments', () => HttpResponse.json([])),
    );
    const [needed, ...rest] = plan.components;
    const covered = { ...needed, code: 'C-0122', name: 'Tartufo sotto – Marrone', missing: 0 };
    const user = userEvent.setup();
    render(<AccodaSummary components={[needed, covered, ...rest]} byHand onClose={() => undefined}
      onDone={() => undefined} />);
    const dialog = await screen.findByRole('dialog', { name: 'Queue: summary' });
    // What is still missing is ticked, the rest can be added; each shows its picture.
    expect(within(dialog).getByLabelText('Print C-0121')).toBeChecked();
    expect(within(dialog).getByLabelText('Print C-0122')).not.toBeChecked();
    expect(within(dialog).getByRole('img', { name: 'Tartufo sopra – Marrone' }).getAttribute('src')).toContain(
      '/farm/components/C-0121/image?c=6b4226',
    );

    // A chosen printer shows what it has loaded and upstream's slot mapping; a slot picked by hand goes.
    await user.selectOptions(within(dialog).getByLabelText('Printer'), 'A1 uno · free');
    expect(await within(dialog).findByText('Loaded now:')).toBeInTheDocument();
    const slot = await within(dialog).findByTitle('Auto-matched');
    expect(slot).toHaveValue('1'); // the brown one, proposed
    await user.selectOptions(slot, '0');
    await user.click(within(dialog).getByRole('button', { name: /Queue Only/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() =>
      expect(createdJobs).toEqual({
        jobs: [{ component_code: 'C-0121', plate_ids: [1], printer_id: 1, ams_mapping: [0], manual_start: true }],
      }),
    );
  });

  it("shows Seller Hub's stock, leaves what is in stock unticked and marks the piece that limits the kit", async () => {
    const [first] = plan.components;
    const big = { ...first, code: 'C-0094', name: 'Maniglia grande – Giallo', stock: 12, to_print: 0, missing: 0,
      status: '', kit_quantity: 2 };
    const small = { ...first, code: 'C-0095', name: 'Maniglia piccola – Giallo', stock: 1, to_print: 6, missing: 6,
      status: 'urgente', kit_quantity: 2 };
    const { unmount } = render(<AccodaSummary components={[big, small]} byHand onClose={() => undefined}
      onDone={() => undefined} />);
    const dialog = await screen.findByRole('dialog', { name: 'Queue: summary' });
    expect(within(dialog).getByLabelText('Print C-0095')).toBeChecked();
    expect(within(dialog).getByLabelText('Print C-0094')).not.toBeChecked(); // 12 in stock
    expect(within(dialog).getByText('you already have 12')).toBeInTheDocument();
    expect(within(dialog).getByText('To print: 6')).toBeInTheDocument();
    expect(within(dialog).getByText('limits the kit: 0 products')).toBeInTheDocument();
    unmount();

    // Seller Hub not read: the dialog works as before and says the stock is not known.
    render(<AccodaSummary components={[{ ...big, stock: null }]} byHand onClose={() => undefined}
      onDone={() => undefined} />);
    const again = await screen.findByRole('dialog', { name: 'Queue: summary' });
    expect(within(again).getByText('stock not available')).toBeInTheDocument();
    expect(within(again).getByLabelText('Print C-0094')).toBeChecked();
  });
});
