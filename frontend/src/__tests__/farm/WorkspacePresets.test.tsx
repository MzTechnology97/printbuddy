/**
 * Printfarm (fork): the 3MF Workspace's Save preset and Compare presets (batch D2): Orca's preset name check, a
 * section's values, the differences between two presets, what a saved user preset holds, and both dialogs.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, renderHook, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { createTestQueryClient, render } from '../utils';
import type { PresetRef, SettingValue, WorkspaceSetting } from '../../farm/workspace/api';
import type { Draft } from '../../farm/workspace/draft';
import { usePresetSwitch } from '../../farm/workspace/presetSwitch';
import { workspaceApi } from '../../farm/workspace/api';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';
import { ComparePresetsDialog, SavePresetDialog, UnsavedChangesDialog } from '../../farm/workspace/PresetDialogs';
import { checkPresetName, draftAfterSwitch, presetDiff, sectionValues, suggestedName, unsavedKeys, userPresetSetting, withSwitched } from '../../farm/workspace/presetTools';

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
    vi.spyOn(workspaceApi, 'presets').mockResolvedValue({
      section: 'process',
      user: [{ id: 7, name: 'Mine', inherits: '0.20mm Standard @Test' }],
      system: ['0.12mm Fine @Test', '0.20mm Standard @Test'],
    });
  });

  it('saves the changes on top of the system preset as a new user preset', async () => {
    const create = vi.spyOn(workspaceApi, 'saveUserPreset').mockResolvedValue({ id: 8, name: 'Fine 0.16' });
    const onSaved = vi.fn();
    render(<SavePresetDialog layout={LAYOUT} section="process" settings={SETTINGS} draft={{ layer_height: '0.16' }} onClose={vi.fn()} onSaved={onSaved} />);
    const name = screen.getByRole('textbox');
    await waitFor(() => expect(name).toHaveValue('0.20mm Standard @Test - Copy'));
    expect(screen.getByText(/Parent preset/)).toHaveTextContent('0.20mm Standard @Test');
    fireEvent.change(name, { target: { value: 'Fine 0.16' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('Fine 0.16'));
    expect(create).toHaveBeenCalledWith(
      'process',
      'Fine 0.16',
      { layer_height: '0.16', wall_loops: '3', name: 'Fine 0.16', from: 'User', type: 'process', inherits: '0.20mm Standard @Test' },
      undefined,
    );
  });

  it('warns before overwriting a user preset, refuses a bad name, and overwrites when asked', async () => {
    const update = vi.spyOn(workspaceApi, 'saveUserPreset').mockResolvedValue({ id: 7, name: 'Mine' });
    render(<SavePresetDialog layout={LAYOUT} section="process" settings={SETTINGS} draft={{}} onClose={vi.fn()} onSaved={vi.fn()} />);
    const name = screen.getByRole('textbox');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled());
    fireEvent.change(name, { target: { value: 'bad:name' } });
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    fireEvent.change(name, { target: { value: 'Mine' } });
    expect(screen.getByRole('status')).toHaveTextContent('Preset "Mine" already exists.');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith('process', 'Mine', expect.objectContaining({ inherits: '0.20mm Standard @Test' }), 7),
    );
  });

  it("never takes a system preset's name, the project's own or any other", async () => {
    render(<SavePresetDialog layout={LAYOUT} section="process" settings={SETTINGS} draft={{}} onClose={vi.fn()} onSaved={vi.fn()} />);
    const name = screen.getByRole('textbox');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled());
    fireEvent.change(name, { target: { value: '0.12mm Fine @Test' } });
    expect(screen.getByRole('status')).toHaveTextContent('Overwriting a system profile is not allowed.');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('refuses while an unsaved value of the section is invalid', async () => {
    const save = vi.spyOn(workspaceApi, 'saveUserPreset');
    save.mockClear();
    render(
      <SavePresetDialog layout={LAYOUT} section="process" settings={SETTINGS} draft={{ layer_height: 'abc' }} invalid={['layer_height']} onClose={vi.fn()} onSaved={vi.fn()} />,
    );
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    fireEvent.submit(screen.getByRole('textbox').closest('form')!);
    expect(save).not.toHaveBeenCalled();
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

describe('preset switch (the preset combos)', () => {
  const WITH_DIFF = [...SETTINGS, setting('different_settings_to_system', null, ['wall_loops', '', 'nozzle_temperature', ''], { editable: false, readonly_reason: 'identity' })];
  const value = (list: WorkspaceSetting[], key: string) => list.find((s) => s.key === key)?.value;

  it('shows the switched values, and their state against the new preset', () => {
    const after = withSwitched(WITH_DIFF, { layer_height: '0.12', print_settings_id: '0.12mm Fine @Test', different_settings_to_system: ['', '', 'nozzle_temperature', ''] });
    expect(value(after, 'layer_height')).toBe('0.12');
    expect(after.find((s) => s.key === 'wall_loops')?.differs_from_system).toBe(false);
    expect(after.find((s) => s.key === 'nozzle_temperature')?.differs_from_system).toBe(true);
    expect(withSwitched(WITH_DIFF, {})).toBe(WITH_DIFF);
  });

  it("what a switch leaves behind: the section's unsaved edits and the project's own changes, a filament's slot only", () => {
    expect(unsavedKeys(WITH_DIFF, { layer_height: '0.1' }, 'process')).toEqual(['layer_height', 'wall_loops']);
    // slot 1's switch: slot 0's edit isn't left behind, the project's own change of slot 1 (nozzle_temperature) is
    expect(unsavedKeys(WITH_DIFF, { nozzle_temperature: ['215', '240'] }, 'filament', 1)).toEqual(['nozzle_temperature']);
    expect(unsavedKeys(WITH_DIFF, {}, 'filament', 0)).toEqual([]);
    expect(unsavedKeys(WITH_DIFF, {}, 'printer')).toEqual([]);
  });

  it('Transfer keeps those values over the new preset; Discard drops them', () => {
    const after = withSwitched(WITH_DIFF, { layer_height: '0.12', wall_loops: '2' });
    const keys = ['layer_height', 'wall_loops'];
    expect(draftAfterSwitch(WITH_DIFF, after, { layer_height: '0.1' }, keys, 'process', 0, true)).toEqual({ layer_height: '0.1', wall_loops: '3' });
    expect(draftAfterSwitch(WITH_DIFF, after, { layer_height: '0.1' }, keys, 'process', 0, false)).toEqual({});
  });

  it("a filament's switch: its slot takes the new preset's value, the other slots keep their edits", () => {
    const after = withSwitched(WITH_DIFF, { nozzle_temperature: ['210', '250'], filament_settings_id: ['PLA @Test', 'PETG HF @Test'] });
    const draft = { nozzle_temperature: ['215', '245'] };
    expect(draftAfterSwitch(WITH_DIFF, after, draft, ['nozzle_temperature'], 'filament', 1, false)).toEqual({ nozzle_temperature: ['215', '250'] });
    expect(draftAfterSwitch(WITH_DIFF, after, draft, ['nozzle_temperature'], 'filament', 1, true)).toEqual({ nozzle_temperature: ['215', '245'] });
    // an edit of another slot only: still switched in this slot
    expect(draftAfterSwitch(WITH_DIFF, after, { nozzle_temperature: ['215', '240'] }, [], 'filament', 1, true)).toEqual({ nozzle_temperature: ['215', '250'] });
  });

  it('the unsaved-changes dialog lists them, with Transfer, Discard and Cancel', () => {
    const transfer = vi.fn(), discard = vi.fn(), close = vi.fn();
    render(<UnsavedChangesDialog layout={LAYOUT} preset="0.20mm Standard @Test" keys={['layer_height', 'wall_loops']} onTransfer={transfer} onDiscard={discard} onClose={close} />);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Layer height')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Layer height:')).toHaveLength(2); // under its group
    expect(within(dialog).getByText('Preset "0.20mm Standard @Test" contains the following unsaved changes:')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Transfer' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Discard' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect([transfer, discard, close].map((f) => f.mock.calls.length)).toEqual([1, 1, 1]);
  });
});

describe('usePresetSwitch: a preview applies onto the page as it is when it comes back', () => {
  const WITH_DIFF = [...SETTINGS, setting('different_settings_to_system', null, ['', '', '', ''], { editable: false, readonly_reason: 'identity' })];
  const ref = (name: string): PresetRef => ({ section: 'process', index: 0, source: 'system', name });
  const deferred = () => {
    let resolve!: (v: { values: Record<string, SettingValue> }) => void;
    return { promise: new Promise<{ values: Record<string, SettingValue> }>((r) => (resolve = r)), resolve: (v: { values: Record<string, SettingValue> }) => resolve(v) };
  };
  const setup = () => {
    const page = { settings: WITH_DIFF, draft: {} as Draft, replace: vi.fn((d: Draft) => (page.draft = d)) };
    const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={createTestQueryClient()}>{children}</QueryClientProvider>;
    const hook = renderHook(() => usePresetSwitch(WITH_DIFF, () => page, vi.fn()), { wrapper });
    return { page, hook };
  };

  it('keeps an edit typed while the preview runs, and takes no other pick meanwhile', async () => {
    const first = deferred();
    const preview = vi.spyOn(workspaceApi, 'previewSwitch').mockReturnValueOnce(first.promise);
    const { page, hook } = setup();
    act(() => hook.result.current.pick(ref('0.12mm Fine @Test')));
    await waitFor(() => expect(hook.result.current.busy).toBe(true));
    act(() => hook.result.current.pick(ref('0.16mm Other @Test'))); // ignored: one at a time
    page.draft = { wall_loops: '4' }; // typed meanwhile
    await act(async () => first.resolve({ values: { layer_height: '0.12', print_settings_id: '0.12mm Fine @Test' } }));
    expect(preview).toHaveBeenCalledTimes(1);
    // the edit typed meanwhile is seen: the unsaved-changes dialog asks about it, and Transfer keeps it
    await waitFor(() => expect(hook.result.current.pending?.keys).toEqual(['wall_loops']));
    act(() => hook.result.current.commit(hook.result.current.pending!, true));
    expect(hook.result.current.switched.refs).toEqual([ref('0.12mm Fine @Test')]);
    expect(page.draft).toEqual({ wall_loops: '4' });
  });

  it('drops an answer that comes back after the switches were undone', async () => {
    const first = deferred();
    vi.spyOn(workspaceApi, 'previewSwitch').mockReturnValueOnce(first.promise);
    const { page, hook } = setup();
    act(() => hook.result.current.pick(ref('0.12mm Fine @Test')));
    await waitFor(() => expect(hook.result.current.busy).toBe(true));
    act(() => hook.result.current.reset()); // Undo all while it runs
    await act(async () => first.resolve({ values: { layer_height: '0.12' } }));
    await waitFor(() => expect(hook.result.current.busy).toBe(false));
    expect(hook.result.current.switched.refs).toEqual([]);
    expect(page.replace).not.toHaveBeenCalled();
  });
});
