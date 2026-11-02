/**
 * Printfarm (fork): link a library folder to several projects/products. All data is made up.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FarmLinkFolderModal } from '../../farm/LinkFolderModal';
import type { LibraryFolderTree } from '../../api/client';

const folder: LibraryFolderTree = {
  id: 9,
  name: 'Prodotto X',
  parent_id: null,
  project_id: null,
  archive_id: null,
  project_name: null,
  archive_name: null,
  is_external: true,
  external_path: '/nas/Prodotto X',
  external_readonly: true,
  file_count: 3,
  children: [],
};

const longName = 'Set 4 Maniglie Interne per auto elettrica piccola, colore verde lime, con perni rinforzati';

describe('FarmLinkFolderModal', () => {
  let saved: unknown;

  beforeEach(() => {
    saved = undefined;
    server.use(
      http.get('/api/v1/projects/', () =>
        HttpResponse.json([
          { id: 1, name: 'Lavoro una tantum' },
          { id: 2, name: 'Prodotto X nero' },
          { id: 3, name: longName },
        ]),
      ),
      http.get('/api/v1/farm/products', () =>
        HttpResponse.json({
          generated_at: '2026-01-01T00:00:00Z',
          products: [2, 3].map((id) => ({ project_id: id, name: '', skus: [], asins: [], updated_at: '', parts: [] })),
        }),
      ),
      http.get('/api/v1/farm/folders/9/projects', () => HttpResponse.json({ folder_id: 9, project_ids: [2] })),
      http.put('/api/v1/farm/folders/9/projects', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ folder_id: 9, project_ids: [2, 3] });
      }),
    );
  });

  it('searches, shows full names and saves several projects', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<FarmLinkFolderModal folder={folder} onClose={onClose} onLink={vi.fn()} isLoading={false} />);

    // Current link is pre-selected; long names are not truncated.
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /Prodotto X nero/ })).toBeChecked());
    expect(screen.getByText(longName)).not.toHaveClass('truncate');

    await user.type(screen.getByRole('searchbox'), 'maniglie verde');
    expect(screen.queryByRole('checkbox', { name: /Lavoro una tantum/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: new RegExp(longName) }));

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(saved).toEqual({ project_ids: [2, 3] }));
    expect(onClose).toHaveBeenCalled();
  });
});
