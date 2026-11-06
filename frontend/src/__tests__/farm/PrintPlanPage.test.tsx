/**
 * Printfarm (fork): "What to print" page. All data is made up.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { AccodaSummary, PrintPlanPage } from '../../farm/PrintPlanPage';

// The 3D viewer itself (three.js) is not drawn here: what it is opened on is what counts.
vi.mock('../../components/ModelViewerModal', () => ({
  ModelViewerModal: (p: { title: string; libraryFileId: number; initialTab?: string; initialPlate?: number; onClose: () => void }) => (
    <div role="dialog" aria-label={`3D ${p.title}`} data-file={p.libraryFileId} data-tab={p.initialTab}
      data-plate={p.initialPlate ?? ''}>
      <button type="button" onClick={p.onClose}>Close 3D</button>
    </div>
  ),
}));

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
        {
          ...plate(1, 'A1', 2, true, [{ id: 1, name: 'A1 uno', idle: true, material_ok: true, color_ok: true }]),
          sliced_file_id: 41,
          slicer: 'orcaslicer',
        },
        {
          ...plate(2, 'P1S', 1, false, [{ id: 3, name: 'P1S', idle: true, material_ok: true, color_ok: false }]),
          filament_grams: null,
          filament_grams_max: 412.4,
        },
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
  let jobs: object[];

  beforeEach(() => {
    savedSettings = undefined;
    createdJobs = undefined;
    jobs = [];
    server.use(
      http.get('/api/v1/farm/jobs', () => HttpResponse.json(jobs)),
      http.post('/api/v1/farm/jobs', async ({ request }) => {
        createdJobs = await request.json();
        return HttpResponse.json([{ id: 1 }]);
      }),
      http.get('/api/v1/farm/plan', () => HttpResponse.json(plan)),
      http.get('/api/v1/farm/printer-spools', () =>
        HttpResponse.json([
          { printer_id: 10, name: 'K1', model: 'K1', material: null, color_name: null, color: null, updated_at: null },
        ]),
      ),
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
    // Compact: no plates or printers on the card, a Print button opening the summary where they are.
    expect(within(first).queryByText('PROPOSED')).not.toBeInTheDocument();
    expect(within(first).queryByText(/A1-truffle\.3mf/)).not.toBeInTheDocument();
    expect(within(first).queryByText(/colour not loaded/)).not.toBeInTheDocument();
    expect(within(first).getByRole('button', { name: /Print/ })).toBeEnabled();
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
    await user.click(within(first).getByRole('button', { name: /Print/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Queue: summary' });
    expect(createdJobs).toBeUndefined();
    expect(within(dialog).getByText(/One print: 15 pieces with the proposed plate \(30 missing\)/)).toBeInTheDocument();
    // Each plate shows its picture, the slicer of its 3MF and its filament: the slicer's weight, else the
    // solid upper bound.
    const previews = within(dialog).getAllByRole('button', { name: /in 3D · Plate 1/ });
    expect(previews.map((b) => b.querySelector('img')!.getAttribute('src'))).toEqual([
      expect.stringContaining('/api/v1/farm/plates/1/image'),
      expect.stringContaining('/api/v1/farm/plates/2/image'),
    ]);
    expect(within(dialog).getAllByAltText('OrcaSlicer')).toHaveLength(1);
    expect(within(dialog).getByText('· 150 g')).toBeInTheDocument();
    expect(within(dialog).getByText('· max 412 g')).toHaveAttribute('title', expect.stringMatching(/counted solid/));
    // A click opens it in 3D: the G-code of a sliced plate, else the 3MF on that plate.
    expect(previews[0]).toHaveAccessibleName('See the G-code in 3D · Plate 1');
    await user.click(previews[0]);
    const gcode = screen.getByRole('dialog', { name: /^3D A1-truffle\.3mf/ });
    expect([gcode.dataset.file, gcode.dataset.tab, gcode.dataset.plate]).toEqual(['41', 'gcode', '']);
    await user.click(within(gcode).getByRole('button', { name: 'Close 3D' }));
    await user.click(previews[1]);
    const model = screen.getByRole('dialog', { name: /^3D P1S-truffle\.3mf/ });
    expect([model.dataset.file, model.dataset.tab, model.dataset.plate]).toEqual(['2', '3d', '1']);
    await user.click(within(model).getByRole('button', { name: 'Close 3D' }));
    // A missing picture leaves a placeholder; the 3D view still opens from it.
    fireEvent.error(previews[1].querySelector('img')!);
    expect(within(previews[1]).getByLabelText('No preview')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() => expect(createdJobs).toEqual({ jobs: [{ component_code: 'C-0121', plate_ids: [1, 2] }] }));

    // A chosen printer keeps only the plates of its model and goes with the job.
    await user.click(within(first).getByRole('button', { name: /Print/ }));
    const again = await screen.findByRole('dialog', { name: 'Queue: summary' });
    await user.selectOptions(within(again).getByLabelText('Printer'), 'P1S · free');
    expect(within(again).queryByText(/A1-truffle\.3mf/)).not.toBeInTheDocument();
    // Automatic: the P1S has not the colour loaded, so no plate is listed and nothing can be queued...
    expect(within(again).getByRole('alert')).toHaveTextContent('No plate fits what is loaded on P1S now');
    expect(within(again).queryByRole('button', { name: /Print this plate/ })).not.toBeInTheDocument();
    expect(within(again).getByRole('button', { name: 'Confirm and queue 1 print' })).toBeDisabled();
    // ...until the plate is picked by hand (it then waits to be started by hand) or sent anyway.
    await user.click(within(again).getByRole('button', { name: 'Show 1 plate that does not fit what is loaded' }));
    await user.click(within(again).getByRole('button', { name: /Print this plate: P1S-truffle/ }));
    await user.click(within(again).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() =>
      expect(createdJobs).toEqual({ jobs: [{ component_code: 'C-0121', plate_ids: [2], printer_id: 3 }] }),
    );

    // A chosen 3MF goes alone; a new slice can be forced before it is queued.
    await user.click(within(first).getByRole('button', { name: /Print/ }));
    const third = await screen.findByRole('dialog', { name: 'Queue: summary' });
    await user.selectOptions(within(third).getByLabelText('3MF and plate'), '1');
    await user.click(within(third).getByRole('checkbox', { name: /Force a new slice/ }));
    await user.click(within(third).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() =>
      expect(createdJobs).toEqual({ jobs: [{ component_code: 'C-0121', plate_ids: [1], force_slice: true }] }),
    );

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

  it('shows the library problems by file, each plate with its problem', async () => {
    server.use(
      http.get('/api/v1/farm/library-problems', () =>
        HttpResponse.json([
          { id: 1, library_file_id: 16, filename: 'Maniglia.3mf', plate: 1, code: 'slice_failed',
            message: 'slicing failed: paths of different objects overlap on the plate', since: '2026-11-05T18:51:00Z' },
          { id: 2, library_file_id: 16, filename: 'Maniglia.3mf', plate: null, code: 'printer_model',
            message: 'printer model X9 has no printer in the farm', since: '2026-11-05T18:52:00Z' },
        ]),
      ),
    );
    render(<PrintPlanPage />);
    const box = (await screen.findByText('Library problems')).closest('section')!;
    expect(within(box).getAllByText('Maniglia.3mf')).toHaveLength(1); // one entry per file
    expect(within(box).getByText(/Plate 1: slicing failed: paths of different objects overlap/)).toBeInTheDocument();
    expect(within(box).getByText(/printer model X9 has no printer in the farm/)).toBeInTheDocument();
    expect(within(box).getByRole('link', { name: 'Open the plates in Products' })).toHaveAttribute(
      'href', '/farm/products?tab=plates',
    );
  });

  it('sums up the jobs under way and sends to the queue for the rest', async () => {
    server.use(
      http.get('/api/v1/farm/board', () =>
        HttpResponse.json({ problems: [{ key: 'job:7' }], printing: [], waiting: [{ key: 'job:8' }], history: [],
          history_total: 0 }),
      ),
    );
    render(<PrintPlanPage />);
    expect(await screen.findByText('1 job under way')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Follow them in the Queue →' })).toHaveAttribute('href', '/queue?view=jobs');
  });

  it('has no spools section any more: they are set on the printer cards in Home', async () => {
    render(<PrintPlanPage />);
    await screen.findByText('Tartufo sopra – Marrone');
    expect(screen.queryByText('Spool loaded')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Material')).not.toBeInTheDocument();
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

  it('sends to Settings for keeping the files on the printer', async () => {
    const user = userEvent.setup();
    render(<PrintPlanPage />);
    await screen.findByText('Supporto Velux');
    await user.click(screen.getByRole('button', { name: /Settings/ }));
    expect(await screen.findByText(/Settings → General → Files on the printer \(Bambu\)/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Settings' })).toHaveAttribute('href', '/settings?tab=general');
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
    await user.click(within(dialog).getByRole('button', { name: /Manual start/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() =>
      expect(createdJobs).toEqual({
        jobs: [{ component_code: 'C-0121', plate_ids: [1], printer_id: 1, ams_mapping: [0], manual_start: true }],
      }),
    );
  });

  it('narrows the plates by their 3MF, and the job only gets those', async () => {
    const user = userEvent.setup();
    render(<AccodaSummary components={[plan.components[0]]} byHand onClose={() => undefined} onDone={() => undefined} />);
    const dialog = await screen.findByRole('dialog', { name: 'Queue: summary' });
    // Both plates are PLA: no material filter; two 3MFs: a 3MF filter.
    expect(within(dialog).queryByLabelText('Filter by material')).not.toBeInTheDocument();
    await user.selectOptions(within(dialog).getByLabelText('Filter by 3MF'), 'Stampa 3D/Truffle/P1S-truffle.3mf');
    expect(within(dialog).queryByRole('button', { name: /Print this plate: A1-truffle/ })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /Print this plate: P1S-truffle/ })).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() => expect(createdJobs).toEqual({ jobs: [{ component_code: 'C-0121', plate_ids: [2] }] }));
  });

  it('picks a plate with a click, sends it without the spool and marks the plate clear', async () => {
    let cleared: string | undefined;
    server.use(
      http.get('/api/v1/farm/plates/:id/filaments', ({ params }) =>
        HttpResponse.json({ plate_id: Number(params.id), sliced: true, filaments: [] }),
      ),
      http.get('/api/v1/printers/1/status', () =>
        HttpResponse.json({ connected: true, state: 'FINISH', awaiting_plate_clear: true, ams: [], vt_tray: [] }),
      ),
      http.get('/api/v1/spool-assignments', () => HttpResponse.json([])),
      http.post('/api/v1/printers/:id/clear-plate', ({ params }) => {
        cleared = String(params.id);
        return HttpResponse.json({ success: true, message: 'ok' });
      }),
    );
    const user = userEvent.setup();
    render(<AccodaSummary components={[plan.components[0]]} byHand onClose={() => undefined} onDone={() => undefined} />);
    const dialog = await screen.findByRole('dialog', { name: 'Queue: summary' });
    await user.selectOptions(within(dialog).getByLabelText('Printer'), 'A1 uno · free');
    // The row picks the plate; a second click goes back to automatic.
    const row = within(dialog).getByRole('button', { name: /Print this plate: A1-truffle\.3mf · Plate 1/ });
    await user.click(row);
    expect(row).toHaveAttribute('aria-pressed', 'true');
    expect(within(dialog).getByText(/✓ Chosen/)).toBeInTheDocument();
    // The printer waits for its plate: marked clear from here, after a confirmation.
    expect(await within(dialog).findByText('A1 uno is waiting for its plate to be cleared.')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Plate cleared' }));
    expect(cleared).toBeUndefined();
    const buttons = screen.getAllByRole('button', { name: 'Plate cleared' });
    await user.click(buttons[buttons.length - 1]); // the confirmation's
    await waitFor(() => expect(cleared).toBe('1'));
    // No spool loaded: sent anyway after a confirmation naming the printer; it starts by itself when free.
    const anyway = within(dialog).getByRole('checkbox', { name: /Send it even without the spool loaded/ });
    await user.click(anyway);
    expect(screen.getByText(/goes to A1 uno and starts by itself.*material and colour are not checked/)).toBeInTheDocument();
    expect(anyway).not.toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Send it anyway' }));
    expect(anyway).toBeChecked();
    await user.click(within(dialog).getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() =>
      expect(createdJobs).toEqual({
        jobs: [{ component_code: 'C-0121', plate_ids: [1], printer_id: 1, ignore_spool: true }],
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

  describe("Seller Hub's link ?accoda=", () => {
    const open = (query: string) => window.history.pushState({}, '', `/farm/print-plan?${query}`);
    afterEach(() => window.history.pushState({}, '', '/'));

    it('opens the summary of the component once the plan is in, and drops the link from the URL', async () => {
      open('accoda=C-0121');
      render(<PrintPlanPage />);
      const dialog = await screen.findByRole('dialog', { name: 'Queue: summary' });
      expect(within(dialog).getByLabelText('Print C-0121')).toBeChecked();
      expect(createdJobs).toBeUndefined(); // the user still confirms
      await waitFor(() => expect(window.location.search).toBe(''));
      expect(window.location.pathname).toBe('/farm/print-plan');
    });

    it('opens one summary with several components', async () => {
      const bottom = { ...plan.components[0], code: 'C-0122', name: 'Tartufo sotto – Marrone' };
      server.use(
        http.get('/api/v1/farm/plan', () => HttpResponse.json({ ...plan, components: [...plan.components, bottom] })),
      );
      open('accoda=C-0121,%20c-0122');
      render(<PrintPlanPage />);
      const dialog = await screen.findByRole('dialog', { name: 'Queue: summary' });
      expect(within(dialog).getByLabelText('Print C-0121')).toBeInTheDocument();
      expect(within(dialog).getByLabelText('Print C-0122')).toBeInTheDocument();
      expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();
      await waitFor(() => expect(window.location.search).toBe(''));
    });

    it('says which codes are not in the plan or cannot be queued, never an empty dialog', async () => {
      const user = userEvent.setup();
      open('accoda=C-0099,C-0004');
      render(<PrintPlanPage />);
      expect(await screen.findByText('C-0099 is not in the plan')).toBeInTheDocument();
      expect(screen.getByText("C-0004 can't be queued: it has no confirmed plate")).toBeInTheDocument();
      expect(screen.queryByRole('dialog', { name: 'Queue: summary' })).not.toBeInTheDocument();
      expect(screen.getByText('Tartufo sopra – Marrone')).toBeInTheDocument(); // the page is all there
      await waitFor(() => expect(window.location.search).toBe(''));
      await user.click(screen.getByRole('button', { name: 'Close' }));
      expect(screen.queryByText('C-0099 is not in the plan')).not.toBeInTheDocument();

      // With one that can be queued, the summary opens and says what was left out.
      open('accoda=C-0121,C-0099');
      render(<PrintPlanPage />);
      const dialog = await screen.findByRole('dialog', { name: 'Queue: summary' });
      expect(within(dialog).getByRole('alert')).toHaveTextContent('C-0099 is not in the plan');
      expect(within(dialog).getByLabelText('Print C-0121')).toBeChecked();
    });

    it("keeps the link when the plan doesn't load, and says it opens once it does", async () => {
      server.use(http.get('/api/v1/farm/plan', () => HttpResponse.json({ detail: 'Plan unavailable' }, { status: 500 })));
      open('accoda=C-0121');
      render(<PrintPlanPage />);
      expect(await screen.findByText('The link to queue C-0121 opens when the plan loads')).toBeInTheDocument();
      expect(screen.getByText(/Plan unavailable/)).toBeInTheDocument(); // the plan's own error, as before
      expect(screen.queryByRole('dialog', { name: 'Queue: summary' })).not.toBeInTheDocument();
      expect(window.location.search).toBe('?accoda=C-0121');
    });
  });
});
