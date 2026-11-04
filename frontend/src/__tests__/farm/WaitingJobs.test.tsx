/**
 * Printfarm (fork): Accoda jobs not in the print queue yet, on the Queue page. All data is made up.
 */

import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { WaitingJobs } from '../../farm/WaitingJobs';

const job = (id: number, extra: object) => ({
  id,
  component_code: `C-00${id}`,
  color: 'Nero',
  state: 'waiting',
  note: null,
  plate_ids: [id],
  plate_id: null,
  printer_id: null,
  printer: null,
  queue_item_id: null,
  pieces: 15,
  scheduled_time: null,
  created_at: '2026-01-01T10:00:00Z',
  slicing: null,
  ...extra,
});

describe('WaitingJobs', () => {
  it('shows the jobs still slicing with their % and why the others wait', async () => {
    server.use(
      http.get('/api/v1/farm/jobs', () =>
        HttpResponse.json([
          job(11, { printer: 'K1', slicing: { status: 'running', percent: 47, stage: 'Generating G-code', error: null } }),
          job(12, { note: 'A1 uno: no Nero PLA loaded' }),
          job(13, { slicing: { status: 'failed', percent: null, stage: null, error: 'No filament of that colour' } }),
          job(14, { state: 'printing' }),
        ]),
      ),
    );
    render(<WaitingJobs />);

    expect(await screen.findByText('C-0011')).toBeInTheDocument();
    expect(screen.getByText(/47%/)).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '47');
    expect(screen.getByText('A1 uno: no Nero PLA loaded')).toBeInTheDocument();
    expect(screen.getByText(/No filament of that colour/)).toBeInTheDocument();
    expect(screen.queryByText('C-0014')).not.toBeInTheDocument(); // already in the print queue
  });

  it('renders nothing when no job waits', async () => {
    server.use(http.get('/api/v1/farm/jobs', () => HttpResponse.json([job(14, { state: 'printing' })])));
    render(<WaitingJobs />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
  });
});
