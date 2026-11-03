/**
 * Printfarm (fork): the project page of a product shows the farm view instead of upstream's panels.
 * All data is made up.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FarmProductGate } from '../../farm/ProductProjectPanel';

const part = { shape_id: 1, color: 'Nero', retired: false, quantity: 2 };
const view = {
  project_id: 65,
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

function Page({ id }: { id: number }) {
  return (
    <>
      <FarmProductGate projectId={id} panel>
        <p>Upstream production plan</p>
      </FarmProductGate>
      <FarmProductGate projectId={id}>
        <p>Upstream BOM</p>
      </FarmProductGate>
    </>
  );
}

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
    render(<Page id={65} />);
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

  it('shows the product parts, plates and files instead of the upstream panels', async () => {
    server.use(http.get('/api/v1/farm/products/65/view', () => HttpResponse.json(view)));
    render(<Page id={65} />);
    expect(await screen.findByText('Product')).toBeInTheDocument();
    expect(screen.queryByText('Upstream production plan')).not.toBeInTheDocument();
    expect(screen.queryByText('Upstream BOM')).not.toBeInTheDocument();
    expect(screen.getByText(/TP-01 — ASIN B0TEST0065/)).toBeInTheDocument();
    expect(screen.getByText(/P1S · PETG · 8 pieces · 1h 30m · 60 g/)).toBeInTheDocument();
    expect(screen.getByText(/to slice/)).toBeInTheDocument();
    expect(screen.getByText('no confirmed plate')).toBeInTheDocument(); // the second part
    const links = screen.getAllByRole('link', { name: 'Confirm plates' });
    expect(links.map((l) => l.getAttribute('href'))).toEqual([
      '/farm/products?tab=plates&q=P1S-Caps-PETG.3mf',
      '/farm/products?tab=plates&q=K1-Caps.3mf',
    ]);
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
    render(<Page id={65} />);
    const include = await screen.findByRole('button', { name: 'Include K1-Caps.3mf' });
    expect(include.closest('li')).toHaveClass('opacity-50');
    expect(screen.getByText('excluded')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Exclude P1S-Caps-PETG.3mf' }));
    await waitFor(() => expect(sent).toEqual({ excluded: true }));
  });

  it('keeps the upstream panels for normal projects', async () => {
    server.use(
      http.get('/api/v1/farm/products/7/view', () => HttpResponse.json({ detail: 'Not a product' }, { status: 404 })),
    );
    render(<Page id={7} />);
    expect(await screen.findByText('Upstream production plan')).toBeInTheDocument();
    expect(screen.getByText('Upstream BOM')).toBeInTheDocument();
  });
});
