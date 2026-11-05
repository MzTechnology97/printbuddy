/**
 * A Telegram bot read by another program too (repeated 409s): the provider card's status and the
 * provider form say so (commands_conflict from the API).
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { server } from '../mocks/server';
import { NotificationProviderCard } from '../../components/NotificationProviderCard';
import { AddNotificationModal } from '../../components/AddNotificationModal';
import type { NotificationProvider } from '../../api/client';

afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});

function telegramProvider(overrides: Partial<NotificationProvider> = {}): NotificationProvider {
  return {
    id: 1,
    name: 'Telegram farm',
    provider_type: 'telegram',
    enabled: true,
    config: { bot_token: 'x', chat_id: '42', commands_enabled: 'true' },
    on_print_start: false,
    on_print_complete: false,
    on_print_failed: false,
    on_print_stopped: false,
    on_print_progress: false,
    on_print_almost_done: false,
    on_print_missing_spool_assignment: false,
    on_printer_offline: false,
    on_printer_error: false,
    on_filament_low: false,
    on_maintenance_due: false,
    on_ams_humidity_high: false,
    on_ams_temperature_high: false,
    on_ams_ht_humidity_high: false,
    on_ams_ht_temperature_high: false,
    on_plate_not_empty: false,
    on_bed_cooled: false,
    on_first_layer_complete: false,
    on_queue_job_added: false,
    on_queue_job_assigned: false,
    on_queue_job_started: false,
    on_queue_job_waiting: false,
    on_queue_job_skipped: false,
    on_queue_job_failed: false,
    on_queue_completed: false,
    on_stock_reorder_alert: false,
    on_stock_break_alert: false,
    on_farm_slicing_failed: false,
    on_farm_file_corrupt: false,
    on_farm_library_problem: false,
    on_farm_plate_to_clear: false,
    on_farm_component_urgent: false,
    on_farm_batch_to_assign: false,
    quiet_hours_enabled: false,
    quiet_hours_start: null,
    quiet_hours_end: null,
    daily_digest_enabled: false,
    daily_digest_time: null,
    printer_id: null,
    last_success: null,
    last_error: null,
    last_error_at: null,
    created_at: '2026-11-04T00:00:00Z',
    updated_at: '2026-11-04T00:00:00Z',
    ...overrides,
  };
}

const full = /also read by another program/i;

describe('Telegram bot shared with another program', () => {
  it('shows it in the provider card status', async () => {
    render(<NotificationProviderCard provider={telegramProvider({ commands_conflict: true })} onEdit={vi.fn()} />);
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Bot read by another program');
    expect(status.getAttribute('title')).toMatch(full);
  });

  it('shows nothing while the bot is read only by Printbuddy', async () => {
    render(<NotificationProviderCard provider={telegramProvider()} onEdit={vi.fn()} />);
    await screen.findByText('Telegram farm');
    expect(screen.queryByText('Bot read by another program')).not.toBeInTheDocument();
  });

  it('warns in the provider form', async () => {
    render(<AddNotificationModal provider={telegramProvider({ commands_conflict: true })} onClose={() => undefined} />);
    await screen.findByDisplayValue('Telegram farm');
    expect(screen.getByRole('alert')).toHaveTextContent(full);
  });
});
