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
import { PresetDialog, ShortcutsDialog } from '../../farm/workspace/Dialogs';
import {
  elementError,
  fromLocaleDecimal,
  toLocaleDecimal,
  valueErrors,
  withValue,
  withoutKey,
  type Draft,
} from '../../farm/workspace/draft';
import { emptyMenu, instanceMenu, multiMenu, objectMenu, partMenu } from '../../farm/workspace/menus';
import { ObjectsPanel } from '../../farm/workspace/ObjectsPanel';
import { SettingsPanel } from '../../farm/workspace/SettingsPanel';
import { ContextMenu, MainToolbar, ObjectInfo } from '../../farm/workspace/Viewport';
import {
  CANVAS_DEFAULTS,
  type ObjectStats,
  VIEW_KEYS,
  drawMeasure,
  meshVolume,
  navigatorView,
  objectStats,
  openEdges,
  parseVolumes,
  plateOrigin,
  targetStats,
} from '../../farm/workspace/viewTools';
import JSZip from 'jszip';
import { buildModelGroup, parse3MF } from '../../components/ModelViewer';
import { CanvasButtons } from '../../farm/workspace/Viewport';
import type { ViewerScene } from '../../components/ModelViewer';
import { type OrcaLayout, type Section, keyText, orcaFormat, orcaPlural } from '../../farm/workspace/orcaLayout';

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

describe('batch B: selection, menus, canvas', () => {
  it('counts the open edges of a mesh (mesh errors), none on a closed one', () => {
    const tetra = new THREE.BufferGeometry();
    tetra.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1], 3));
    tetra.setIndex([0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]);
    expect(openEdges(tetra)).toBe(0);
    tetra.setIndex([0, 2, 1]);
    expect(openEdges(tetra)).toBe(3);
  });

  it("has OrcaSlicer's part, multiple selection and empty space menus, only what looks working", () => {
    const tr = (s: string) => s;
    const part = partMenu(tr, 'Del', { select: () => {}, zoom: () => {}, settings: () => {} });
    expect(part.filter((i) => i.onClick).map((i) => i.label)).toEqual(['Select', 'Zoom', 'Edit Process Settings']);
    expect(part.map((i) => i.label)).toContain('Change Type');
    const multi = multiMenu(tr, 'Del', true, { zoom: () => {}, deselect: () => {} });
    expect(multi.filter((i) => i.onClick).map((i) => i.label)).toEqual(['Zoom', 'Deselect All']);
    const labels = vi.fn();
    const empty = emptyMenu(tr, false, { labels });
    expect(empty.map((i) => i.label)).toEqual(['Add Primitive', 'Add Handy models', 'Add Models', 'Show Labels']);
    empty[3].onClick!();
    expect(labels).toHaveBeenCalled();
    expect(VIEW_KEYS['7']).toBe('plate'); // Ctrl+0..7
  });

  it("lists the canvas menu's switches in OrcaSlicer's order and toggles them", async () => {
    const user = userEvent.setup();
    const onOption = vi.fn();
    render(
      <CanvasButtons
        layout={LAYOUT}
        options={CANVAS_DEFAULTS}
        onOption={onOption}
        onFit={() => {}}
        section={0}
        onSection={() => {}}
        onToggleSection={() => {}}
        onSectionAngle={() => {}}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Gridlines' }));
    const items = within(screen.getByRole('menu')).getAllByRole('menuitemcheckbox');
    expect(items.map((i) => i.textContent?.replace(/Ctrl.*/, ''))).toEqual([
      '3D Navigator', 'Zoom button', 'Overhangs', 'Outline', 'Wireframe', 'X-Ray', 'Realistic View', 'Perspective', 'Axes', 'Gridlines',
      'Focus mode', 'Labels', 'Center of mass',
    ]);
    await user.click(items[4]);
    expect(onOption).toHaveBeenCalledWith('wireframe', true);
  });
});

const STATS = (over: Partial<ObjectStats> = {}): ObjectStats => ({
  size: [20, 20, 10],
  volume: 4000,
  triangles: 12,
  printable: true,
  openEdges: 0,
  centroid: new THREE.Vector3(),
  sinking: false,
  paint: { supports: false, color: false },
  variableLayers: false,
  ...over,
});

describe('batch B: tree columns, instances, info box, shortcuts, section and navigator', () => {
  it("formats OrcaSlicer's strings, plurals and key names in Italian", () => {
    const layout = {
      ...LAYOUT,
      it: {
        'Part name: %1%\n': 'Nome parte: %1%\n',
        '%1$d non-manifold edges': '%1$d geometrie con spessore zero',
        'Keyboard Shortcut\u0004Shift': 'Maiusc',
        'Left mouse': 'Tasto sinistro',
      },
    };
    expect(orcaFormat(layout, 'Part name: %1%\n', 'it', 'Base')).toBe('Nome parte: Base');
    expect(orcaFormat(layout, '[P%d]Instance %d', 'en', 1, 2)).toBe('[P1]Instance 2');
    expect(orcaPlural(layout, '%1$d non-manifold edge', '%1$d non-manifold edges', 3, 'it')).toBe('3 geometrie con spessore zero');
    expect(orcaPlural(layout, '%1$d non-manifold edge', '%1$d non-manifold edges', 1, 'en')).toBe('1 non-manifold edge');
    expect(keyText(layout, 'Shift', 'it')).toBe('Maiusc');
    expect(keyText(layout, 'Keyboard Shortcut\u0004Esc', 'en')).toBe('Esc');
    expect(keyText(layout, 'Left mouse', 'it')).toBe('Tasto sinistro');
  });

  it('shows instances, the mesh warning and only the columns some object needs', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const onMenu = vi.fn();
    const doc = {
      ...DOC,
      plates: [
        { ...DOC.plates[0], object_ids: ['2', '2'], instances: [{ object_id: '2', instance_id: '0' }, { object_id: '2', instance_id: '1' }] },
        { ...DOC.plates[1], object_ids: ['4', '2'], instances: [{ object_id: '4', instance_id: '0' }, { object_id: '2', instance_id: '2' }] },
      ],
    } as WorkspaceDocument;
    const stats = new Map([
      ['2', STATS({ sinking: true, openEdges: 3 })],
      ['4', STATS()],
    ]);
    render(<ObjectsPanel layout={LAYOUT} doc={doc} draft={{}} colours={[]} stats={stats} selection={null} onSelect={onSelect} onMenu={onMenu} />);
    const tree = screen.getByRole('tree');
    // the sinking column shows (Cube A sinks), the painting and height columns don't (nobody needs them)
    expect(within(tree).getAllByTitle('Click the icon to shift this object to the bed').length).toBeGreaterThan(0);
    expect(within(tree).queryByTitle('Click the icon to edit support painting of the object')).toBeNull();
    expect(within(tree).queryByTitle('Variable layer height')).toBeNull();
    const warning = within(tree).getAllByAltText('Errors')[0];
    expect(warning.getAttribute('title')).toContain('3 non-manifold edges');
    // three instances across two plates: OrcaSlicer marks those on another plate with its index from 0
    const plate1 = within(tree).getByText('Plate 1 · First').closest('li')!;
    expect(within(plate1).getByText('Instances')).toBeInTheDocument();
    expect(within(plate1).getByText('Instance 1')).toBeInTheDocument();
    expect(within(plate1).getByText('[P1]Instance 3')).toBeInTheDocument();
    await user.click(within(plate1).getByText('Instance 2'));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'object', id: '2', plate: 1, instance: 1 });
    fireEvent.contextMenu(within(plate1).getByText('Instance 2'), { clientX: 1, clientY: 2 });
    expect(onMenu).toHaveBeenLastCalledWith({ x: 1, y: 2 }, { kind: 'object', id: '2', plate: 1, instance: 1 });
    expect(instanceMenu((t) => t, 1).map((i) => i.label)).toEqual(['Set as An Individual Object']);
  });

  it("writes the info box as OrcaSlicer: a part's name, or only how many are selected", () => {
    const { rerender } = render(<ObjectInfo layout={LAYOUT} name="Cube" part="Base" stats={STATS({ openEdges: 2 })} onClose={() => {}} />);
    expect(screen.getByText('Part name: Base')).toBeInTheDocument();
    expect(screen.queryByText(/Object name/)).toBeNull();
    expect(screen.getByText('Size: 20 x 20 x 10 mm')).toBeInTheDocument();
    expect(screen.getByText('Error: 2 non-manifold edges.')).toBeInTheDocument();
    rerender(<ObjectInfo layout={LAYOUT} name="Cube" count={2} stats={STATS()} onClose={() => {}} />);
    expect(screen.getByText('Number of currently selected objects: 2')).toBeInTheDocument();
    expect(screen.queryByText(/Size/)).toBeNull();
  });

  it("lists OrcaSlicer's shortcuts by page and section, those not here yet muted", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const layout: OrcaLayout = {
      ...LAYOUT,
      shortcuts: [
        { title: 'Global', caption: 'Available anywhere in the window.', context: 'Global', rows: [{ id: 'NewProject', section: 'Project', keys: ['Ctrl', 'N'], label: 'New Project' }] },
        {
          title: 'Prepare',
          caption: 'Available while the 3D view on the Prepare tab has focus.',
          context: 'Plater',
          rows: [
            { id: 'SelectAll', section: 'Selection', keys: ['Ctrl', 'A'], label: 'Select all objects' },
            { id: null, section: 'Display', keys: ['Alt', 'Mouse wheel'], label: 'Move section plane' },
            { id: 'GizmoMove', section: 'Gizmos', keys: ['M'], label: 'Gizmo move' },
          ],
        },
      ],
    };
    render(<ShortcutsDialog layout={layout} context="Plater" works={(r) => r.id === 'SelectAll' || r.id === null} onClose={onClose} />);
    expect(screen.getByRole('tab', { name: 'Prepare' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('heading', { name: 'Selection' })).toBeInTheDocument();
    expect(screen.getByText('Gizmo move').parentElement).toHaveAttribute('title', expect.stringContaining('phase 2'));
    expect(screen.getByText('Select all objects').parentElement).not.toHaveAttribute('title');
    await user.click(screen.getByRole('tab', { name: 'Global' }));
    expect(screen.getByText('New Project')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalled();
  });

  it('moves the section plane from the icon: wheel, right click toggles, middle click sets the angle', () => {
    const on = { section: vi.fn(), toggle: vi.fn(), angle: vi.fn() };
    render(
      <CanvasButtons
        layout={LAYOUT}
        options={CANVAS_DEFAULTS}
        onOption={() => {}}
        onFit={() => {}}
        section={0.5}
        onSection={on.section}
        onToggleSection={on.toggle}
        onSectionAngle={on.angle}
      />,
    );
    const icon = screen.getByRole('button', { name: 'Section view' });
    fireEvent.wheel(icon, { deltaY: -100 });
    expect(on.section).toHaveBeenLastCalledWith(0.51);
    fireEvent.contextMenu(icon);
    expect(on.toggle).toHaveBeenCalled();
    fireEvent.mouseDown(icon, { button: 1 });
    expect(on.angle).toHaveBeenCalled();
    fireEvent.click(icon); // opens the panel: slider, Set viewing angle, Reset
    expect(screen.getByRole('slider', { name: 'Section view' })).toHaveValue('0.5');
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(on.section).toHaveBeenLastCalledWith(0);
  });

  it("looks from a navigator face, edge or corner, lays plates out as OrcaSlicer and reads collision volumes", () => {
    expect(navigatorView('front', 0.5, 0.5)).toBe('front');
    expect(navigatorView('front', 0.95, 0.5)).toEqual([1, 0, 1]); // its right edge: between front and right
    expect(navigatorView('top', 0.05, 0.05)).toEqual([-1, 1, -1]); // the top's far left corner
    // 4 plates: 2 columns; 5 plates: 3 (sqrt 2.24 rounds to 2, below it: one more)
    expect(plateOrigin(1, 4, { x: 100, y: 50 })).toEqual({ x: 120, z: 0 });
    expect(plateOrigin(2, 4, { x: 100, y: 50 })).toEqual({ x: 0, z: 60 });
    expect(plateOrigin(2, 5, { x: 100, y: 50 })).toEqual({ x: 240, z: 0 });
    expect(parseVolumes('0x0, 10x0, 10x10 | 5..20;0x0,1x0,1x1 | bad', 250)).toEqual([
      { points: [[0, 0], [10, 0], [10, 10]], zmin: 0, zmax: 250 },
      { points: [[0, 0], [1, 0], [1, 1]], zmin: 5, zmax: 20 },
    ]);
  });

  it('marks a sinking, painted or variable layer height object from the scene', () => {
    const model = new THREE.Group();
    const item = new THREE.Group();
    item.userData = { objectId: '7', printable: true, instance: 0, paint: { supports: true, color: false }, variableLayers: true };
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(10, 10, 10));
    mesh.position.y = 2; // 3 mm below the bed
    item.add(mesh);
    model.add(item);
    const s = objectStats({ model } as unknown as ViewerScene).get('7')!;
    expect(s.sinking).toBe(true);
    expect(s.paint).toEqual({ supports: true, color: false });
    expect(s.variableLayers).toBe(true);
  });
});

describe('review of #142: info box size, instances across plates, freed helpers', () => {
  it("measures a scaled and rotated instance as OrcaSlicer's info box: its transformed box on the bed's axes", () => {
    const model = new THREE.Group();
    const item = new THREE.Group();
    item.userData = { objectId: '3', instance: 0 };
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(10, 10, 10).toNonIndexed());
    mesh.scale.set(2, 1, 1); // 20 mm along its own x...
    mesh.rotation.y = Math.PI / 2; // ...turned to the scene's z (the slicer's Y)
    item.add(mesh);
    model.add(item);
    const s = targetStats({ model } as unknown as ViewerScene, { objectId: '3', instance: 0 })!;
    expect(s.size.map((v) => Math.round(v))).toEqual([10, 20, 10]);
    expect(Math.round(s.volume)).toBe(2000);
  });

  it('numbers instances and places each on its own plate, as the object list does', async () => {
    const zip = new JSZip();
    const v = [[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]].map(([x, y, z]) => `<vertex x="${x}" y="${y}" z="${z}"/>`).join('');
    const t = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]].map(([a, b, c]) => `<triangle v1="${a}" v2="${b}" v3="${c}"/>`).join('');
    zip.file(
      '3D/3dmodel.model',
      `<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>` +
        `<object id="2" type="model"><mesh><vertices>${v}</vertices><triangles>${t}</triangles></mesh></object></resources>` +
        `<build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 10 10 0"/><item objectid="2" transform="1 0 0 0 1 0 0 0 1 50 10 0"/>` +
        `<item objectid="2" transform="1 0 0 0 1 0 0 0 1 300 10 0"/></build></model>`,
    );
    const inst = (plate: number, ids: number[]) =>
      `<plate><metadata key="plater_id" value="${plate}"/>` +
      ids.map((i) => `<model_instance><metadata key="object_id" value="2"/><metadata key="instance_id" value="${i}"/></model_instance>`).join('') +
      '</plate>';
    zip.file('Metadata/model_settings.config', `<?xml version="1.0"?><config><object id="2"></object>${inst(1, [0, 1])}${inst(2, [2])}</config>`);
    const parsed = await parse3MF(await zip.generateAsync({ type: 'arraybuffer' }));
    const shown = (plate: number) => buildModelGroup(parsed, plate, [], true).children.filter((c) => c.userData.objectId != null);
    expect(shown(1).map((c) => c.userData.instance)).toEqual([0, 1]);
    expect(shown(2).map((c) => [c.userData.instance, c.userData.plateId])).toEqual([[2, 2]]);
  });

  it("frees the measuring line's geometries and materials when it is redrawn", () => {
    const scene = new THREE.Scene();
    const view = { scene } as unknown as ViewerScene;
    drawMeasure(view, new THREE.Vector3(), new THREE.Vector3(1, 0, 0));
    const used = new Set<THREE.EventDispatcher<{ dispose: object }>>();
    const freed = new Set<unknown>();
    scene.traverse((o) => {
      if (o instanceof THREE.Mesh || o instanceof THREE.Line) used.add(o.geometry).add(o.material as THREE.Material);
    });
    for (const u of used) u.addEventListener('dispose', (e) => freed.add(e.target));
    drawMeasure(view, null, null);
    expect(scene.children).toHaveLength(0);
    expect(freed.size).toBe(5); // 2 dots and the line: 3 geometries; the dots' material and the line's
    expect([...used].every((u) => freed.has(u))).toBe(true);
  });
});
