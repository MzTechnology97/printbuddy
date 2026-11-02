/**
 * Printfarm (fork): confirming the plates proposed from NAS 3MF files. All data is made up.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
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
  let confirmed: unknown;

  beforeEach(() => {
    confirmed = undefined;
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
      http.put('/api/v1/farm/plates/11', async ({ request }) => {
        confirmed = await request.json();
        return HttpResponse.json({});
      }),
    );
  });

  it('lists production files to confirm and confirms a plate with the proposed shapes', async () => {
    const user = userEvent.setup();
    render(<PlatesPanel />);

    // Old/ and CAD files are hidden; a file with everything confirmed is not "to confirm".
    expect(await screen.findByText('A1-truffle.3mf')).toBeInTheDocument();
    expect(screen.queryByText('Tartufi A1.3mf')).not.toBeInTheDocument();
    expect(screen.queryByText('Trurffle_sopra.3mf')).not.toBeInTheDocument();
    expect(screen.queryByText('Ganci.3mf')).not.toBeInTheDocument();
    expect(screen.getByText(/2 to confirm · 0 confirmed/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /A1-truffle\.3mf/ }));
    const top = await screen.findByLabelText('Shape Trurffle_sopra k2');
    expect(top).toHaveValue('5');
    expect(screen.getByText('proposed')).toBeInTheDocument();
    expect(screen.getByText(/3h 20m · 151 g/)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Shape Trurffle_sotto (1) (1)'), 'Tartufo sotto');
    await user.click(screen.getByRole('button', { name: 'Confirm plate' }));
    await waitFor(() =>
      expect(confirmed).toEqual({
        yields: [
          { object_name: 'Trurffle_sopra k2', quantity: 15, shape_id: 5 },
          { object_name: 'Trurffle_sotto (1) (1)', quantity: 15, shape_id: 6 },
        ],
      }),
    );
  });

  it('shows confirmed files on request', async () => {
    const user = userEvent.setup();
    render(<PlatesPanel />);
    await screen.findByText('A1-truffle.3mf');
    await user.selectOptions(screen.getByLabelText('Filter'), 'Confirmed');
    expect(screen.getByText('Ganci.3mf')).toBeInTheDocument();
    expect(screen.queryByText('A1-truffle.3mf')).not.toBeInTheDocument();
  });
});
