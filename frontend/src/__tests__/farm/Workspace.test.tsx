/**
 * Printfarm (fork): 3MF Workspace. The draft of unsaved changes (checks, back to the original, decimal comma),
 * the settings panel (edit, original and changed values, restore, search, read-only and greyed settings, the
 * pattern listbox, one filament, an object's own settings), the objects tree, the 3D toolbar and menus.
 */
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as THREE from 'three';
import { render } from '../utils';
import type { WorkspaceDocument, WorkspaceSetting } from '../../farm/workspace/api';
import { dependencies } from '../../farm/workspace/dependencies';
import { consistencyWarnings } from '../../farm/workspace/warnings';
import { PresetDialog } from '../../farm/workspace/Dialogs';
import {
  elementError,
  fromLocaleDecimal,
  toLocaleDecimal,
  valueErrors,
  withValue,
  withoutKey,
  type Draft,
} from '../../farm/workspace/draft';
import { objectMenu } from '../../farm/workspace/menus';
import { ObjectsPanel } from '../../farm/workspace/ObjectsPanel';
import { SettingsPanel } from '../../farm/workspace/SettingsPanel';
import { ContextMenu, MainToolbar } from '../../farm/workspace/Viewport';
import { meshVolume, objectStats } from '../../farm/workspace/viewTools';
import type { ViewerScene } from '../../components/ModelViewer';
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
const PATTERN: WorkspaceSetting = {
  ...base,
  key: 'sparse_infill_pattern',
  label: 'Sparse infill pattern',
  section: 'process',
  group: 'infill',
  tab: 'strength',
  type: 'enum',
  options: [
    { value: 'grid', label: 'Grid' },
    { value: 'gyroid', label: 'Gyroid' },
    { value: 'honeycomb', label: 'Honeycomb' },
  ],
  value: 'grid',
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
  type: 'string',
  multiline: true,
  value: 'G28',
};
const RAFT: WorkspaceSetting = { ...base, key: 'raft_layers', label: 'Raft layers', section: 'process', type: 'int', value: '0' };
const MODEL: WorkspaceSetting = {
  ...base,
  key: 'printer_model',
  label: 'printer_model',
  section: 'printer',
  type: 'string',
  editable: false,
  readonly_reason: 'identity',
  value: 'Test Printer',
};
const SETTINGS = [LAYER, WALLS, DENSITY, PATTERN, SUPPORT, TYPE, TEMP, PRINTER_ID, GCODE, RAFT, MODEL];

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

  it('writes numbers with a decimal comma in Italian and keeps the point in the project', () => {
    expect(toLocaleDecimal('0.2', 'it')).toBe('0,2');
    expect(toLocaleDecimal('0.', 'it')).toBe('0,'); // while typing "0,"
    expect(toLocaleDecimal('0.2', 'en')).toBe('0.2');
    expect(toLocaleDecimal('0.2,0.3', 'it')).toBe('0.2,0.3'); // not one number: as it is
    expect(fromLocaleDecimal('0,12', 'it')).toBe('0.12');
    expect(fromLocaleDecimal('15,5%', 'it')).toBe('15.5%');
    expect(fromLocaleDecimal('0,12', 'en')).toBe('0,12'); // English: a comma is not a decimal
  });

  it("greys and hides what depends on a switch that is off, as OrcaSlicer's toggle_field / toggle_line", () => {
    const values: Record<string, string> = {
      enable_support: '0', raft_layers: '0', brim_type: 'no_brim', wall_loops: '2', support_type: 'tree(auto)', support_style: 'organic',
      ironing_type: 'no ironing', spiral_mode: '0', fuzzy_skin: 'none',
    };
    const off = dependencies((k) => values[k]);
    expect(off.disabled.has('support_type')).toBe(true); // greyed, as toggle_field
    expect(off.disabled.has('brim_width')).toBe(true);
    expect(off.hidden.has('ironing_flow')).toBe(true); // hidden, as toggle_line
    expect(off.hidden.has('spiral_mode_smooth')).toBe(true);
    expect(off.hidden.has('fuzzy_skin_thickness')).toBe(false); // "none" is "Painted only" in OrcaSlicer 2.5
    expect(off.disabled.has('seam_position')).toBe(false); // walls are on
    const on = dependencies((k) => ({ ...values, enable_support: '1' })[k]);
    expect(on.disabled.has('support_type')).toBe(false);
    expect(on.hidden.has('tree_support_tip_diameter')).toBe(false); // organic tree support: its own options
    expect(on.hidden.has('tree_support_branch_angle')).toBe(true); // the other tree styles' options
    expect(dependencies(() => undefined).disabled.size).toBe(0); // a parent the project lacks: nothing greyed
  });

  it("warns about inconsistent values as the slicer does, without changing them", () => {
    const values: Record<string, string | string[]> = {
      layer_height: '0.6', nozzle_diameter: ['0.4'], spiral_mode: '1', wall_loops: '2', top_shell_layers: '0', sparse_infill_density: '0%',
      enable_support: '0', enforce_support_layers: '0', detect_thin_wall: '0', overhang_reverse: '0', timelapse_type: '0',
      nozzle_temperature: ['250', '240'], nozzle_temperature_range_low: ['190', '230'], nozzle_temperature_range_high: ['230', '260'],
    };
    const ids = consistencyWarnings((k) => values[k]).map((w) => w.id);
    expect(ids).toEqual(['layerNozzle', 'spiral', 'nozzleTemperature']);
    expect(consistencyWarnings(() => undefined)).toEqual([]);
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
        groups: [{ title: 'Walls', icon: null, keys: ['wall_loops', 'sparse_infill_density', 'sparse_infill_pattern'] }],
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
      { title: 'Machine G-code', icon: 'custom-gcode_gcode', groups: [{ title: 'G-code', icon: null, keys: ['machine_start_gcode', 'machine_end_gcode', 'printer_model'] }] },
    ],
  },
  options: {
    layer_height: { type: 'coFloat', label: 'Layer height', tooltip: 'Slicing height for each layer', unit: 'mm', mode: 'simple' },
    wall_loops: { type: 'coInt', label: 'Wall loops', mode: 'simple' },
    sparse_infill_density: { type: 'coPercent', label: 'Sparse infill density', mode: 'advanced' },
    sparse_infill_pattern: {
      type: 'coEnum',
      label: 'Sparse infill pattern',
      mode: 'simple',
      enum: [
        { value: 'grid', label: 'Grid', icon: 'param_grid' },
        { value: 'gyroid', label: 'Gyroid', icon: 'param_gyroid' },
        { value: 'honeycomb', label: 'Honeycomb', icon: 'param_honeycomb' },
      ],
    },
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
    machine_start_gcode: { type: 'coString', label: 'Machine start G-code', mode: 'advanced', multiline: true },
    machine_end_gcode: { type: 'coString', label: 'Machine end G-code', mode: 'advanced', multiline: true },
    printer_model: { type: 'coString', label: 'Printer type', mode: 'develop' },
  },
  it: { 'Layer height': 'Altezza strato', Quality: 'Qualità' },
  object_keys: ['layer_height', 'wall_loops', 'sparse_infill_density', 'sparse_infill_pattern', 'enable_support', 'support_type'],
  plate_keys: { print_sequence: 'print_sequence' },
};

function Harness({
  editable = true,
  section = 'process',
  simple = false,
  index,
  own,
}: {
  editable?: boolean;
  section?: Section;
  simple?: boolean;
  index?: number;
  own?: Record<string, string>;
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
        mode={simple ? 'simple' : 'expert'}
        develop
        index={index}
        object={own ? { own } : undefined}
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
    await user.click(screen.getByRole('tab', { name: /Strength/ }));
    await user.click(screen.getByRole('combobox', { name: 'Sparse infill pattern' }));
    expect(screen.getByRole('option', { name: 'Gyroid' })).toBeInTheDocument(); // the slicer's enum label
  });

  it('shows the Italian wording of the slicer', async () => {
    const { default: i18n } = await import('../../i18n');
    await i18n.changeLanguage('it');
    try {
      render(<Harness />);
      expect(screen.getByRole('tab', { name: 'Qualità' })).toBeInTheDocument();
      expect(screen.getByTestId('setting-layer_height')).toHaveTextContent('Altezza strato');
      // a decimal comma on screen, a point in the change
      const input = screen.getByRole('textbox', { name: 'Altezza strato' });
      expect(input).toHaveValue('0,2');
      await userEvent.setup().clear(input);
      await userEvent.setup().type(input, '0,12');
      expect(draftOf()).toEqual({ layer_height: '0.12' });
      expect(input).toHaveValue('0,12');
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
    expect(row.getAttribute('title')).toContain('Original: 0.2'); // in the tooltip, as OrcaSlicer's hover
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
    // greyed while support is off, as the slicer
    expect(screen.getByTestId('setting-support_type')).toHaveAttribute('data-greyed', 'true');
    expect(screen.queryByRole('combobox', { name: 'Type' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', { name: 'Enable support' }));
    expect(screen.getByTestId('setting-support_type')).not.toHaveAttribute('data-greyed');
    await user.click(screen.getByRole('combobox', { name: 'Type' }));
    await user.click(screen.getByRole('option', { name: 'Tree (auto)' }));
    expect(draftOf()).toEqual({ enable_support: '1', support_type: 'tree(auto)' });
    expect(screen.getByRole('tab', { name: /Support\s*2/ })).toBeInTheDocument();
  });

  it('edits one value per filament in the filament preset', async () => {
    const user = userEvent.setup();
    render(<Harness section="filament" />);
    const second = screen.getByRole('textbox', { name: 'Nozzle temperature — Filament 2' });
    await user.clear(second);
    await user.type(second, '245');
    expect(draftOf()).toEqual({ nozzle_temperature: ['220', '245'] });
  });

  it("edits one filament's value in that filament's window", async () => {
    const user = userEvent.setup();
    render(<Harness section="filament" index={1} />);
    const input = screen.getByRole('textbox', { name: 'Nozzle temperature' }); // one input, no per-filament list
    expect(input).toHaveValue('240');
    expect(screen.getAllByRole('textbox')).toHaveLength(1);
    await user.clear(input);
    await user.type(input, '250');
    expect(draftOf()).toEqual({ nozzle_temperature: ['220', '250'] });
  });

  it('picks a pattern from the listbox with the keyboard, its icons shown', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('tab', { name: /Strength/ }));
    const box = screen.getByRole('combobox', { name: 'Sparse infill pattern' });
    expect(box.querySelector('img')).not.toBeNull(); // the value's icon in the closed field
    box.focus();
    await user.keyboard('{ArrowDown}');
    const list = screen.getByRole('listbox');
    expect(box).toHaveAttribute('aria-expanded', 'true');
    expect(within(list).getAllByRole('option')).toHaveLength(3);
    within(list).getAllByRole('option').forEach((o) => expect(o.querySelector('img')).not.toBeNull());
    await user.keyboard('{ArrowDown}');
    expect(box.getAttribute('aria-activedescendant')).toBe(within(list).getByRole('option', { name: 'Gyroid' }).id);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(draftOf()).toEqual({});
    await user.keyboard('{ArrowDown}h{Enter}'); // type-ahead
    expect(draftOf()).toEqual({ sparse_infill_pattern: 'honeycomb' });
  });

  it("shows an object's settings read only, its own first with the global value", async () => {
    render(<Harness own={{ wall_loops: '5' }} />);
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Frequent1', 'Quality', 'Strength1', 'Support']);
    const row = screen.getByTestId('setting-wall_loops');
    expect(row).toHaveTextContent('5');
    expect(row).toHaveTextContent('global: 3');
    expect(within(row).queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByTestId('setting-nozzle_temperature')).not.toBeInTheDocument();
  });

  it('shows the system preset state and the objects with their own value', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('tab', { name: /Strength/ }));
    const row = screen.getByTestId('setting-wall_loops');
    expect(row.getAttribute('title')).toContain('differs from the system preset');
    expect(row.getAttribute('title')).toContain('Cube B = 5');
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

  it('edits G-code in a full-width text box, line breaks and all', async () => {
    const user = userEvent.setup();
    render(<Harness section="printer" />);
    const box = screen.getByRole('textbox', { name: 'Machine start G-code' });
    expect(box.tagName).toBe('TEXTAREA');
    await user.type(box, '{Enter}G1 Z5');
    expect(draftOf()).toEqual({ machine_start_gcode: 'G28\nG1 Z5' });
  });

  it('shows read-only settings with their reason, and an option the file lacks as the system preset value', async () => {
    const user = userEvent.setup();
    render(<Harness section="printer" />);
    expect(screen.getByTestId('setting-printer_model').getAttribute('title')).toContain('Preset or printer identity: read only.');
    // in the slicer's layout, not in the file: listed, never guessed
    expect(screen.getByTestId('setting-machine_end_gcode')).toHaveTextContent('from the system preset (not in the file)');
    expect(within(screen.getByTestId('setting-machine_end_gcode')).queryByRole('textbox')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Filters' }));
    await user.click(screen.getByRole('checkbox', { name: 'Show read only' }));
    expect(screen.queryByTestId('setting-printer_model')).not.toBeInTheDocument();
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

  it("shows OrcaSlicer's toolbar in its order: measure works, geometry tools are phase 2", async () => {
    const user = userEvent.setup();
    const onTool = vi.fn();
    render(<MainToolbar layout={LAYOUT} active={null} onTool={onTool} />);
    const tools = within(screen.getByRole('toolbar')).getAllByRole('button');
    expect(tools).toHaveLength(24);
    expect(tools[0]).toHaveAccessibleName(/^Add \[Ctrl\+I\]: coming in phase 2/);
    expect(tools[9]).toHaveAccessibleName(/^Move \[M\]: coming in phase 2/);
    expect(tools.filter((b) => !b.hasAttribute('disabled')).map((b) => b.getAttribute('aria-label'))).toEqual(['Measure [U]']);
    await user.click(screen.getByRole('button', { name: 'Measure [U]' }));
    expect(onTool).toHaveBeenCalledWith('measure');
  });

  it('opens a right-click menu where only what looks works, and Esc closes it', async () => {
    const user = userEvent.setup();
    const on = { select: vi.fn(), zoom: vi.fn(), info: vi.fn(), settings: vi.fn() };
    const onClose = vi.fn();
    render(<ContextMenu items={objectMenu((s) => s, 'Del', true, on)} at={{ x: 10, y: 10 }} onClose={onClose} />);
    const menu = screen.getByRole('menu');
    expect(within(menu).getByRole('menuitem', { name: 'Clone Ctrl+K' })).toHaveAttribute('aria-disabled', 'true');
    expect(within(menu).getByRole('menuitemcheckbox', { name: /Printable/ })).toHaveAttribute('aria-checked', 'true');
    await user.click(within(menu).getByRole('menuitem', { name: 'Edit Process Settings' }));
    expect(on.settings).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.click(within(menu).getByRole('menuitem', { name: 'Clone Ctrl+K' }));
    expect(onClose).toHaveBeenCalledTimes(1); // disabled: nothing happens
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('closes a settings window with Esc, but an open list first', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <PresetDialog
        layout={LAYOUT}
        kind="filament"
        filament={1}
        name={null}
        settings={SETTINGS}
        draft={{}}
        editable
        onChange={() => {}}
        onReset={() => {}}
        onClose={onClose}
      />,
    );
    expect(screen.getByRole('dialog')).toHaveTextContent('2'); // the filament's own chip
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

const DOC = {
  plates: [
    { index: 1, name: 'First', object_ids: ['2'], instances: [], metadata: { plater_id: '1' } },
    { index: 2, name: null, object_ids: ['4'], instances: [], metadata: { plater_id: '2', print_sequence: 'by object' } },
  ],
  objects: [
    { id: '2', name: 'Cube A', extruder: '1', parts: [{ id: '1', name: 'Cube A', type: 'normal_part' }], overrides: [] },
    {
      id: '4',
      name: 'Cube B',
      extruder: '2',
      parts: [
        { id: '1', name: 'Cube B', type: 'normal_part' },
        { id: '2', name: 'Blocker', type: 'support_blocker' },
      ],
      overrides: [{ key: 'wall_loops', value: '5', part: null }],
    },
  ],
  settings: [...SETTINGS, { ...base, key: 'print_sequence', label: 'Print sequence', section: 'process', tab: 'other', type: 'string', value: 'by layer' }],
} as unknown as WorkspaceDocument;

describe('objects view', () => {
  it('lists plates, objects and parts, marks own settings, selects and opens menus', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const onMenu = vi.fn();
    render(
      <ObjectsPanel
        layout={LAYOUT}
        doc={DOC}
        draft={{}}
        colours={['#ff0000', '#00ff00']}
        stats={new Map()}
        selection={null}
        onSelect={onSelect}
        onMenu={onMenu}
      />,
    );
    const tree = screen.getByRole('tree');
    expect(within(tree).getByText('Plate 1 · First')).toBeInTheDocument();
    expect(within(tree).getByText('Blocker')).toBeInTheDocument(); // a part of its own kind
    const cubeB = within(tree).getAllByText('Cube B')[0].closest('div')!; // the object (its part has its name too)
    expect(within(cubeB).getByAltText('Has settings of its own')).toBeInTheDocument();
    expect(within(within(tree).getByText('Cube A').closest('div')!).queryByAltText('Has settings of its own')).toBeNull();
    await user.click(within(tree).getAllByText('Cube B')[0]);
    expect(onSelect).toHaveBeenCalledWith({ kind: 'object', id: '4', plate: 2 });
    fireEvent.contextMenu(within(tree).getByText('Plate 2'), { clientX: 5, clientY: 6 });
    expect(onMenu).toHaveBeenCalledWith({ x: 5, y: 6 }, { kind: 'plate', plate: 2 });
    await user.type(screen.getByRole('searchbox'), 'cube a');
    expect(within(tree).queryByText('Cube B')).not.toBeInTheDocument();
  });

  it("shows a plate's own settings against the project's", () => {
    render(
      <ObjectsPanel
        layout={LAYOUT}
        doc={DOC}
        draft={{}}
        colours={[]}
        stats={new Map()}
        selection={{ kind: 'plate', plate: 2 }}
        onSelect={() => {}}
        onMenu={() => {}}
      />,
    );
    expect(screen.getByTestId('plate-print_sequence')).toHaveTextContent('by object');
  });
});

describe('3D object info', () => {
  it('measures volume, size and triangles of each object', () => {
    expect(meshVolume(new THREE.BoxGeometry(2, 3, 4))).toBeCloseTo(24);
    const model = new THREE.Group();
    const item = new THREE.Group();
    item.userData = { objectId: '4', printable: false };
    item.add(new THREE.Mesh(new THREE.BoxGeometry(10, 20, 30)));
    model.add(item);
    const stats = objectStats({ model } as unknown as ViewerScene).get('4')!;
    expect(stats.size).toEqual([10, 30, 20]); // the slicer's X, Y, Z (three.js Y is up)
    expect(stats.volume).toBeCloseTo(6000);
    expect(stats.triangles).toBe(12);
    expect(stats.printable).toBe(false);
  });
});
