/**
 * Fork: the queue history says why an item failed or was stopped, where, and what was reported.
 */

import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { CompactHistoryRow } from '../../components/CompactHistoryRow';
import type { PrintQueueItem } from '../../api/client';
import en from '../../i18n/locales/en';

// The English strings, with {{placeholders}} filled: what a user reads.
const t = (key: string, options?: Record<string, unknown>) => {
  const text = key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
  return String(text ?? key).replace(/\{\{(\w+)\}\}/g, (_, name) => String(options?.[name] ?? ''));
};

const item = (over: Partial<PrintQueueItem>): PrintQueueItem =>
  ({
    id: 1,
    status: 'failed',
    library_file_name: 'part.gcode',
    printer_name: 'K2',
    created_at: '2026-11-05T10:00:00Z',
    completed_at: '2026-11-05T10:33:00Z',
    error_message: null,
    failure_detail: null,
    dispatch_attempts: 0,
    ...over,
  }) as PrintQueueItem;

const row = (it: PrintQueueItem) =>
  render(
    <CompactHistoryRow
      item={it}
      onRequeue={vi.fn()}
      onRemove={vi.fn()}
      hasPermission={() => true}
      canModify={() => true}
      t={t}
    />,
  );

describe('CompactHistoryRow failure reason', () => {
  it('shows the reason, the phase with progress and layer, and the technical details on request', () => {
    row(
      item({
        error_message: 'Printer error: Heater extruder not heating',
        failure_detail: {
          phase: 'printing',
          progress: 25,
          layer: 30,
          total_layers: 120,
          technical: ['print_stats.state=error', 'webhooks.state=ready'],
        },
      }),
    );
    expect(screen.getByText('Printer error: Heater extruder not heating')).toBeInTheDocument();
    expect(screen.getByText('While printing · at 25% · layer 30 of 120')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Technical details'));
    expect(screen.getByText(/print_stats.state=error/)).toBeInTheDocument();
  });

  it('says the printer gave no reason instead of showing nothing', () => {
    row(item({ error_message: 'Reason not reported by the printer', failure_detail: { phase: 'printing', code: 'unknown' } }));
    expect(screen.getByText('Reason not reported by the printer')).toBeInTheDocument();
    expect(screen.queryByText('Technical details')).not.toBeInTheDocument();
  });

  it('shows an upload failure with its start attempts', () => {
    row(
      item({
        error_message: 'Upload to the printer failed: 552 Storage quota exceeded',
        failure_detail: { phase: 'upload', technical: ['ERROR: FTP 552'] },
        dispatch_attempts: 2,
      }),
    );
    expect(screen.getByText('Upload to the printer · start attempts: 2')).toBeInTheDocument();
  });

  it('says who cancelled an item and from where', () => {
    row(item({ status: 'cancelled', error_message: 'Stopped by user', failure_detail: { phase: 'printing', by: 'mario', via: 'panel' } }));
    expect(screen.getByText('Stopped by user')).toBeInTheDocument();
    expect(screen.getByText('While printing · by mario · from the panel')).toBeInTheDocument();
  });

  it('adds nothing to a completed item', () => {
    row(item({ status: 'completed' }));
    expect(screen.queryByTestId('history-failure')).not.toBeInTheDocument();
  });
});
