/**
 * Fork (printfarm): NAS settings card. The password is write-only.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { NasSettingsCard } from '../../farm/NasSettingsCard';

const STORED = {
  host: 'nas.test',
  share: 'share',
  path: 'Prints',
  username: 'farm',
  password_set: true,
  scan_interval_seconds: 300,
  folder_id: 7,
  last_scan_at: null,
  last_result: null,
  cache_mb: 2048,
  cache_used_bytes: 300 * 1024 * 1024,
};

describe('NasSettingsCard', () => {
  beforeEach(() => {
    server.use(http.get('/api/v1/farm/nas', () => HttpResponse.json(STORED)));
  });

  it('keeps the stored password when the field is left empty', async () => {
    let saved: Record<string, unknown> | null = null;
    server.use(
      http.put('/api/v1/farm/nas', async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(STORED);
      }),
    );
    const user = userEvent.setup();
    render(<NasSettingsCard />);

    const password = await screen.findByPlaceholderText(/set: leave empty/i);
    expect(password).toHaveValue('');
    await waitFor(() => expect(screen.getByDisplayValue('Prints')).toBeInTheDocument());
    expect(screen.getByText('Cache: 300 MB used of 2048 MB')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).toEqual({
      host: 'nas.test',
      share: 'share',
      path: 'Prints',
      username: 'farm',
      scan_interval_seconds: 300,
      cache_mb: 2048,
    });
  });

  it('browses directories and picks the subfolder', async () => {
    server.use(
      http.post('/api/v1/farm/nas/browse', async ({ request }) => {
        const body = (await request.json()) as { path: string };
        return HttpResponse.json(
          body.path === '' ? { path: '', directories: ['Archive', 'Prints'] } : { path: body.path, directories: [] },
        );
      }),
    );
    const user = userEvent.setup();
    render(<NasSettingsCard />);
    await waitFor(() => expect(screen.getByDisplayValue('Prints')).toBeInTheDocument());

    await user.clear(screen.getByDisplayValue('Prints'));
    await user.click(screen.getByRole('button', { name: /browse/i }));
    await user.click(await screen.findByRole('button', { name: 'Archive' }));
    await user.click(await screen.findByRole('button', { name: /use this folder/i }));

    expect(screen.getByDisplayValue('Archive')).toBeInTheDocument();
  });
});
