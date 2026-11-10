/**
 * Printfarm (fork): what a farm slice changed in the project. All data is made up.
 */

import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SliceFidelityNote } from '../../farm/JobsBoard';
import type { FarmSliceFidelity } from '../../farm/api';

const drift: FarmSliceFidelity = {
  project: 'OrcaSlicer 2.4.2',
  slicer: 'OrcaSlicer 2.5.0',
  version_drift: true,
  changed: [['machine_start_gcode', null, null], ['support_ironing_spacing', '0.1', '0.15']],
  changed_count: 3,
  highlight: ['machine_start_gcode'],
};

describe('SliceFidelityNote', () => {
  it('warns of the version drift and lists the changed settings on demand', async () => {
    render(<SliceFidelityNote fidelity={drift} />);
    expect(screen.getByText(/Project saved with OrcaSlicer 2.4.2, sliced with OrcaSlicer 2.5.0/)).toBeInTheDocument();
    expect(screen.getByText(/The slicer changed 3 settings/)).toBeInTheDocument();
    expect(screen.getByText(/check machine_start_gcode/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/The slicer changed 3 settings/));
    expect(screen.getByText('support_ironing_spacing: 0.1 → 0.15')).toBeVisible();
    expect(screen.getByText('…and 1 more')).toBeVisible();
  });

  it('says nothing when the slice kept the project as it was', () => {
    const same: FarmSliceFidelity = { ...drift, version_drift: false, changed: [], changed_count: 0, highlight: [] };
    render(<SliceFidelityNote fidelity={same} />);
    expect(screen.queryByText(/Project saved with|The slicer changed/)).not.toBeInTheDocument();
  });
});
