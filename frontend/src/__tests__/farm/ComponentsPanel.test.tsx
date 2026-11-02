/**
 * Printfarm (fork): components grouped by shape. All data is made up.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ComponentsPanel } from '../../farm/ComponentsPanel';

const base = { retired: false };
const components = [
  { ...base, code: 'C-0101', name: 'Tartufo sopra – Nero', shape_id: 1, shape: 'Tartufo sopra', color: 'Nero' },
  { ...base, code: 'C-0102', name: 'Tartufo sopra – Bianco', shape_id: 1, shape: 'Tartufo sopra', color: 'Bianco' },
  { ...base, code: 'C-0103', name: 'Gancio SX – Nero', shape_id: 2, shape: 'Gancio SX', color: 'Nero' },
  { ...base, code: 'C-0010', name: 'Set intero vecchio', shape_id: null, shape: null, color: null, retired: true },
  { ...base, code: 'C-0020', name: 'Portachiavi', shape_id: null, shape: null, color: null },
];
const products = [
  { project_id: 1, name: 'Set di 2 tartufi neri', parts: [{ component_code: 'C-0101', quantity: 2 }] },
  { project_id: 2, name: 'Set di 5 tartufi neri', parts: [{ component_code: 'C-0101', quantity: 5 }] },
  { project_id: 3, name: 'Portachiavi auto', parts: [{ component_code: 'C-0020', quantity: 1 }] },
].map((p) => ({ ...p, skus: [], asins: [], updated_at: '', parts: p.parts.map((x) => ({ ...x, name: '', plates: [] })) }));
const plate = (id: number, quantity: number) => ({
  id,
  library_file_id: 4,
  plate_index: id,
  status: 'confirmed',
  yields: [{ object_name: 'sopra', quantity, shape_id: 1, component_code: null }],
});

describe('ComponentsPanel', () => {
  let patched: { code: string; body: unknown } | undefined;

  beforeEach(() => {
    patched = undefined;
    server.use(
      http.get('/api/v1/farm/products', () => HttpResponse.json({ generated_at: '', products })),
      http.get('/api/v1/farm/components', () => HttpResponse.json(components)),
      http.get('/api/v1/farm/shapes', () =>
        HttpResponse.json([
          { id: 2, name: 'Gancio SX' },
          { id: 1, name: 'Tartufo sopra' },
        ]),
      ),
      http.get('/api/v1/farm/plates', () => HttpResponse.json([plate(1, 15), plate(2, 2)])),
      http.patch('/api/v1/farm/components/:code', async ({ params, request }) => {
        patched = { code: String(params.code), body: await request.json() };
        return HttpResponse.json(components[0]);
      }),
    );
  });

  it('groups colours under their shape with plates and products', async () => {
    const user = userEvent.setup();
    render(<ComponentsPanel />);
    const truffle = (await screen.findByText('Tartufo sopra')).closest('li')!;
    expect(within(truffle).getByText('2 plates')).toBeInTheDocument();
    expect(within(truffle).getByText('C-0101')).toBeInTheDocument();
    expect(within(truffle).getByText('2× Set di 2 tartufi neri · 5× Set di 5 tartufi neri')).toBeInTheDocument();
    expect(within(truffle).getByText('not used in any product')).toBeInTheDocument(); // the white one

    const hook = screen.getByText('Gancio SX').closest('li')!;
    expect(within(hook).getByText('no confirmed plate')).toBeInTheDocument();
    // Retired components are hidden until asked for.
    expect(screen.queryByText('C-0010')).not.toBeInTheDocument();
    await user.click(screen.getByLabelText('Show retired (1)'));
    expect(screen.getByText('C-0010')).toBeInTheDocument();
  });

  it('filters and searches', async () => {
    const user = userEvent.setup();
    render(<ComponentsPanel />);
    await screen.findByText('Tartufo sopra');

    await user.selectOptions(screen.getByLabelText('Filter'), 'Not used in any product');
    expect(screen.getByText('C-0102')).toBeInTheDocument();
    expect(screen.queryByText('C-0101')).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Filter'), 'Without a plate');
    expect(screen.getByText('C-0103')).toBeInTheDocument();
    expect(screen.queryByText('C-0101')).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Filter'), 'All');
    await user.type(screen.getByRole('searchbox'), 'portachiavi');
    expect(screen.getByText('C-0020')).toBeInTheDocument();
    expect(screen.queryByText('Tartufo sopra')).not.toBeInTheDocument();
  });

  it('asks before retiring', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    const user = userEvent.setup();
    render(<ComponentsPanel />);
    const row = (await screen.findByText('C-0103')).closest('li')!;
    await user.click(within(row).getByRole('button', { name: 'Retire' }));
    expect(patched).toBeUndefined();
    await user.click(within(row).getByRole('button', { name: 'Retire' }));
    await waitFor(() => expect(patched).toEqual({ code: 'C-0103', body: { retired: true } }));
    expect(confirm).toHaveBeenCalledTimes(2);
    confirm.mockRestore();
  });
});
