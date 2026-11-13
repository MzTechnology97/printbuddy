/**
 * Printfarm (fork): 3MF Workspace, batch E5b. OrcaSlicer's "Arrange options" and the request the backend takes
 * (Orca's own Arrange runs in the slicer sidecar). Made-up data.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ArrangeOptionsDialog } from '../../farm/workspace/ArrangeOptions';
import { arrangeDefaults, arrangeRequest, loadArrangeSettings, saveArrangeSettings } from '../../farm/workspace/arrange';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';

const LAYOUT: OrcaLayout = { tag: 'test', source: '', pages: { process: [], filament: [], printer: [] }, options: {}, it: {}, object_keys: [], plate_keys: {} };

describe('the arrange options', () => {
  // a browser's storage of its own (the test runner's may be off)
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
      clear: () => store.clear(),
    });
  });

  it("Orca's defaults (Align to Y axis on an i3), kept per browser, sent as the backend takes them", () => {
    expect(arrangeDefaults(false)).toEqual({ rotation: false, multiMaterial: true, avoidCalibration: false, alignY: false });
    expect(arrangeDefaults(true).alignY).toBe(true);
    saveArrangeSettings({ ...arrangeDefaults(false), rotation: true });
    expect(loadArrangeSettings(false).rotation).toBe(true);
    // rotating turns "Align to Y axis" off, as Orca's dialog does
    expect(arrangeRequest({ rotation: true, multiMaterial: false, avoidCalibration: false, alignY: true }, 2)).toEqual({
      plate: 2,
      allow_rotations: true,
      align_to_y_axis: false,
      allow_multi_material: false,
      avoid_calibration_region: false,
    });
  });

  it('shows Spacing at auto, the calibration option only with a lidar, and arranges', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onArrange = vi.fn();
    const { rerender } = render(
      <ArrangeOptionsDialog layout={LAYOUT} value={arrangeDefaults(false)} lidar={false} i3={false} onChange={onChange} onArrange={onArrange} onClose={() => {}} />,
    );
    expect(screen.getByRole('textbox', { name: 'Spacing' })).toBeDisabled();
    expect(screen.queryByLabelText('Avoid extrusion calibration region')).toBeNull();
    await user.click(screen.getByLabelText('Auto rotate for arrangement'));
    expect(onChange).toHaveBeenCalledWith({ ...arrangeDefaults(false), rotation: true });
    rerender(
      <ArrangeOptionsDialog layout={LAYOUT} value={{ ...arrangeDefaults(false), rotation: true }} lidar i3={false} onChange={onChange} onArrange={onArrange} onClose={() => {}} />,
    );
    expect(screen.getByLabelText('Align to Y axis')).toBeDisabled();
    expect(screen.getByLabelText('Avoid extrusion calibration region')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Arrange' }));
    expect(onArrange).toHaveBeenCalled();
  });
});
