/**
 * Printfarm (fork): 3MF Workspace. The draft of unsaved changes (checks, back to the original) and the
 * settings panel (edit, see original and changed values, restore, search, read-only settings).
 */
import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import type { WorkspaceSetting } from '../../farm/workspace/api';
import { elementError, valueErrors, withValue, withoutKey, type Draft } from '../../farm/workspace/draft';
import { SettingsPanel } from '../../farm/workspace/SettingsPanel';
import { GizmoToolbar } from '../../farm/workspace/Viewport';
import type { OrcaLayout, Section } from '../../farm/workspace/orcaLayout';

const base = {
  description: null,
  group: 'quality',
  unit: null,
  min: null,
  max: null,
  step: null,
  options: null,
  editable: true,
  readonly_reason: null,
  differs_from_system: false,
  overrides: [],
} as const;

const LAYER: WorkspaceSetting = {
  ...base,
  key: 'layer_height',
  label: 'Layer Height',
  section: 'process',
  tab: 'quality',
  type: 'float',
  unit: 'mm',
  min: 0.04,
  max: 0.6,
  value: '0.2',
};
const WALLS: WorkspaceSetting = {
  ...base,
  key: 'wall_loops',
  label: 'Wall Loops',
  section: 'process',
  group: 'strength',
  tab: 'strength',
  type: 'int',
  min: 1,
  max: 10,
  value: '3',
  differs_from_system: true,
  overrides: [{ object_id: '4', object: 'Cube B', part: null, value: '5' }],
};
const DENSITY: WorkspaceSetting = {
  ...base,
  key: 'sparse_infill_density',
  label: 'Infill Density',
  section: 'process',
  group: 'infill',
  tab: 'strength',
  type: 'float',
  unit: '%',
  value: '15%',
};
const SUPPORT: WorkspaceSetting = {
  ...base,
  key: 'enable_support',
  label: 'Enable Support',
  section: 'process',
  group: 'support',
  tab: 'support',
  type: 'bool',
  value: '0',
};
const TYPE: WorkspaceSetting = {
  ...base,
  key: 'support_type',
  label: 'Support Type',
  section: 'process',
  group: 'support',
  tab: 'support',
  type: 'enum',
  options: [
    { value: 'normal(auto)', label: 'Normal (Auto)' },
    { value: 'tree(auto)', label: 'Tree (Auto)' },
  ],
  value: 'normal(auto)',
};
const TEMP: WorkspaceSetting = {
  ...base,
  key: 'nozzle_temperature',
  label: 'Nozzle Temperature',
  section: 'filament',
  group: 'temperature',
  tab: 'filament',
  type: 'float',
  unit: '°C',
  min: 0,
  max: 500,
  value: ['220', '240'],
};
const PRINTER_ID: WorkspaceSetting = {
  ...base,
  key: 'printer_settings_id',
  label: 'printer_settings_id',
  section: null,
  group: null,
  tab: null,
  type: 'string',
  editable: false,
  readonly_reason: 'unknown',
  differs_from_system: null,
  value: 'Test Printer',
};
const GCODE: WorkspaceSetting = {
  ...base,
  key: 'machine_start_gcode',
  label: 'Start G-code',
  section: 'printer',
  group: 'gcode',
  tab: 'printer',
  type: 'string',
  editable: false,
  readonly_reason: 'text',
  value: 'G28',
};
const SETTINGS = [LAYER, WALLS, DENSITY, SUPPORT, TYPE, TEMP, PRINTER_ID, GCODE];

describe('workspace draft', () => {
  it('checks numbers, whole numbers, ranges and percentages like the backend', () => {
    expect(elementError(LAYER, '0.16', '0.2')).toBeNull();
    expect(elementError(LAYER, 'abc', '0.2')).toBe('number');
    expect(elementError(LAYER, '0.9', '0.2')).toBe('max');
    expect(elementError(WALLS, '2.5', '3')).toBe('integer');
    expect(elementError(WALLS, '0', '3')).toBe('min');
    expect(elementError(DENSITY, '25%', '15%')).toBeNull();
    expect(elementError(LAYER, '20%', '0.2')).toBe('percent');
    expect(elementError(TYPE, 'rocket', 'normal(auto)')).toBe('option');
    expect(elementError(LAYER, 'nil', '0.2')).toBe('inherit');
    expect(valueErrors(TEMP, ['225', 'x'])).toEqual([null, 'number']);
  });

  it('a value set back to the original is no change any more', () => {
    let draft: Draft = withValue({}, LAYER, '0.12');
    expect(draft).toEqual({ layer_height: '0.12' });
    draft = withValue(draft, TEMP, ['225', '240']);
    draft = withValue(draft, LAYER, '0.2');
    expect(draft).toEqual({ nozzle_temperature: ['225', '240'] });
    expect(withoutKey(draft, 'nozzle_temperature')).toEqual({});
  });
});

// A small OrcaSlicer layout: the real one (orca-assets/settings-layout.json) is generated and large.
const LAYOUT: OrcaLayout = {
  tag: 'test',
  source: '',
  pages: {
    process: [
      { title: 'Quality', icon: 'custom-gcode_quality', groups: [{ title: 'Layer height', icon: null, keys: ['layer_height'] }] },
      {
        title: 'Strength',
        icon: 'custom-gcode_strength',
        groups: [{ title: 'Walls', icon: null, keys: ['wall_loops', 'sparse_infill_density'] }],
      },
      {
        title: 'Support',
        icon: 'custom-gcode_support',
        groups: [{ title: 'Support', icon: null, keys: ['enable_support', 'support_type'] }],
      },
    ],
    filament: [
      { title: 'Filament', icon: 'custom-gcode_filament', groups: [{ title: 'Temperature', icon: null, keys: ['nozzle_temperature'] }] },
    ],
    printer: [
      { title: 'Machine G-code', icon: 'custom-gcode_gcode', groups: [{ title: 'G-code', icon: null, keys: ['machine_start_gcode'] }] },
    ],
  },
  options: {
    layer_height: { type: 'coFloat', label: 'Layer height', tooltip: 'Slicing height for each layer', unit: 'mm', mode: 'simple' },
    wall_loops: { type: 'coInt', label: 'Wall loops', mode: 'simple' },
    sparse_infill_density: { type: 'coPercent', label: 'Sparse infill density', mode: 'advanced' },
    enable_support: { type: 'coBool', label: 'Enable support', mode: 'simple' },
    support_type: {
      type: 'coEnum',
      label: 'Type',
      mode: 'simple',
      enum: [
        { value: 'normal(auto)', label: 'Normal (auto)' },
        { value: 'tree(auto)', label: 'Tree (auto)' },
      ],
    },
    nozzle_temperature: { type: 'coInts', label: 'Nozzle temperature', mode: 'simple' },
    machine_start_gcode: { type: 'coString', label: 'Machine start G-code', mode: 'advanced' },
  },
  it: { 'Layer height': 'Altezza strato', Quality: 'Qualità' },
};

function Harness({
  editable = true,
  section = 'process',
  simple = false,
}: {
  editable?: boolean;
  section?: Section;
  simple?: boolean;
}) {
  const [draft, setDraft] = useState<Draft>({});
  return (
    <>
      <output data-testid="draft">{JSON.stringify(draft)}</output>
      <SettingsPanel
        layout={LAYOUT}
        section={section}
        settings={SETTINGS}
        draft={draft}
        editable={editable}
        simple={simple}
        onChange={(s, v) => setDraft((d) => withValue(d, s, v))}
        onReset={(key) => setDraft((d) => withoutKey(d, key))}
      />
    </>
  );
}

const draftOf = () => JSON.parse(screen.getByTestId('draft').textContent || '{}');

describe('SettingsPanel', () => {
  it('lays settings out on the slicer pages and groups, with its labels', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Quality', 'Strength', 'Support', 'Other values']);
    expect(screen.getByRole('heading', { name: 'Layer height' })).toBeInTheDocument();
    expect(screen.getByTestId('setting-layer_height')).toHaveTextContent('Layer height');
    await user.click(screen.getByRole('tab', { name: /Support/ }));
    expect(screen.getByRole('option', { name: 'Tree (auto)' })).toBeInTheDocument(); // the slicer's enum label
  });

  it('shows the Italian wording of the slicer', async () => {
    const { default: i18n } = await import('../../i18n');
    await i18n.changeLanguage('it');
    try {
      render(<Harness />);
      expect(screen.getByRole('tab', { name: 'Qualità' })).toBeInTheDocument();
      expect(screen.getByTestId('setting-layer_height')).toHaveTextContent('Altezza strato');
    } finally {
      await i18n.changeLanguage('en');
    }
  });

  it('edits a number, shows original and changed value, and restores it', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const row = screen.getByTestId('setting-layer_height');
    const input = within(row).getByRole('textbox', { name: 'Layer height' });
    await user.clear(input);
    await user.type(input, '0.12');
    expect(draftOf()).toEqual({ layer_height: '0.12' });
    expect(row).toHaveTextContent('Original: 0.2');
    await user.click(within(row).getByRole('button', { name: /Restore the original value/ }));
    expect(draftOf()).toEqual({});
    expect(within(row).getByRole('textbox', { name: 'Layer height' })).toHaveValue('0.2');
  });

  it('flags a value out of range (the backend range)', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByRole('textbox', { name: 'Layer height' });
    await user.clear(input);
    await user.type(input, '0.9');
    expect(within(screen.getByTestId('setting-layer_height')).getByRole('alert')).toHaveTextContent('At most 0.6');
  });

  it('switches a boolean and picks an option in the project format', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('tab', { name: /Support/ }));
    await user.click(screen.getByRole('checkbox', { name: 'Enable support' }));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Type' }), 'tree(auto)');
    expect(draftOf()).toEqual({ enable_support: '1', support_type: 'tree(auto)' });
    expect(screen.getByRole('tab', { name: /Support · 2/ })).toBeInTheDocument();
  });

  it('edits one value per filament in the filament preset', async () => {
    const user = userEvent.setup();
    render(<Harness section="filament" />);
    const second = screen.getByRole('textbox', { name: 'Nozzle temperature — Filament 2' });
    await user.clear(second);
    await user.type(second, '245');
    expect(draftOf()).toEqual({ nozzle_temperature: ['220', '245'] });
  });

  it('shows the system preset state and the objects with their own value', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('tab', { name: /Strength/ }));
    const row = screen.getByTestId('setting-wall_loops');
    expect(row).toHaveTextContent('differs from the system preset');
    expect(row).toHaveTextContent('Cube B = 5');
  });

  it('searches the pages by name or key', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(screen.getByRole('searchbox'), 'wall_lo');
    expect(screen.getByTestId('setting-wall_loops')).toBeInTheDocument();
    expect(screen.queryByTestId('setting-layer_height')).not.toBeInTheDocument();
  });

  it('keeps identifiers and keys the slicer places nowhere read only, under Other values', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('tab', { name: /Other values/ }));
    expect(within(screen.getByTestId('setting-printer_settings_id')).queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('hides read-only settings until asked', async () => {
    const user = userEvent.setup();
    render(<Harness section="printer" />);
    expect(screen.queryByTestId('setting-machine_start_gcode')).not.toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: 'Show read only' }));
    expect(screen.getByTestId('setting-machine_start_gcode')).toHaveTextContent('Text or G-code: read only.');
  });

  it('a file that cannot be edited shows every value as text', () => {
    render(<Harness editable={false} />);
    expect(screen.queryByRole('textbox', { name: 'Layer height' })).not.toBeInTheDocument();
    expect(screen.getByTestId('setting-layer_height')).toHaveTextContent('0.2');
  });
});

describe('client layout', () => {
  it("Simple shows the slicer's Simple settings only, and no Other values tab", async () => {
    const user = userEvent.setup();
    render(<Harness simple />);
    await user.click(screen.getByRole('tab', { name: /Strength/ }));
    expect(screen.getByTestId('setting-wall_loops')).toBeInTheDocument();
    expect(screen.queryByTestId('setting-sparse_infill_density')).not.toBeInTheDocument(); // advanced in the slicer
    expect(screen.queryByRole('tab', { name: /Other values/ })).not.toBeInTheDocument();
  });

  it('shows the object tools disabled, saying they come in phase 2', () => {
    render(<GizmoToolbar />);
    const tools = within(screen.getByRole('toolbar')).getAllByRole('button');
    expect(tools).toHaveLength(6);
    tools.forEach((b) => expect(b).toBeDisabled());
    expect(tools[0]).toHaveAccessibleName(/Move: coming in phase 2/);
  });
});
