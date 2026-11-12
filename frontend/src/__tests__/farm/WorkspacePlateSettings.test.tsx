/**
 * Printfarm (fork): 3MF Workspace, batch E4a. A plate's own settings, as OrcaSlicer's Plate Settings dialog and tab
 * set them: kept in the draft under ``plate:N`` and saved apart from the objects'; the dialog's values (Same as
 * Global, the filament orders, the other layers' ranges); spiral vase setting the plate's objects up for it; the
 * tab's own values and reset. Made-up data.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import type { WorkspaceDocument, WorkspaceObject, WorkspacePlate, WorkspaceSetting } from '../../farm/workspace/api';
import { ObjectsPanel } from '../../farm/workspace/ObjectsPanel';
import { ownToSave } from '../../farm/workspace/objects';
import type { OrcaLayout } from '../../farm/workspace/orcaLayout';
import { PlateSettingsDialog } from '../../farm/workspace/PlateSettings';
import { formatRanges, parseRanges, plateEdits, plateMeta, plateToSave, spiralEdits } from '../../farm/workspace/plates';

const setting = (key: string, type: WorkspaceSetting['type'], value: string, more: Partial<WorkspaceSetting> = {}): WorkspaceSetting => ({
  key,
  label: key,
  section: 'process',
  type,
  value,
  editable: true,
  readonly_reason: null,
  differs_from_system: null,
  overrides: [],
  ...more,
});
const BEDS = [
  { value: 'Cool Plate', label: 'Cool Plate' },
  { value: 'Textured PEI Plate', label: 'Textured PEI Plate' },
];
const SEQUENCES = [
  { value: 'by layer', label: 'By layer' },
  { value: 'by object', label: 'By object' },
];
const SETTINGS = [
  setting('curr_bed_type', 'enum', 'Textured PEI Plate', { options: BEDS }),
  setting('print_sequence', 'enum', 'by layer', { options: SEQUENCES }),
  setting('spiral_mode', 'bool', '0'),
  setting('wall_loops', 'int', '3'),
  setting('top_shell_layers', 'int', '0'),
  setting('sparse_infill_density', 'float', '15%', { percent: 'always' }),
  setting('enable_support', 'bool', '0'),
];
const LAYOUT: OrcaLayout = {
  tag: 'test',
  source: '',
  pages: { process: [], filament: [], printer: [] },
  options: {
    curr_bed_type: { type: 'coEnum', label: 'Bed type', mode: 'simple', enum: BEDS },
    print_sequence: { type: 'coEnum', label: 'Print sequence', mode: 'simple', enum: SEQUENCES },
    spiral_mode: { type: 'coBool', label: 'Spiral vase', mode: 'simple' },
    skirt_start_angle: { type: 'coFloat', label: 'Skirt start point', mode: 'advanced' },
  },
  it: {},
  object_keys: [],
  region_keys: [],
  plate_keys: {
    bed_type: 'curr_bed_type',
    skirt_start_angle: 'skirt_start_angle',
    print_sequence: 'print_sequence',
    spiral_mode: 'spiral_mode',
    first_layer_print_sequence: 'first_layer_print_sequence',
    other_layers_print_sequence: 'other_layers_print_sequence',
  },
};
const CUP: WorkspaceObject = {
  id: '2',
  name: 'Cup',
  extruder: '1',
  parts: [{ id: '1', name: 'Cup', type: 'normal_part' }],
  overrides: [{ key: 'enable_support', value: '1', part: null, part_id: null }],
};
const BOX: WorkspaceObject = { id: '4', name: 'Box', extruder: '1', parts: [{ id: '1', name: 'Box', type: 'normal_part' }], overrides: [] };
const PLATE: WorkspacePlate = {
  index: 1,
  name: 'Front',
  object_ids: ['2'],
  instances: [{ object_id: '2', instance_id: '0' }],
  metadata: {
    plater_id: '1',
    plater_name: 'Front',
    locked: 'false',
    print_sequence: 'by object',
    other_layers_print_sequence: '2 5 2 1 6 2147483646 1 2',
    other_layers_print_sequence_nums: '2',
  },
};
const OTHER: WorkspacePlate = { index: 2, name: null, object_ids: ['4'], instances: [{ object_id: '4', instance_id: '0' }], metadata: { plater_id: '2' } };
const DOC = { editable: true, plates: [PLATE, OTHER], objects: [CUP, BOX], settings: SETTINGS } as unknown as WorkspaceDocument;

describe("a plate's own settings", () => {
  it('reads them from the file and the edits, and keeps a value the file holds out of the edits', () => {
    expect(plateMeta(PLATE)).toMatchObject({ plater_name: 'Front', print_sequence: 'by object' });
    expect(plateMeta(PLATE, { 'plate:1': { bed_type: 'Cool Plate' } })).toMatchObject({ bed_type: 'Cool Plate' });
    expect(plateMeta(PLATE, { 'plate:1': { print_sequence: null } }).print_sequence).toBeUndefined();
    expect(plateEdits(PLATE, { plater_name: 'Front', print_sequence: null, spiral_mode: null, bed_type: 'Cool Plate' })).toEqual({
      'plate:1': { plater_name: undefined, print_sequence: null, spiral_mode: undefined, bed_type: 'Cool Plate' },
    });
  });

  it("saves the plates' edits apart from the objects'", () => {
    const edits = { 'plate:2': { spiral_mode: 'true' }, '4': { wall_loops: '1' } };
    expect(plateToSave(edits)).toEqual([{ plate: 2, key: 'spiral_mode', value: 'true' }]);
    expect(ownToSave(edits)).toEqual([{ object_id: '4', part_id: null, key: 'wall_loops', value: '1' }]);
  });

  it("reads and writes the other layers' ranges, in Orca's order", () => {
    const ranges = parseRanges('2 5 2 1 6 2147483646 1 2', 2, 2);
    expect(ranges).toEqual([
      { from: 2, to: 5, order: [2, 1] },
      { from: 6, to: 2147483646, order: [1, 2] },
    ]);
    expect(formatRanges([ranges[1], ranges[0]])).toBe('2 5 2 1 6 2147483646 1 2');
  });

  it("sets spiral vase's settings on the plate's objects where the project's or their own differ", () => {
    // Cup: walls 3 and 15% (the project's), its own support on; top shells already 0 in the project
    expect(spiralEdits(DOC, 1, {})).toEqual({ '2': { wall_loops: '1', sparse_infill_density: '0%', enable_support: '0' } });
    expect(spiralEdits(DOC, 3, {})).toEqual({}); // no objects there
  });
});

describe('the Plate Settings dialog', () => {
  it("opens with the plate's values and gives back the ones chosen", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<PlateSettingsDialog layout={LAYOUT} doc={DOC} plate={PLATE} colours={['#fff', '#000']} onApply={onApply} onClose={() => {}} />);
    expect(screen.getByRole('textbox', { name: 'Plate name' })).toHaveValue('Front');
    expect(screen.getByRole('combobox', { name: 'Print sequence' })).toHaveTextContent('By object');
    expect(screen.getByTestId('range-1')).toBeInTheDocument(); // the file's two ranges
    await user.clear(screen.getByRole('textbox', { name: 'Plate name' }));
    await user.type(screen.getByRole('textbox', { name: 'Plate name' }), 'Left');
    await user.click(screen.getByRole('combobox', { name: 'Bed type' }));
    await user.click(screen.getByRole('option', { name: 'Cool Plate' }));
    await user.click(screen.getByRole('combobox', { name: 'Print sequence' }));
    await user.click(screen.getByRole('option', { name: 'Same as Global Print Sequence' }));
    await user.click(screen.getByRole('combobox', { name: 'First layer filament sequence' }));
    await user.click(screen.getByRole('option', { name: 'Customize' }));
    fireEvent.keyDown(within(screen.getByRole('list', { name: 'First layer filament sequence' })).getAllByRole('listitem')[0], { key: 'ArrowRight' });
    await user.click(screen.getByRole('button', { name: '−' })); // the last range goes
    await user.click(screen.getByRole('button', { name: 'OK' }));
    expect(onApply).toHaveBeenCalledWith({
      plater_name: 'Left',
      bed_type: 'Cool Plate',
      print_sequence: null,
      spiral_mode: null,
      first_layer_print_sequence: '2 1',
      other_layers_print_sequence: '2 5 2 1',
    });
  });

  it('refuses a range that starts before layer 2', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<PlateSettingsDialog layout={LAYOUT} doc={DOC} plate={PLATE} colours={['#fff', '#000']} onApply={onApply} onClose={() => {}} />);
    await user.clear(screen.getByRole('textbox', { name: 'Layer 1' }));
    await user.type(screen.getByRole('textbox', { name: 'Layer 1' }), '1');
    await user.click(screen.getByRole('button', { name: 'OK' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Please input layer value (>= 2).');
    expect(onApply).not.toHaveBeenCalled();
  });
});

describe('the Plate Settings tab', () => {
  it("changes a plate's own value, resets it, and opens the dialog's sequences", async () => {
    const user = userEvent.setup();
    const apply = vi.fn();
    const customize = vi.fn();
    render(
      <ObjectsPanel
        layout={LAYOUT}
        doc={DOC}
        draft={{}}
        colours={['#fff', '#000']}
        stats={new Map()}
        selection={{ kind: 'plate', plate: 1 }}
        onSelect={() => {}}
        onMenu={() => {}}
        own={{ edits: {}, set: () => {}, setMany: () => {}, plate: { apply, customize } }}
      />,
    );
    await user.click(within(screen.getByTestId('plate-print_sequence')).getByRole('button', { name: /print sequence/i }));
    expect(apply).toHaveBeenLastCalledWith(PLATE, { print_sequence: null });
    await user.click(within(screen.getByTestId('plate-curr_bed_type')).getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: 'Cool Plate' }));
    expect(apply).toHaveBeenLastCalledWith(PLATE, { bed_type: 'Cool Plate' });
    await user.click(within(screen.getByTestId('plate-spiral_mode')).getByRole('checkbox'));
    expect(apply).toHaveBeenLastCalledWith(PLATE, { spiral_mode: 'true' });
    await user.click(within(screen.getByTestId('plate-first_layer_print_sequence')).getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: 'Customize' }));
    expect(customize).toHaveBeenCalledWith(PLATE);
    await user.click(within(screen.getByTestId('plate-other_layers_print_sequence')).getByRole('combobox'));
    await user.click(screen.getByRole('option', { name: 'Auto' }));
    expect(apply).toHaveBeenLastCalledWith(PLATE, { other_layers_print_sequence: null });
    expect(within(screen.getByTestId('plate-skirt_start_angle')).queryByRole('textbox')).toBeNull();
  });
});
