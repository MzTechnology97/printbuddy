/**
 * Printfarm (fork): the 3MF Workspace's Save preset and Compare presets (batch D2): Orca's preset name check, a
 * section's values, the differences between two presets, what a saved user preset holds, and both dialogs.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { render } from '../utils';
import type { WorkspaceSetting } from '../../farm/workspace/api';
import { workspaceApi } from '../../farm/workspace/api';
import { api } from '../../api/client';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';
import { ComparePresetsDialog, SavePresetDialog } from '../../farm/workspace/PresetDialogs';
import { checkPresetName, presetDiff, sectionValues, suggestedName, userPresetSetting } from '../../farm/workspace/presetTools';

const setting = (key: string, section: WorkspaceSetting['section'], value: WorkspaceSetting['value'], over: Partial<WorkspaceSetting> = {}): WorkspaceSetting => ({
  key, label: key, section, type: 'string', value, editable: true, readonly_reason: null, differs_from_system: false, overrides: [], ...over,
});
const SETTINGS: WorkspaceSetting[] = [
  setting('print_settings_id', 'process', '0.20mm Standard @Test', { editable: false, readonly_reason: 'identity' }),
  setting('layer_height', 'process', '0.2'),
  setting('wall_loops', 'process', '3', { differs_from_system: true }),
  setting('filament_settings_id', 'filament', ['PLA @Test', 'PETG @Test'], { editable: false, readonly_reason: 'identity' }),
  setting('nozzle_temperature', 'filament', ['210', '240']),
  setting('printer_settings_id', 'printer', 'Test Printer', { editable: false, readonly_reason: 'identity' }),
];
const LAYOUT = {
  tag: 'v', source: 's', pages: {
    process: [{ title: 'Quality', icon: 'q', groups: [{ title: 'Layer height', icon: null, keys: ['layer_height', 'wall_loops'] }] }],
    filament: [{ title: 'Filament', icon: 'f', groups: [{ title: 'Temperature', icon: null, keys: ['nozzle_temperature'] }] }],
    printer: [],
  },
  options: { layer_height: { type: 'float', label: 'Layer height', mode: 'simple' }, wall_loops: { type: 'int', label: 'Wall loops', mode: 'simple' } },
  object_keys: [], plate_keys: {}, it: {},
} as unknown as OrcaLayout;

describe('presetTools', () => {
  it("checks names as SavePresetDialog's check_preset_name", () => {
    expect(checkPresetName('My fine 0.12', []).status).toBe('valid');
    expect(checkPresetName('a/b', []).messages[1]).toEqual({ text: 'illegal characters:', values: ['<>[]:/\\|?*"'] });
    expect(checkPresetName('x (modified)', []).status).toBe('invalid');
    expect(checkPresetName('', []).messages[0].text).toBe('The name field is not allowed to be empty.');
    expect(checkPresetName('com1.json', []).messages[0].text).toBe('The name is a reserved system name.');
    expect(checkPresetName(' lead', []).messages[0].text).toBe('The name is not allowed to start with a space.');
    expect(checkPresetName('trail ', []).messages[0].text).toBe('The name is not allowed to end with a space.');
    expect(checkPresetName('x'.repeat(151), []).messages[0].text).toBe('The name is too long.');
    expect(checkPresetName('0.20mm Standard @Test', [], ['0.20mm Standard @Test']).messages[0].text).toBe('Overwriting a system profile is not allowed.');
    expect(checkPresetName('Mine', ['Mine'])).toMatchObject({ status: 'warning' });
    expect(suggestedName('0.20mm Standard @Test', true, 'Copy')).toBe('0.20mm Standard @Test - Copy');
    expect(suggestedName('Mine', false, 'Copy')).toBe('Mine');
  });

  it("takes a section's values with the unsaved changes, a filament's element as a one-element list", () => {
    expect(sectionValues(SETTINGS, { layer_height: '0.16' }, 'process')).toEqual({ layer_height: '0.16', wall_loops: '3' });
    expect(sectionValues(SETTINGS, {}, 'filament', 1)).toEqual({ nozzle_temperature: ['240'] });
    expect(presetDiff({ a: ['1'], b: '2' }, { a: '1', b: '3' }, ['a', 'b'])).toEqual(['b']);
    expect(presetDiff({ a: '1' }, {}, ['a'])).toEqual([]); // unknown on one side: not a difference
  });

  it('a user preset holds its parent and what differs, or, detached, everything and no parent', () => {
    const values = { layer_height: '0.16', wall_loops: '3' };
    expect(userPresetSetting('process', 'Mine', 'Base', values, ['layer_height'], false)).toEqual({
      layer_height: '0.16', name: 'Mine', from: 'User', type: 'process', inherits: 'Base',
    });
    expect(userPresetSetting('process', 'Mine', 'Base', values, ['layer_height'], true)).toMatchObject({ ...values, inherits: '' });
  });
});

describe('SavePresetDialog', () => {
  beforeEach(() => {
    vi.spyOn(workspaceApi, 'presets').mockResolvedValue({ section: 'process', user: [{ id: 7, name: 'Mine', inherits: '0.20mm Standard @Test' }] });
  });

  it('saves the changes on top of the system preset as a new user preset', async () => {
    const create = vi.spyOn(api, 'createLocalPreset').mockResolvedValue({} as never);
    const onSaved = vi.fn();
    render(<SavePresetDialog layout={LAYOUT} section="process" settings={SETTINGS} draft={{ layer_height: '0.16' }} onClose={vi.fn()} onSaved={onSaved} />);
    const name = screen.getByRole('textbox');
    await waitFor(() => expect(name).toHaveValue('0.20mm Standard @Test - Copy'));
    expect(screen.getByText(/Parent preset/)).toHaveTextContent('0.20mm Standard @Test');
    fireEvent.change(name, { target: { value: 'Fine 0.16' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('Fine 0.16'));
    expect(create).toHaveBeenCalledWith({
      name: 'Fine 0.16',
      preset_type: 'process',
      setting: { layer_height: '0.16', wall_loops: '3', name: 'Fine 0.16', from: 'User', type: 'process', inherits: '0.20mm Standard @Test' },
    });
  });

  it('warns before overwriting a user preset, refuses a bad name, and overwrites when asked', async () => {
    const update = vi.spyOn(api, 'updateLocalPreset').mockResolvedValue({} as never);
    render(<SavePresetDialog layout={LAYOUT} section="process" settings={SETTINGS} draft={{}} onClose={vi.fn()} onSaved={vi.fn()} />);
    const name = screen.getByRole('textbox');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled());
    fireEvent.change(name, { target: { value: 'bad:name' } });
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    fireEvent.change(name, { target: { value: 'Mine' } });
    expect(screen.getByRole('status')).toHaveTextContent('Preset "Mine" already exists.');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(update).toHaveBeenCalledWith(7, expect.objectContaining({ setting: expect.objectContaining({ inherits: '0.20mm Standard @Test' }) })));
  });
});

describe('ComparePresetsDialog', () => {
  it('lists the differences of a user preset against the project, and carries the picked ones into it', async () => {
    vi.spyOn(workspaceApi, 'presets').mockImplementation(async (section) => ({
      section, user: section === 'process' ? [{ id: 7, name: 'Mine', inherits: '0.20mm Standard @Test' }] : [],
    }));
    vi.spyOn(workspaceApi, 'presetValues').mockResolvedValue({ section: 'process', name: 'Mine', inherits: null, values: { layer_height: '0.12', wall_loops: '3' }, missing_parent: null });
    const onTransfer = vi.fn();
    render(<ComparePresetsDialog layout={LAYOUT} settings={SETTINGS} draft={{}} editable onTransfer={onTransfer} onClose={vi.fn()} />);
    expect(screen.getByText('Select presets to compare')).toBeInTheDocument();
    const left = screen.getByRole('combobox', { name: 'Process Left Preset Value' });
    await waitFor(() => expect(within(left).getByRole('option', { name: 'Mine' })).toBeInTheDocument());
    expect(within(left).getByRole('option', { name: '0.20mm Standard @Test' })).toBeInTheDocument(); // its system parent
    fireEvent.change(left, { target: { value: 'user:7' } });
    const row = (await screen.findByText('Layer height')).closest('tr')!;
    expect(row).toHaveTextContent('0.12');
    expect(row).toHaveTextContent('0.2');
    expect(screen.queryByText('Wall loops')).not.toBeInTheDocument(); // the same on both sides
    fireEvent.click(screen.getByRole('checkbox', { name: 'Transfer values from left to right' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Layer height' }));
    fireEvent.click(screen.getByRole('button', { name: 'Transfer' }));
    expect(onTransfer).toHaveBeenCalledWith(expect.objectContaining({ key: 'layer_height' }), '0.12');
  });
});
