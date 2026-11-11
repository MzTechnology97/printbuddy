/**
 * Printfarm (fork): the queue page's "Jobs" board. All data is made up; no printer is contacted.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { BoardSummary, JobsBoard } from '../../farm/JobsBoard';
import type { FarmBoardRow } from '../../farm/api';

const copied: string[] = [];
vi.mock('../../utils/clipboard', () => ({
  copyTextToClipboard: async (text: string) => {
    copied.push(text);
    return true;
  },
}));

const row = (over: Partial<FarmBoardRow>): FarmBoardRow => ({
  key: 'job:1', job_id: 1, queue_item_id: null, archive_id: null, title: 'C-0085 Maniglia', component_code: 'C-0085',
  colour: 'Nero', pieces: 8, printer_id: null, printer: null, group: 'waiting', phase: 'waiting', level: 'info',
  status_text: null, failure_phase: null, slicer: null, file: null, library_file_id: null, manual_start: false, source: 'farm',
  created_at: '2026-01-01T10:00:00', completed_at: null, actions: [], ...over,
});

const sliceFailed = row({
  key: 'job:1', group: 'problems', phase: 'slice_failed', level: 'error',
  status_text: 'plate 2 of file 1: slicing failed (empty plate)', actions: ['retry_slice', 'reslice', 'cancel'],
});
const printing = row({
  key: 'job:2', job_id: 2, queue_item_id: 5, group: 'printing', phase: 'printing', printer_id: 1, printer: 'A1 uno',
  slicer: 'orcaslicer', file: 'Maniglia.gcode.3mf', library_file_id: 40,
});
const failed = row({
  key: 'job:3', job_id: 3, queue_item_id: 6, group: 'problems', phase: 'failed', level: 'error', printer: 'A1 uno',
  status_text: 'FTP 550', failure_phase: 'upload', actions: ['reslice', 'requeue'],
});
const slicing = row({
  key: 'job:9', job_id: 9, group: 'waiting', phase: 'slicing', status_text: 'plate 1 being sliced',
  slice_progress: { percent: 37, stage: 'Generating G-code' },
});
const manual = row({
  key: 'item:7', job_id: null, queue_item_id: 7, title: 'Regalo.gcode.3mf', group: 'waiting', phase: 'queued',
  printer_id: 1, printer: 'A1 uno', source: 'queue', manual_start: true, actions: ['start', 'cancel_item'],
});
const done = row({ key: 'item:8', job_id: null, queue_item_id: 8, title: 'Vecchio.3mf', group: 'history', phase: 'finished' });

const detail = {
  events: [
    { at: '2026-01-01T10:00:00', phase: 'created', level: 'info', message: 'C-0085: 8 pieces', printer_id: null, detail: null },
    { at: '2026-01-01T10:00:05', phase: 'slice_requested', level: 'info', message: 'Slice asked', printer_id: null, detail: null },
    { at: '2026-01-01T10:01:00', phase: 'slice_failed', level: 'error', message: 'OrcaSlicer failed on plate 2',
      printer_id: null, detail: { slicer: 'orcaslicer', error: 'Plate 2 has no objects' } },
  ],
  technical: { job: { id: 1, component: 'C-0085' } },
  log: ['10:01 ERROR Farm job 1: access_code=***'],
};

describe('JobsBoard', () => {
  let actions: string[];

  beforeEach(() => {
    actions = [];
    copied.length = 0;
    server.use(
      http.get('/api/v1/farm/board', () =>
        HttpResponse.json({ problems: [sliceFailed, failed], printing: [printing], waiting: [manual, slicing],
          history: [done], history_total: 1 }),
      ),
      http.get('/api/v1/farm/board/:key', ({ params }) => {
        const all = [sliceFailed, printing, failed, manual, done];
        return HttpResponse.json({ row: all.find((r) => r.key === params.key), ...detail });
      }),
      http.post('/api/v1/farm/board/:key/:action', ({ params }) => {
        actions.push(`${params.key} ${params.action}`);
        return HttpResponse.json({ asked: 1 });
      }),
      http.delete('/api/v1/farm/jobs/:id', ({ params }) => {
        actions.push(`cancel job ${params.id}`);
        return HttpResponse.json({ id: Number(params.id), state: 'cancelled' });
      }),
      http.post('/api/v1/queue/:id/start', ({ params }) => {
        actions.push(`start ${params.id}`);
        return HttpResponse.json({ id: Number(params.id) });
      }),
      http.get('/api/v1/farm/jobs', () => HttpResponse.json([])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({ progress: 42, layer_num: 10, total_layers: 80 })),
    );
  });

  it('puts one row per print in its group, with the slicer that made the file', async () => {
    render(<JobsBoard />);
    const problems = await screen.findByRole('region', { name: 'To fix' });
    expect(within(problems).getByText('plate 2 of file 1: slicing failed (empty plate)')).toBeInTheDocument();
    expect(within(problems).getByText('FTP 550')).toBeInTheDocument();
    const printingGroup = screen.getByRole('region', { name: 'Printing' });
    expect(within(printingGroup).getByAltText('OrcaSlicer')).toHaveAttribute('src', expect.stringContaining('orcaslicer.png'));
    expect(await within(printingGroup).findByText(/42% · layer 10\/80/)).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Waiting' })).toHaveTextContent('Regalo.gcode.3mf');
    // A plate being sliced: the slicer's progress.
    expect(screen.getByText('Slicing: 37%')).toBeInTheDocument();
    // The history stays closed until asked.
    expect(screen.queryByText('Vecchio.3mf')).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: /History/ }));
    expect(screen.getByText('Vecchio.3mf')).toBeInTheDocument();
  });

  it('shows where a print stopped, its raw details, and copies everything for support', async () => {
    const user = userEvent.setup();
    render(<JobsBoard />);
    await user.click(await screen.findByText('plate 2 of file 1: slicing failed (empty plate)'));
    const steps = await screen.findByRole('list', { name: 'Timeline' });
    const step = (name: string) => within(steps).getByText(name).closest('li');
    expect(step('Requested')).toHaveAttribute('data-state', 'done');
    expect(step('Sliced')).toHaveAttribute('data-state', 'failed');
    expect(step('Started')).toHaveAttribute('data-state', 'todo');
    await user.click(screen.getByText(/OrcaSlicer failed on plate 2/));
    expect(screen.getByText(/Plate 2 has no objects/)).toBeInTheDocument();
    expect(screen.getByText(/access_code=\*\*\*/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Copy everything' }));
    await waitFor(() => expect(copied).toHaveLength(1));
    expect(copied[0]).toContain('C-0085 Maniglia [job:1]');
    expect(copied[0]).toContain('ERROR slice_failed: OrcaSlicer failed on plate 2');
    expect(copied[0]).toContain('"component": "C-0085"');
    expect(copied[0]).toContain('access_code=***');
  });

  it('places a print from before the event log by its state: failed while sending', async () => {
    server.use(http.get('/api/v1/farm/board/job:3', () => HttpResponse.json({ row: failed, events: [], technical: {}, log: [] })));
    const user = userEvent.setup();
    render(<JobsBoard />);
    await user.click(await screen.findByText('FTP 550'));
    const steps = await screen.findByRole('list', { name: 'Timeline' });
    const step = (name: string) => within(steps).getByText(name).closest('li');
    expect(step('In a printer queue')).toHaveAttribute('data-state', 'done');
    expect(step('Sent to the printer')).toHaveAttribute('data-state', 'failed');
    expect(step('Started')).toHaveAttribute('data-state', 'todo');
  });

  it('retries or forces the slicing, queues a failed print again and cancels a job', async () => {
    const user = userEvent.setup();
    render(<JobsBoard />);
    await user.click(await screen.findByText('plate 2 of file 1: slicing failed (empty plate)'));
    await user.click(await screen.findByRole('button', { name: 'Retry slicing' }));
    await waitFor(() => expect(actions).toContain('job:1 retry-slice'));
    await user.click(screen.getByRole('button', { name: 'Force a new slice' }));
    await waitFor(() => expect(actions).toContain('job:1 reslice'));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(actions).toContain('cancel job 1'));
    await user.click(screen.getByText('FTP 550'));
    await user.click(await screen.findByRole('button', { name: 'Queue again' }));
    await waitFor(() => expect(actions).toContain('job:3 requeue'));
  });

  it('starts a print by hand only after the plate is confirmed clear', async () => {
    const user = userEvent.setup();
    render(<JobsBoard />);
    await user.click(await screen.findByText('Regalo.gcode.3mf'));
    await user.click(await screen.findByRole('button', { name: 'Start' }));
    expect(screen.getByText('Check that the plate of A1 uno is clear before starting.')).toBeInTheDocument();
    expect(actions).toEqual([]);
    const buttons = screen.getAllByRole('button', { name: 'Start' });
    await user.click(buttons[buttons.length - 1]); // the dialog's
    await waitFor(() => expect(actions).toEqual(['start 7']));
  });

  it('shows the filament deficit of a refused start and can start anyway', async () => {
    server.use(
      http.post('/api/v1/queue/:id/start', ({ params, request }) => {
        const skip = new URL(request.url).searchParams.get('skip_filament_check') === 'true';
        actions.push(`start ${params.id}${skip ? ' anyway' : ''}`);
        if (skip) return HttpResponse.json({ id: Number(params.id) });
        return HttpResponse.json({ detail: { code: 'insufficient_filament',
          deficit: [{ slot_id: 3, required_grams: 222.4, remaining_grams: 99.7 }] } }, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    render(<JobsBoard />);
    await user.click(await screen.findByText('Regalo.gcode.3mf'));
    await user.click(await screen.findByRole('button', { name: 'Start' }));
    const buttons = screen.getAllByRole('button', { name: 'Start' });
    await user.click(buttons[buttons.length - 1]);
    expect(await screen.findByText(/Slot 3: needs 222 g, 100 g remaining/)).toBeInTheDocument();
    expect(screen.queryByText('HTTP 409')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Print Anyway' }));
    await waitFor(() => expect(actions).toEqual(['start 7', 'start 7 anyway']));
  });
});

describe('JobsBoard: prints outside the queue', () => {
  let jobs: object[];

  beforeEach(() => {
    server.use(
      http.get('/api/v1/farm/jobs', () => HttpResponse.json(jobs)),
      http.get('/api/v1/farm/board', () =>
        HttpResponse.json({ problems: [], printing: [], waiting: [], history: [], history_total: 0 }),
      ),
    );
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
      { ...outsideJob, id: 12, print_name: 'Sopra__abcdef12.3mf',
        proposal: [
          { component_code: 'C-0121', name: 'Sopra', pieces: 15, color: 'Nero', plate_id: 1, alternative: true },
          { component_code: 'C-0122', name: 'Sotto', pieces: 15, color: 'Nero', plate_id: 1, alternative: true },
        ] },
    ];
    const user = userEvent.setup();
    render(<JobsBoard />);
    expect(await screen.findByText('3 prints outside the queue to assign')).toBeInTheDocument();
    // Alternatives: the product is chosen, but no pieces are filled in.
    expect(
      screen.getByText('It is one of these, not all: C-0121 Sopra ×15 / C-0122 Sotto ×15. Say which, with its pieces.'),
    ).toBeInTheDocument();
    expect(screen.getByText('From its name it looks like: C-0121 Sopra ×15')).toBeInTheDocument();
    // The proposed product is chosen, with the proposed pieces; the user adds the bottoms.
    await waitFor(() => expect(screen.getAllByLabelText('Pieces C-0121')).toHaveLength(2));
    expect(screen.getAllByLabelText('Pieces C-0121')[0]).toHaveValue(15);
    expect(screen.getAllByLabelText('Pieces C-0121')[1]).toHaveValue(null);
    await user.type(screen.getAllByLabelText('Pieces C-0122')[0], '14');
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
});

describe('BoardSummary', () => {
  it('counts the jobs under way and those to fix, with a link to the queue', async () => {
    server.use(
      http.get('/api/v1/farm/board', () =>
        HttpResponse.json({ problems: [failed], printing: [printing], waiting: [manual], history: [], history_total: 0 }),
      ),
    );
    render(<BoardSummary />);
    expect(await screen.findByText('2 jobs under way')).toBeInTheDocument();
    expect(screen.getByText('· 1 to fix')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Follow them in the Queue →' })).toHaveAttribute('href', '/queue?view=jobs');
  });

  it('says nothing when nothing is under way', async () => {
    let asked = false;
    server.use(
      http.get('/api/v1/farm/board', () => {
        asked = true;
        return HttpResponse.json({ problems: [], printing: [], waiting: [], history: [], history_total: 3 });
      }),
    );
    render(<BoardSummary />);
    await waitFor(() => expect(asked).toBe(true));
    expect(screen.queryByText(/under way|Queue/)).not.toBeInTheDocument();
  });
});
