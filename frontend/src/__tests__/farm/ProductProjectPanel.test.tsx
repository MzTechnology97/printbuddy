/**
 * Printfarm (fork): the project page of a product shows the farm view instead of upstream's panels.
 * All data is made up.
 */

import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
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
    },
    {
      library_file_id: 10,
      filename: 'K1-Caps.3mf',
      path: 'y',
      folder: 'Caps / Alfio',
      proposed: 0,
      confirmed: 0,
      opened: false,
    },
  ],
};

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

  it('keeps the upstream panels for normal projects', async () => {
    server.use(
      http.get('/api/v1/farm/products/7/view', () => HttpResponse.json({ detail: 'Not a product' }, { status: 404 })),
    );
    render(<Page id={7} />);
    expect(await screen.findByText('Upstream production plan')).toBeInTheDocument();
    expect(screen.getByText('Upstream BOM')).toBeInTheDocument();
  });
});
