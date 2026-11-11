/**
 * Printfarm (fork): the slot grid of the Accoda dialog, on a dual-nozzle X2D, a K2 with CFS and the Klipper printers
 * with only an external spool (K1, SV08, Sovol Zero). Made-up data.
 */

import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SlotGrid } from '../../farm/SlotGrid';
import { PrinterSlots } from '../../farm/PrinterSlots';

const tray = (id: number, type: string, color: string, sub = '') => ({
  id, tray_type: type, tray_color: color, tray_sub_brands: sub, remain: 50, tray_info_idx: '',
});

function status(body: object) {
  server.use(
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(body)),
    http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
  );
}

describe('SlotGrid', () => {
  it('shows each unit and lets a filament pick only the slots of its nozzle', async () => {
    status({
      connected: true,
      ams_extruder_map: { '0': 1, '128': 0 }, // AMS on the left nozzle, AMS HT on the right
      ams: [
        { id: 0, is_ams_ht: false, module_type: 'n3f', tray: [tray(0, '', ''), tray(1, 'PETG', '000000FF', 'PETG HF'),
          tray(2, 'PETG', '161616FF'), tray(3, 'PETG', '161616FF')] },
        { id: 128, is_ams_ht: true, module_type: 'n3s', tray: [tray(0, 'PLA', '9B9EA0FF', 'PLA Matte')] },
      ],
      vt_tray: [tray(254, 'PET-CF', '111111FF'), tray(255, '', '')],
    });
    const onMappings = vi.fn();
    render(
      <SlotGrid printerId={1} mappings={{}} onMappings={onMappings} filamentReqs={{ filaments: [
        { slot_id: 1, type: 'PETG', color: '#000000', used_grams: 402, used_meters: 0, nozzle_id: 1 },
        { slot_id: 2, type: 'PLA', color: '#C0C0C0', used_grams: 197, used_meters: 0, nozzle_id: 0 },
      ] }} />,
    );
    await waitFor(() => expect(screen.getAllByText('AMS HT-A')).toHaveLength(2));
    expect(screen.getAllByText('AMS A')).toHaveLength(2);

    const a3 = screen.getAllByTitle(/^A3 · /);
    expect(a3[0]).toBeEnabled(); // PETG, left nozzle
    expect(a3[1]).toBeDisabled(); // PLA, right nozzle: the AMS feeds the left one
    expect(screen.getAllByTitle(/^HT-A · PLA Matte/)[1]).toBeEnabled();
    expect(screen.getAllByTitle(/^A1 · /)[0]).toBeDisabled(); // empty

    await userEvent.click(a3[0]);
    expect(onMappings).toHaveBeenCalledWith({ 1: 2 });
  });

  it('lets a single external spool on a dual-nozzle printer feed the left nozzle, as upstream maps it', async () => {
    status({
      connected: true,
      ams_extruder_map: { '0': 0 },
      ams: [{ id: 0, is_ams_ht: false, module_type: 'n3f', tray: [tray(0, 'PLA', 'FFFFFFFF'), tray(1, '', ''),
        tray(2, '', ''), tray(3, '', '')] }],
      vt_tray: [tray(254, 'PETG', '000000FF')],
    });
    render(
      <SlotGrid printerId={3} mappings={{}} onMappings={vi.fn()} filamentReqs={{ filaments: [
        { slot_id: 1, type: 'PETG', color: '#000000', used_grams: 10, used_meters: 0, nozzle_id: 1 },
      ] }} />,
    );
    const ext = await screen.findByTitle(/^Ext · /);
    expect(ext).toBeEnabled();
    expect(ext).toHaveAttribute('aria-pressed', 'true'); // what upstream proposes, and sends
    expect(screen.getByTitle(/^A1 · /)).toBeDisabled(); // the AMS feeds the right nozzle
  });

  it('names the K2 CFS units and keeps every slot reachable on one nozzle', async () => {
    status({
      connected: true,
      ams: [{ id: 0, is_ams_ht: false, module_type: 'cfs', tray: [tray(0, 'PETG', '000000FF', 'Hyper PETG'),
        tray(1, 'PLA', 'FFFFFFFF'), tray(2, '', ''), tray(3, '', '')] }],
      vt_tray: [],
    });
    render(
      <SlotGrid printerId={2} mappings={{ 1: 1 }} onMappings={vi.fn()} filamentReqs={{ filaments: [
        { slot_id: 1, type: 'PETG', color: '#000000', used_grams: 0, used_meters: 0 },
      ] }} />,
    );
    await waitFor(() => expect(screen.getByText('CFS T1')).toBeInTheDocument());
    expect(screen.getByTitle(/^A2 · /)).toBeEnabled(); // chosen by hand even if it is PLA
    expect(screen.getByTitle(/^A2 · /)).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows a Klipper printer without units its external spool alone, and lets the filament take it', async () => {
    status({ connected: true, ams: [], vt_tray: [tray(254, 'PETG', '000000FF', 'PETG HF')] }); // K1, SV08, Sovol Zero
    const onMappings = vi.fn();
    render(
      <SlotGrid printerId={4} mappings={{}} onMappings={onMappings} filamentReqs={{ filaments: [
        { slot_id: 1, type: 'PETG', color: '#000000', used_grams: 0, used_meters: 0 },
      ] }} />,
    );
    await waitFor(() => expect(screen.getByText('External spool')).toBeInTheDocument());
    expect(screen.queryByText(/^AMS|^CFS/)).not.toBeInTheDocument();
    const ext = screen.getByTitle(/^Ext · /);
    expect(ext).toBeEnabled();
    await userEvent.click(ext);
    expect(onMappings).toHaveBeenCalledWith({ 1: 254 });
  });

  it('shows a filament with no slot to take when a Klipper printer reports nothing, and blocks nothing', async () => {
    status({ connected: true, ams: [], vt_tray: [] });
    render(
      <SlotGrid printerId={5} mappings={{}} onMappings={vi.fn()} filamentReqs={{ filaments: [
        { slot_id: 1, type: 'PETG', color: '#000000', used_grams: 0, used_meters: 0 },
      ] }} />,
    );
    await waitFor(() => expect(screen.getByText('no slot')).toBeInTheDocument());
    expect(screen.queryAllByRole('button', { pressed: true })).toHaveLength(0);
  });

  it('sends no slot mapping before one is chosen by hand, on a K1 too', async () => {
    status({ connected: true, ams: [], vt_tray: [tray(254, 'PETG', '000000FF')] });
    server.use(
      http.get('/api/v1/farm/plates/:id/filaments', () =>
        HttpResponse.json({ plate_id: 7, sliced: false, filaments: [{ slot_id: 1, type: 'PETG', color: '#000000', used_grams: 0 }] }),
      ),
      http.get('/api/v1/settings/', () => HttpResponse.json({})),
      http.get('/api/v1/farm/plan/settings', () => HttpResponse.json({ tolerance: 80 })),
      http.get('/api/v1/farm/printer-spools', () => HttpResponse.json([])),
    );
    const onAmsMapping = vi.fn();
    render(
      <PrinterSlots printerId={4} plateId={7} plateLabel="test.3mf · plate 1" color="Nero" colorHex="000000"
        mappings={{}} onMappings={vi.fn()} onAmsMapping={onAmsMapping} />,
    );
    await waitFor(() => expect(screen.getByTitle(/^Ext · /)).toBeInTheDocument());
    expect(screen.queryByText(/the slots can be chosen once it is/)).not.toBeInTheDocument();
    expect(screen.getByText(/the filaments come from the 3MF/)).toBeInTheDocument();
    expect(onAmsMapping.mock.calls.every(([m]) => m === undefined)).toBe(true); // the dispatcher maps it: nothing held
  });
});
