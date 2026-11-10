/**
 * Tests for the printfarm (fork) Products page. All data is made up.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ProductsPage } from '../../farm/ProductsPage';

const products = [
  {
    project_id: 7,
    name: 'Portaoggetti X',
    skus: ['PX-01', 'PX-02'],
    asins: ['B0TEST0001'],
    updated_at: '2026-01-01T00:00:00Z',
    parts: [
      { component_code: 'C-0001', name: 'Base X', quantity: 1, plates: [] },
      { component_code: 'C-0002', name: 'Gancio', quantity: 2, plates: [] },
    ],
  },
];
const component = { shape_id: null, shape: null, color: null, retired: false };
const components = [
  { ...component, code: 'C-0001', name: 'Base X' },
  { ...component, code: 'C-0002', name: 'Gancio' },
  { ...component, code: 'C-0003', name: 'Set intero', retired: true },
];

describe('ProductsPage', () => {
  let saved: unknown;
  let created: unknown;

  beforeEach(() => {
    saved = undefined;
    created = undefined;
    server.use(
      http.get('/api/v1/farm/shapes', () => HttpResponse.json([{ id: 5, name: 'Tartufo sopra' }])),
      http.post('/api/v1/farm/components', async ({ request }) => {
        created = await request.json();
        return HttpResponse.json({ ...component, code: 'C-0004', name: 'Tartufo sopra – Nero', shape_id: 5 });
      }),
      http.get('/api/v1/farm/products', () => HttpResponse.json({ generated_at: '2026-01-01T00:00:00Z', products })),
      http.get('/api/v1/farm/products/overview', () =>
        HttpResponse.json([{ project_id: 7, has_cover: false, queued: 3, printing: 1, printed_recent: 12 }]),
      ),
      http.get('/api/v1/farm/components', () => HttpResponse.json(components)),
      http.get('/api/v1/farm/projects', () => HttpResponse.json([])),
      http.put('/api/v1/farm/products/:id', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ project_id: 7 });
      }),
    );
  });

  it('creates a shape + colour component and hides retired ones from the parts', async () => {
    const user = userEvent.setup();
    render(<ProductsPage />);
    await user.click(await screen.findByRole('button', { name: 'Edit' }));

    const picker = screen.getAllByLabelText('Choose component…')[0];
    const offered = Array.from(picker.querySelectorAll('option')).map((o) => o.value);
    expect(offered).toEqual(['', 'C-0001', 'C-0002']);

    await user.selectOptions(screen.getByLabelText('Shape'), 'Tartufo sopra');
    await user.type(screen.getByLabelText('Colour'), 'Nero');
    await user.click(screen.getByRole('button', { name: 'Create component' }));
    await waitFor(() => expect(created).toEqual({ shape_id: 5, color: 'Nero' }));
  });

  it('shows products with SKUs, parts and print activity', async () => {
    render(<ProductsPage />);
    expect(await screen.findByText('Portaoggetti X')).toBeInTheDocument();
    expect(screen.getByText(/PX-01 · PX-02/)).toBeInTheDocument();
    expect(screen.getByText(/Queued 3/)).toBeInTheDocument();
    expect(screen.getByText(/Printed in 7 days 12/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Prints and queue' })).toHaveAttribute('href', '/projects/7?view=project');
    // Components and pieces live on each product's sheet: only Products and Plates remain.
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Products', 'Plates']);
  });

  it('shows which products are still to sort out', async () => {
    const ready = {
      ...products[0],
      project_id: 9,
      name: 'Pronto',
      kind: 'set',
      parts: [{ component_code: 'C-0009', name: 'Set', quantity: 1, plates: [{ library_file_id: 1, plate: 1 }] }],
    };
    server.use(
      http.get('/api/v1/farm/products', () =>
        HttpResponse.json({ generated_at: '2026-01-01T00:00:00Z', products: [...products, ready] }),
      ),
    );
    const user = userEvent.setup();
    render(<ProductsPage />);
    expect(await screen.findByText('Pronto')).toBeInTheDocument();
    expect(screen.getByText('ready')).toBeInTheDocument();
    await user.click(screen.getByLabelText('Only to sort out'));
    expect(screen.queryByText('Pronto')).not.toBeInTheDocument();
    expect(screen.getByText('Portaoggetti X')).toBeInTheDocument(); // no type chosen yet
  });

  it('marks disabled products and lists them last', async () => {
    const off = { ...products[0], project_id: 10, name: 'Fuori produzione', disabled: true };
    server.use(
      http.get('/api/v1/farm/products', () =>
        HttpResponse.json({ generated_at: '2026-01-01T00:00:00Z', products: [off, ...products] }),
      ),
    );
    render(<ProductsPage />);
    expect(await screen.findByText('disabled')).toBeInTheDocument();
    const names = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
    expect(names[names.length - 1]).toContain('Fuori produzione');
  });

  it('filters products by name, SKU, ASIN or component', async () => {
    const lamp = {
      ...products[0],
      project_id: 8,
      name: 'Lampada',
      skus: ['LA-01'],
      asins: ['B0TEST0002'],
      parts: [{ component_code: 'C-0003', name: 'Paralume', quantity: 1, plates: [] }],
    };
    server.use(
      http.get('/api/v1/farm/products', () =>
        HttpResponse.json({ generated_at: '2026-01-01T00:00:00Z', products: [...products, lamp] }),
      ),
    );
    const user = userEvent.setup();
    render(<ProductsPage />);
    await screen.findByText('Lampada');
    const search = screen.getByRole('searchbox', { name: 'Search name, SKU, ASIN or component' });

    for (const [query, visible] of [
      ['lampada', 'Lampada'],
      ['px-02', 'Portaoggetti X'],
      ['b0test0002', 'Lampada'],
      ['gancio', 'Portaoggetti X'],
      ['c-0003', 'Lampada'],
    ]) {
      await user.clear(search);
      await user.type(search, query);
      expect(screen.getByRole('heading', { name: visible })).toBeInTheDocument();
      expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual([visible]);
    }
    await user.clear(search);
    await user.type(search, 'nessuno');
    expect(screen.getByText('No product matches the search.')).toBeInTheDocument();
  });

  it('saves edited SKUs and part quantities', async () => {
    const user = userEvent.setup();
    render(<ProductsPage />);
    await user.click(await screen.findByRole('button', { name: 'Edit' }));

    const skus = screen.getByLabelText('Amazon SKUs');
    await user.clear(skus);
    await user.type(skus, 'PX-01, PX-03');
    const qty = screen.getAllByLabelText('Quantity')[1];
    await user.clear(qty);
    await user.type(qty, '4');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(saved).toEqual({
        name: 'Portaoggetti X',
        skus: ['PX-01', 'PX-03'],
        asins: ['B0TEST0001'],
        parts: [
          { component_code: 'C-0001', quantity: 1 },
          { component_code: 'C-0002', quantity: 4 },
        ],
        variants: [],
      }),
    );
  });

  it('saves another listing of the product as a pack in a colour', async () => {
    const user = userEvent.setup();
    render(<ProductsPage />);
    await user.click(await screen.findByRole('button', { name: 'Edit' }));
    await user.click(screen.getByRole('button', { name: 'Add listing' }));

    await user.type(screen.getAllByLabelText('Amazon SKUs')[1], 'PX-5N');
    await user.type(screen.getByLabelText('Colour (empty: as above)'), 'Nero');
    const pieces = screen.getByLabelText('Pieces per sale');
    await user.clear(pieces);
    await user.type(pieces, '5');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect((saved as { variants: unknown }).variants).toEqual([
        { skus: ['PX-5N'], asins: [], color: 'Nero', pieces: 5 },
      ]),
    );
  });
});

describe('ProductsPage · import from Seller Hub', () => {
  let posted: unknown;

  beforeEach(() => {
    posted = undefined;
    server.use(
      http.get('/api/v1/farm/shapes', () => HttpResponse.json([])),
      http.get('/api/v1/farm/products', () => HttpResponse.json({ generated_at: '2026-01-01T00:00:00Z', products })),
      http.get('/api/v1/farm/products/overview', () => HttpResponse.json([])),
      http.get('/api/v1/farm/components', () => HttpResponse.json(components)),
      http.get('/api/v1/farm/import/sellerhub/listings', () =>
        HttpResponse.json({
          listings: [
            {
              key: 'asin:B0FAKE0001',
              name: 'Supporto di prova',
              title: 'Supporto di prova, nero',
              skus: ['TEST-A1', 'TEST-A1-FBM'],
              asins: ['B0FAKE0001'],
              image_url: 'https://m.media-amazon.com/images/I/FAKEIMG01.jpg',
            },
            { key: 'asin:B0FAKE0002', name: 'Gancio di prova', title: 'Gancio di prova', skus: ['TEST-B1'], asins: ['B0FAKE0002'], image_url: null },
          ],
          excluded_inactive: 2,
          ignored: 1,
        }),
      ),
      http.post('/api/v1/farm/import/sellerhub/listings', async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({
          created: [{ key: 'asin:B0FAKE0002', project_id: 9, name: 'Gancio di prova' }],
          already_owned: [],
          not_found: [],
          covers_added: 0,
        });
      }),
    );
  });

  it('lists photo, SKU and title, and imports only the ticked listings', async () => {
    const user = userEvent.setup();
    render(<ProductsPage />);
    await user.click(await screen.findByRole('button', { name: /Import from Seller Hub/ }));

    const first = await screen.findByRole('checkbox', { name: /Supporto di prova, nero/ });
    expect(first).toHaveAccessibleName(/TEST-A1 · TEST-A1-FBM/);
    expect(screen.getByText('becomes “Supporto di prova”')).toBeInTheDocument();
    // The Amazon photo goes through Printbuddy, never straight to Amazon (CSP).
    const img = document.querySelector('img[src*="sellerhub/thumbnail"]');
    expect(img?.getAttribute('src')).toBe(
      '/api/v1/farm/import/sellerhub/thumbnail?url=' + encodeURIComponent('https://m.media-amazon.com/images/I/FAKEIMG01.jpg'),
    );
    expect(screen.getByText(/2 inactive on Amazon, 1 marked/)).toBeInTheDocument();

    const button = screen.getByRole('button', { name: 'Import 0 products' });
    expect(button).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /Gancio di prova/ }));
    await user.click(screen.getByRole('button', { name: 'Import 1 product' }));
    await waitFor(() => expect(posted).toEqual({ keys: ['asin:B0FAKE0002'] }));
  });
});
