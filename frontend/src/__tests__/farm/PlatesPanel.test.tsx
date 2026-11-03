/**
 * Printfarm (fork): the plates of the NAS 3MF files, read-only (pieces are linked per product). Data is made up.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PlatesPanel } from '../../farm/PlatesPanel';

const file = (id: number, path: string, slicer = 'bambu_studio') => ({
  library_file_id: id,
  path,
  status: 'ok',
  slicer,
  printer_model: 'A1',
});

describe('PlatesPanel', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/farm/library/files', () =>
        HttpResponse.json([
          file(1, 'Stampa 3D/Truffle/A1 saro/A1-truffle.3mf'),
          file(2, 'Stampa 3D/Truffle/A1 saro/Old/Tartufi A1.3mf'),
          file(3, 'Stampa 3D/Truffle/Trurffle_sopra.3mf', 'cad'),
          file(4, 'Stampa 3D/Ganci/Ganci.3mf', 'orcaslicer'),
        ]),
      ),
      http.get('/api/v1/farm/plates/summary', () =>
        HttpResponse.json([
          { library_file_id: 1, proposed: 2, confirmed: 0 },
          { library_file_id: 4, proposed: 0, confirmed: 1 },
        ]),
      ),
      http.get('/api/v1/farm/shapes', () =>
        HttpResponse.json([
          { id: 5, name: 'Tartufo sopra' },
          { id: 6, name: 'Tartufo sotto' },
        ]),
      ),
      http.get('/api/v1/farm/library/files/1/plates', () =>
        HttpResponse.json([
          {
            id: 11,
            library_file_id: 1,
            plate_index: 1,
            name: null,
            printer_model: 'Bambu Lab A1',
            target_model: 'A1',
            materials: ['PLA'],
            nozzle_diameter: 0.4,
            print_time_seconds: 3 * 3600 + 20 * 60,
            filament_grams: 151.4,
            sliced_file_id: null,
            status: 'proposed',
            warning: null,
            yields: [
              { object_name: 'Trurffle_sopra k2', quantity: 15, shape_id: null, suggested_shape_id: 5, component_code: null },
              { object_name: 'Trurffle_sotto (1) (1)', quantity: 15, shape_id: null, suggested_shape_id: null, component_code: null },
            ],
          },
        ]),
      ),
    );
  });

  it('lists production files and shows their plates, read-only', async () => {
    const user = userEvent.setup();
    render(<PlatesPanel />);

    // Old/ and CAD files are hidden.
    expect(await screen.findByText('A1-truffle.3mf')).toBeInTheDocument();
    expect(screen.getByText('Ganci.3mf')).toBeInTheDocument();
    expect(screen.queryByText('Tartufi A1.3mf')).not.toBeInTheDocument();
    expect(screen.queryByText('Trurffle_sopra.3mf')).not.toBeInTheDocument();
    expect(screen.getByText(/2 plates/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /A1-truffle\.3mf/ }));
    expect(await screen.findByText('Trurffle_sopra k2')).toBeInTheDocument();
    expect(screen.getByText(/3h 20m · 151 g/)).toBeInTheDocument();
    // Pieces are linked on each product sheet: no shape to pick and nothing to confirm here.
    expect(screen.queryByRole('combobox', { name: /Shape/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirm plate' })).not.toBeInTheDocument();
    // The plate preview comes from the 3MF and opens the 3D viewer.
    const preview = screen.getByRole('button', { name: 'Open in the 3D viewer Plate 1' });
    expect(preview.querySelector('img')?.getAttribute('src')).toContain('/library/files/1/plate-thumbnail/1');
  });

  it('shows every file on request', async () => {
    const user = userEvent.setup();
    render(<PlatesPanel />);
    await screen.findByText('A1-truffle.3mf');
    await user.click(screen.getByRole('checkbox'));
    expect(screen.getByText('Tartufi A1.3mf')).toBeInTheDocument();
  });
});
