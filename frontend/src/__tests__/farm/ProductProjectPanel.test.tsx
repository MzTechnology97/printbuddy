/**
 * Printfarm (fork): the project page of a product shows the farm view instead of upstream's panels.
 * All data is made up.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { Route, Routes } from 'react-router-dom';
import { FarmProductGate, ProductPage } from '../../farm/ProductProjectPanel';

const part = { shape_id: 1, color: 'Nero', retired: false, quantity: 2, used_by: [] as { project_id: number; name: string }[] };
const view = {
  project_id: 65,
  name: 'Tappi Paraspifferi AMI',
  has_cover: false,
  disabled: false,
  folders: [{ folder_id: 3, name: 'Caps' }],
  skus: ['TP-01'],
  asins: ['B0TEST0065'],
  parts: [
    {
      ...part,
      code: 'C-0118',
      name: 'Tappo Bouchon porte – Nero',
      shape: 'Tappo Bouchon porte',
      plates: [
        {
          plate_id: 4,
          library_file_id: 9,
          file: 'Stampa 3D/Caps/P1S-Caps-PETG.3mf',
          plate: 1,
          printer_model: 'P1S',
          material: 'PETG',
          yield: 8,
          print_time_seconds: 5400,
          filament_grams: 60,
          sliced: false,
        },
      ],
    },
    { ...part, code: 'C-0119', name: 'Tappo kapi kolu – Nero', shape: 'Tappo kapi kolu', plates: [] },
  ],
  files: [
    {
      library_file_id: 9,
      filename: 'P1S-Caps-PETG.3mf',
      path: 'x',
      folder: 'Caps / Bambu',
      proposed: 1,
      confirmed: 1,
      opened: true,
      excluded: false,
    },
    {
      library_file_id: 10,
      filename: 'K1-Caps.3mf',
      path: 'y',
      folder: 'Caps / Alfio',
      proposed: 0,
      confirmed: 0,
      opened: false,
      excluded: false,
    },
  ],
};

const piece = (over: object) => ({
  yield_ids: [1, 2],
  image_yield_id: 1,
  names: ['Tappo porte (1)'],
  plates: 2,
  files: ['P1S-Caps-PETG.3mf'],
  file_count: 1,
  printer_models: ['P1S'],
  hand: 0,
  shape_id: null,
  shape: null,
  mixed: false,
  suggested_shape_id: null,
  quantity: null,
  component: null,
  ...over,
});

// The project page of a project (upstream panels behind the gate), or the product sheet.
function Page({ id }: { id: number }) {
  return (
    <Routes>
      <Route path="/farm/products/:id" element={<ProductPage />} />
      <Route
        path="*"
        element={
          <>
            <FarmProductGate projectId={id} panel>
              <p>Upstream production plan</p>
            </FarmProductGate>
            <FarmProductGate projectId={id}>
              <p>Upstream BOM</p>
            </FarmProductGate>
          </>
        }
      />
    </Routes>
  );
}

const sheet = (id: number) => {
  window.history.pushState({}, '', `/farm/products/${id}`);
  return render(<Page id={id} />);
};

describe('FarmProductGate', () => {
  let savedPiece: unknown;

  beforeEach(() => {
    savedPiece = undefined;
    server.use(
      http.get('/api/v1/farm/products/65/pieces', () =>
        HttpResponse.json({
          default_color: 'Nero',
          unshaped_parts: [{ code: 'C-0025', name: 'Case intero', quantity: 1 }],
          pieces: [
            piece({}),
            piece({
              yield_ids: [3],
              image_yield_id: 3,
              names: ['kapi'],
              shape_id: 2,
              shape: 'Tappo kapi kolu',
              quantity: 2,
              component: { code: 'C-0119', name: 'Tappo kapi kolu – Nero', color: 'Nero', color_hex: '111111' },
            }),
          ],
        }),
      ),
      http.get('/api/v1/farm/shapes', () => HttpResponse.json([])),
      http.get('/api/v1/farm/plan/settings', () =>
        HttpResponse.json({ url: '', tolerance: 80, colors: { nero: '111111', bianco: 'ffffff' } }),
      ),
      http.put('/api/v1/farm/products/65/pieces', async ({ request }) => {
        savedPiece = await request.json();
        return HttpResponse.json({ shape_id: 9, component_code: 'C-0200', quantity: 2, plates_confirmed: 1 });
      }),
    );
  });

  it('maps a piece of the product in one save, with its picture in the colour', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)));
    sheet(65);
    const name = await screen.findByLabelText('Piece name Tappo porte (1)');
    expect(name).toHaveValue('Tappo porte'); // the slicer's copy number is dropped
    expect(screen.getByText('1 to decide · 1 in the product')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Tappo kapi kolu' }).getAttribute('src')).toContain(
      '/farm/pieces/3/image?color=111111',
    );
    await user.type(screen.getByLabelText('Per product Tappo porte'), '2');
    await user.click(screen.getAllByRole('button', { name: 'Save' })[0]);
    await waitFor(() =>
      expect(savedPiece).toEqual({ yield_ids: [1, 2], quantity: 2, shape_name: 'Tappo porte', color: 'Nero' }),
    );
  });

  it('opens the product sheet from the project page of a product', async () => {
    server.use(http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)));
    window.history.pushState({}, '', '/projects/65');
    render(<Page id={65} />);
    expect(await screen.findByRole('heading', { name: 'Tappi Paraspifferi AMI' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/farm/products/65');
    expect(screen.queryByText('Upstream production plan')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Photo, notes and print history' })).toHaveAttribute(
      'href',
      '/projects/65?view=project',
    );
  });

  it('keeps the project page of a product with ?view=project, linking back to the sheet', async () => {
    server.use(http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)));
    window.history.pushState({}, '', '/projects/65?view=project');
    render(<Page id={65} />);
    expect(await screen.findByRole('link', { name: 'Open the product sheet' })).toHaveAttribute('href', '/farm/products/65');
    expect(screen.queryByText('Upstream BOM')).not.toBeInTheDocument();
  });

  it('shows the product parts, plates and files on its sheet', async () => {
    server.use(http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)));
    sheet(65);
    expect(await screen.findByRole('heading', { name: 'Tappi Paraspifferi AMI' })).toBeInTheDocument();
    expect(screen.getByText(/TP-01 — ASIN B0TEST0065/)).toBeInTheDocument();
    expect(screen.getByText(/P1S · PETG · 8 pieces · 1h 30m · 60 g/)).toBeInTheDocument();
    expect(screen.getByText(/to slice/)).toBeInTheDocument();
    expect(screen.getByText('no confirmed plate')).toBeInTheDocument(); // the second part
    // Plates: each 3MF still used, opened in place (both still to look at here).
    const files = within(screen.getByRole('heading', { name: 'Plates' }).closest('section')!).getAllByRole('button');
    expect(files.map((b) => b.textContent)).toEqual([
      expect.stringContaining('P1S-Caps-PETG.3mf'),
      expect.stringContaining('K1-Caps.3mf'),
    ]);
    server.use(http.get('/api/v1/farm/library/files/10/plates', () => HttpResponse.json([])));
    await userEvent.setup().click(files[1]);
    expect(await screen.findByText('No plates in this file.')).toBeInTheDocument();
  });

  it('shows what is missing and makes the product a set', async () => {
    const user = userEvent.setup();
    let sent: unknown;
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    server.use(
      http.get('/api/v1/farm/products/65/view', () => HttpResponse.json({ ...view, kind: null })),
      http.put('/api/v1/farm/products/65/kind', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ project_id: 65, kind: 'set' });
      }),
    );
    sheet(65);
    expect(await screen.findByText('Choose set or kit')).toBeInTheDocument();
    expect(await screen.findByText('1 piece to decide')).toBeInTheDocument();
    expect(screen.getByText('C-0119 has no confirmed plate')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Set printed together' }));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(sent).toEqual({ kind: 'set' }));
    confirm.mockRestore();
  });

  it('a set shows its pieces per set and asks how many per set', async () => {
    const recipe = [
      { shape_id: 1, shape: 'Tappo Bouchon porte', quantity: 2 },
      { shape_id: 2, shape: 'Tappo kapi kolu', quantity: 1 },
    ];
    server.use(
      http.get('/api/v1/farm/products/65/view', () =>
        HttpResponse.json({ ...view, kind: 'set', parts: [{ ...view.parts[0], shape_id: null, recipe }] }),
      ),
      http.get('/api/v1/farm/products/65/pieces', () =>
        HttpResponse.json({
          kind: 'set',
          set: { code: 'C-0118', name: 'Tappi', color: 'Nero', color_hex: '111111' },
          default_color: 'Nero',
          unshaped_parts: [],
          pieces: [piece({ quantity: null })],
        }),
      ),
    );
    sheet(65);
    expect(await screen.findByText('Per set: 2× Tappo Bouchon porte + 1× Tappo kapi kolu')).toBeInTheDocument();
    expect(await screen.findByLabelText('Per set Tappo porte')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Set printed together' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('prints the product now, also a part Seller Hub does not ask for', async () => {
    const user = userEvent.setup();
    let sent: unknown;
    const plate = {
      plate_id: 4,
      library_file_id: 9,
      file: 'Stampa 3D/Caps/P1S-Caps-PETG.3mf',
      plate: 1,
      printer_model: 'P1S',
      material: 'PETG',
      yield: 8,
      print_time_seconds: 5400,
      filament_grams: 60,
      sliced: true,
      score: 1,
      printers: [{ id: 3, name: 'P1S', idle: true, material_ok: true, color_ok: false }],
    };
    const row = (code: string, name: string, plates: (typeof plate)[]) => ({
      code, name, status: null, to_print: 0, stock: null, need: null, days_left: null, color: 'Nero',
      color_hex: '111111', shape: null, shape_id: null, known: true, products: [], in_queue: 0, missing: 0, plates,
    });
    server.use(
      http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)),
      http.get('/api/v1/farm/plan/products/65', () =>
        HttpResponse.json({
          fetched_at: null, error: null, tolerance: 80,
          components: [row('C-0118', 'Tappo Bouchon porte – Nero', [plate]), row('C-0119', 'Tappo kapi kolu – Nero', [])],
        }),
      ),
      http.post('/api/v1/farm/jobs', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json([]);
      }),
    );
    sheet(65);
    await user.click(await screen.findByRole('button', { name: 'Print now' }));
    expect(await screen.findByText(/^One print: 8 pieces with the proposed plate ·/)).toBeInTheDocument();
    expect(screen.getByText('Components without a confirmed plate are left out.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Confirm and queue 1 print' }));
    await waitFor(() => expect(sent).toEqual({ jobs: [{ component_code: 'C-0118', plate_ids: [4] }] }));
  });

  it('excludes a 3MF of the linked folders and shows the excluded ones in grey', async () => {
    const user = userEvent.setup();
    let sent: unknown;
    server.use(
      http.get('/api/v1/farm/products/65/view', () =>
        HttpResponse.json({ ...view, files: [view.files[0], { ...view.files[1], excluded: true }] }),
      ),
      http.put('/api/v1/farm/products/65/files/9', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ library_file_id: 9, path: 'x', excluded: true });
      }),
    );
    sheet(65);
    const include = await screen.findByRole('button', { name: 'Include K1-Caps.3mf' });
    expect(include.closest('li')).toHaveClass('opacity-50');
    expect(screen.getByText('excluded')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Exclude P1S-Caps-PETG.3mf' }));
    await waitFor(() => expect(sent).toEqual({ excluded: true }));
  });

  it('edits the components of a kit: picture, colour, quantity, add and remove', async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    server.use(
      http.get('/api/v1/farm/products/65/view', () => HttpResponse.json({ ...view, kind: 'kit' })),
      http.get('/api/v1/farm/components', () =>
        HttpResponse.json([
          { code: 'C-0200', name: 'Vite M3', shape_id: null, shape: null, color: null, retired: false },
          { code: 'C-0118', name: 'Tappo Bouchon porte – Nero', shape_id: 1, shape: null, color: 'Nero', retired: false },
        ]),
      ),
      http.patch('/api/v1/farm/components/C-0118', async ({ request }) => {
        sent.push(['patch', await request.json()]);
        return HttpResponse.json({});
      }),
      http.put('/api/v1/farm/products/65/parts/:code', async ({ request, params }) => {
        sent.push([params.code, await request.json()]);
        return HttpResponse.json({});
      }),
    );
    sheet(65);
    const picture = await screen.findByRole('img', { name: 'Tappo Bouchon porte – Nero' });
    await waitFor(() => expect(picture.getAttribute('src')).toContain('/farm/components/C-0118/image?c=111111'));
    await user.selectOptions(screen.getByLabelText('Colour C-0118'), 'Bianco');
    const qty = screen.getByLabelText('Per unit C-0119');
    await user.clear(qty);
    await user.type(qty, '4');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Remove C-0118' }));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('It is retired (the code stays in the history)'));
    await user.selectOptions(screen.getByLabelText('Add component'), 'C-0200'); // C-0118 is already in
    await user.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(sent).toEqual([
        ['patch', { color: 'Bianco' }],
        ['C-0119', { quantity: 4 }],
        ['C-0118', { quantity: 0 }],
        ['C-0200', { quantity: 1 }],
      ]),
    );
    expect(screen.queryByRole('option', { name: /C-0118 Tappo/ })).not.toBeInTheDocument();
    confirm.mockRestore();
  });

  it('shows the printers of each model with a plate, and the models without one', async () => {
    const fit = (id: number, name: string, idle: boolean, ok: boolean) =>
      ({ id, name, idle, material_ok: ok, color_ok: ok });
    const plate = (file: string, model: string, printers: unknown[]) => ({
      plate_id: 1, library_file_id: 9, file, plate: 1, printer_model: model, material: 'PETG', yield: 8,
      print_time_seconds: null, filament_grams: null, sliced: true, score: 1, printers,
    });
    server.use(
      http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)),
      http.get('/api/v1/farm/plan/products/65', () =>
        HttpResponse.json({
          fetched_at: null, error: null, tolerance: 80,
          components: [
            { code: 'C-0118', name: 'Tappo', plates: [
              plate('Caps/P1S-Caps-PETG.3mf', 'P1S', [fit(3, 'P1S uno', true, true), fit(4, 'P1S due', false, true)]),
              plate('Caps/A1-Caps.3mf', 'A1', []),
            ] },
          ],
        }),
      ),
      http.get('/api/v1/printers/', () =>
        HttpResponse.json([
          { id: 3, name: 'P1S uno', model: 'P1S', is_active: true },
          { id: 7, name: 'K1', model: 'K1', is_active: true },
          { id: 8, name: 'Vecchia', model: 'X1C', is_active: false },
        ]),
      ),
    );
    sheet(65);
    const section = (await screen.findByRole('heading', { name: 'Printers' })).closest('section')!;
    expect(await within(section).findByText('C-0118 · P1S-Caps-PETG.3mf · Plate 1 · PETG · 8 per print')).toBeInTheDocument();
    expect(within(section).getByText('P1S uno')).toBeInTheDocument();
    expect(within(section).getByText('P1S due')).toBeInTheDocument();
    expect(within(section).getByText('no active printer of this model')).toBeInTheDocument(); // A1
    expect(within(section).getByText('No plate for: K1')).toBeInTheDocument(); // the inactive X1C is left out
  });

  it('links and unlinks the product folders', async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    server.use(
      http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)),
      http.get('/api/v1/library/folders', () =>
        HttpResponse.json([
          { id: 3, name: 'Caps', children: [{ id: 4, name: 'Bambu', children: [] }] },
          { id: 5, name: 'Specchietto', children: [] },
        ]),
      ),
      http.put('/api/v1/farm/products/65/folders/:id', async ({ request, params }) => {
        sent.push([params.id, await request.json()]);
        return HttpResponse.json({ folder_id: Number(params.id), project_ids: [] });
      }),
    );
    sheet(65);
    const section = (await screen.findByRole('heading', { name: 'Linked folders' })).closest('section')!;
    expect(within(section).getByText('Caps')).toBeInTheDocument();
    const pick = within(section).getByLabelText('Link a folder…');
    await waitFor(() => expect(within(pick).getByRole('option', { name: 'Caps / Bambu' })).toBeInTheDocument());
    expect(within(pick).queryByRole('option', { name: 'Caps' })).not.toBeInTheDocument(); // already linked
    await user.selectOptions(pick, 'Specchietto');
    await user.click(within(section).getByRole('button', { name: 'Unlink' }));
    await waitFor(() => expect(sent).toEqual([['5', { linked: true }], ['3', { linked: false }]]));
    confirm.mockRestore();
  });

  it('disables, enables and removes the product from its sheet', async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    server.use(
      http.get('/api/v1/farm/products/65/view', () =>
        sent.length > 1
          ? HttpResponse.json({ detail: 'Not a product' }, { status: 404 }) // removed
          : HttpResponse.json({ ...view, disabled: true }),
      ),
      http.put('/api/v1/farm/products/65/active', async ({ request }) => {
        sent.push(['active', await request.json()]);
        return HttpResponse.json({ project_id: 65, disabled: false });
      }),
      http.delete('/api/v1/farm/products/65', () => {
        sent.push(['delete']);
        return HttpResponse.json({ project_id: 65, retired: [] });
      }),
    );
    sheet(65);
    expect(await screen.findByText(/Disabled: kept with its data/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Enable again' }));
    await user.click(screen.getByRole('button', { name: 'Remove product' }));
    await waitFor(() => expect(sent).toEqual([['active', { active: true }], ['delete']]));
    await waitFor(() => expect(window.location.pathname).toBe('/farm/products'));
    confirm.mockRestore();
  });

  it('takes a component or a wrong shape out of a set, saying what happens to the code', async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const recipe = [
      { shape_id: 1, shape: 'Tappo Bouchon porte', quantity: 2 },
      { shape_id: 7, shape: 'Forma sbagliata', quantity: 1 },
    ];
    const setPart = {
      ...view.parts[0],
      shape_id: null,
      recipe,
    };
    server.use(
      http.get('/api/v1/farm/products/65/view', () => HttpResponse.json({ ...view, kind: 'set', parts: [setPart] })),
      http.put('/api/v1/farm/products/65/recipe/:shape', async ({ request, params }) => {
        sent.push(['recipe', params.shape, await request.json()]);
        return HttpResponse.json({ component_code: 'C-0118', recipe: [] });
      }),
      http.put('/api/v1/farm/products/65/parts/:code', async ({ request, params }) => {
        sent.push(['part', params.code, await request.json()]);
        return HttpResponse.json({});
      }),
    );
    sheet(65);
    await user.click(await screen.findByRole('button', { name: 'Take out of the set Forma sbagliata' }));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('Take Forma sbagliata out of the set?'));
    await user.click(screen.getByRole('button', { name: 'Remove C-0118' }));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('It is retired'));
    await waitFor(() =>
      expect(sent).toEqual([['recipe', '7', { quantity: 0 }], ['part', 'C-0118', { quantity: 0 }]]),
    );
    confirm.mockRestore();
  });

  it('sets a piece aside as not part of the product, and puts it back', async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    server.use(
      http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)),
      http.get('/api/v1/farm/products/65/pieces', () =>
        HttpResponse.json({
          kind: null,
          set: null,
          default_color: 'Nero',
          unshaped_parts: [],
          pieces: [
            piece({}),
            piece({ yield_ids: [9], image_yield_id: 9, names: ['Klamka Lewa'], quantity: 0, not_part: true }),
          ],
        }),
      ),
      http.put('/api/v1/farm/products/65/pieces', async ({ request }) => {
        sent.push(['save', await request.json()]);
        return HttpResponse.json({ shape_id: null, component_code: null, quantity: 0, plates_confirmed: 0 });
      }),
      http.put('/api/v1/farm/products/65/pieces/restore', async ({ request }) => {
        sent.push(['restore', await request.json()]);
        return HttpResponse.json({ project_id: 65, not_part: 0 });
      }),
    );
    sheet(65);
    expect(await screen.findByText('1 to decide · 0 in the product')).toBeInTheDocument(); // the aside one not counted
    expect(screen.getByText('Not part of the product (1)')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Not part of it Tappo porte' }));
    await user.click(screen.getByText('Not part of the product (1)'));
    await user.click(screen.getByRole('button', { name: 'Put back Klamka Lewa' }));
    await waitFor(() =>
      expect(sent).toEqual([
        ['save', { yield_ids: [1, 2], quantity: 0 }],
        ['restore', { yield_ids: [9] }],
      ]),
    );
  });

  it('warns when no plate holds a whole set, and splits it into a kit after confirming', async () => {
    const user = userEvent.setup();
    let sent: unknown;
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const recipe = [
      { shape_id: 1, shape: 'Base', quantity: 2 },
      { shape_id: 2, shape: 'Cornice', quantity: 2 },
    ];
    server.use(
      http.get('/api/v1/farm/products/65/view', () =>
        HttpResponse.json({ ...view, kind: 'set', parts: [{ ...view.parts[1], code: 'C-0140', shape_id: null, recipe }] }),
      ),
      http.put('/api/v1/farm/products/65/kind', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ project_id: 65, kind: 'kit' });
      }),
    );
    sheet(65);
    expect(await screen.findByText(/No plate holds every piece of the set/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Kit of parts printed apart' }));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('The set code C-0140 is retired'));
    await waitFor(() => expect(sent).toEqual({ kind: 'kit' }));
    confirm.mockRestore();
  });

  it('tells a piece is the same shape as another one of the product', async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    server.use(
      http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)),
      http.get('/api/v1/farm/products/65/pieces', () =>
        HttpResponse.json({
          kind: 'kit',
          set: null,
          default_color: 'Bianco',
          unshaped_parts: [],
          pieces: [
            piece({ yield_ids: [1], image_yield_id: 1, names: ['Base (1)'], shape_id: 58, shape: 'Base', quantity: 2 }),
            piece({ yield_ids: [2], image_yield_id: 2, names: ['Cornice v15'], shape_id: 57, shape: 'Cornice', quantity: 2 }),
            piece({ yield_ids: [3], image_yield_id: 3, names: ['Tappo nuovo'] }),
          ],
        }),
      ),
      http.post('/api/v1/farm/shapes/58/merge', async ({ request }) => {
        sent.push(['merge', await request.json()]);
        return HttpResponse.json({ merged: 58, into: 57, objects: 4, components_merged: [] });
      }),
      http.put('/api/v1/farm/products/65/pieces', async ({ request }) => {
        sent.push(['piece', await request.json()]);
        return HttpResponse.json({ shape_id: 58, component_code: 'C-0118', quantity: 2, plates_confirmed: 0 });
      }),
    );
    sheet(65);
    await user.selectOptions(await screen.findByLabelText('Same shape as… Base'), 'Cornice');
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('Is "Base" the same piece as "Cornice"'));
    await user.selectOptions(screen.getByLabelText('Same shape as… Tappo nuovo'), 'Base');
    await waitFor(() =>
      expect(sent).toEqual([
        ['merge', { into: 57 }],
        ['piece', { yield_ids: [3], quantity: 2, shape_id: 58 }], // its geometry joins the product's Base
      ]),
    );
    confirm.mockRestore();
  });

  it('keeps the upstream panels for normal projects', async () => {
    server.use(
      http.get('/api/v1/farm/products/7/view', () => HttpResponse.json({ detail: 'Not a product' }, { status: 404 })),
    );
    window.history.pushState({}, '', '/projects/7');
    render(<Page id={7} />);
    expect(await screen.findByText('Upstream production plan')).toBeInTheDocument();
    expect(screen.getByText('Upstream BOM')).toBeInTheDocument();
  });
});
