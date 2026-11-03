/**
 * Printfarm (fork): the slot of the component's colour, by the farm's tolerance. All data is made up.
 */

import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import type { LoadedFilament } from '../../hooks/useFilamentMapping';
import { PrinterSlots } from '../../farm/PrinterSlots';
import { colourDistance, componentSlot } from '../../farm/slotColour';

const slot = (globalTrayId: number, type: string, color: string) =>
  ({ globalTrayId, type, color, label: `A${globalTrayId + 1}` }) as LoadedFilament;

describe('componentSlot', () => {
  it('reads colours in any form and matches the material by type', () => {
    expect(colourDistance('#F75403', 'F75403FF')).toBe(0);
    const loaded = [slot(0, 'PLA', '#F75403'), slot(3, 'PETG', 'F75403FF')];
    expect(componentSlot('PETG', loaded, 'f75403', 80)?.globalTrayId).toBe(3); // the PLA one is the wrong type
    // The farm's "Arancio" (f28c28) is the same orange within its tolerance, not within upstream's 40 per channel.
    expect(componentSlot('PETG', loaded, 'f28c28', 80)?.globalTrayId).toBe(3);
    expect(componentSlot('PETG', loaded, 'f5d000', 80)).toBeNull(); // yellow: not loaded
  });
});

describe('PrinterSlots', () => {
  it('shows no colour warning when the slot is the component colour by the farm tolerance', async () => {
    server.use(
      http.get('/api/v1/farm/plates/:id/filaments', () =>
        HttpResponse.json({ plate_id: 2, sliced: true,
          filaments: [{ slot_id: 1, type: 'PETG', color: '#F75403', used_grams: 30, used_meters: 10 }] }),
      ),
      http.get('/api/v1/farm/plan/settings', () => HttpResponse.json({ url: '', tolerance: 80, colors: {} })),
      http.get('/api/v1/farm/printer-spools', () => HttpResponse.json([])),
      http.get('/api/v1/spool-assignments', () => HttpResponse.json([])),
      http.get('/api/v1/printers/9/status', () =>
        HttpResponse.json({ connected: true, state: 'IDLE', vt_tray: [], ams: [{ id: 0, humidity: null, temp: null,
          tray: [
            { id: 0, tray_type: 'PETG', tray_color: 'FFFFFFFF' },
            { id: 3, tray_type: 'PETG', tray_color: 'F75403FF', tray_sub_brands: 'PETG HF' },
          ] }] }),
      ),
    );
    render(<PrinterSlots printerId={9} plateId={2} plateLabel="K2 · Plate 2" color="Arancio" colorHex="f28c28"
      mappings={{}} onMappings={() => undefined} onAmsMapping={() => undefined} />);
    expect(await screen.findByText('(Ready)')).toBeInTheDocument();
    expect(screen.queryByText('(Color mismatch)')).not.toBeInTheDocument();
    expect(screen.getByTitle('Auto-matched')).toHaveValue('3');
  });
});
