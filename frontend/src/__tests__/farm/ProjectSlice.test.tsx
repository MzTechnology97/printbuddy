/**
 * Printfarm (fork): the SliceModal slices a library 3MF with its own project settings
 * by default, and pre-picks presets that match the 3MF when presets are chosen instead.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SliceModal } from '../../components/SliceModal';
import { SliceJobTrackerProvider } from '../../contexts/SliceJobTrackerContext';
import { api, type UnifiedPresetsResponse } from '../../api/client';
import { farmProjectApi, pickProjectPrinter, type ProjectSettings } from '../../farm/projectSliceApi';

vi.mock('../../api/client', () => ({
  api: {
    getSlicerPresets: vi.fn(),
    sliceLibraryFile: vi.fn(),
    getSliceJob: vi.fn(),
    getLibraryFilePlates: vi.fn(),
    getLibraryFileFilamentRequirements: vi.fn(),
    listSlicerBundles: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../../farm/projectSliceApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../farm/projectSliceApi')>();
  return { ...actual, farmProjectApi: { get: vi.fn(), slice: vi.fn() } };
});

const mockApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const mockFarm = farmProjectApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const PROJECT: ProjectSettings = {
  slicer: 'orcaslicer',
  sliceable: true,
  sidecar_url: 'http://orca.test:3003',
  printer_model: 'K2 Pro',
  printer_model_raw: 'Creality K2 Pro',
  printer_preset: 'Creality K2 Pro 0.4 nozzle (custom)',
  process_preset: '0.20mm Standard @Creality K2 Pro',
  nozzle_diameter: '0.4',
  filaments: [{ index: 1, preset: 'Generic PETG-CF @K2-all', type: 'PETG-CF', color: '#000000' }],
};

// The first entries are unrelated to the 3MF: they used to be picked by default.
const PRESETS: UnifiedPresetsResponse = {
  orca_cloud: { printer: [], process: [], filament: [] },
  cloud: { printer: [], process: [], filament: [] },
  local: {
    printer: [
      { id: 'p1s', name: 'Bambu Lab P1S 0.4 nozzle - zoffset', source: 'local' },
      { id: 'k2-06', name: 'Creality K2 Pro 0.6 nozzle', source: 'local' },
      { id: 'k2-04', name: 'Creality K2 Pro 0.4 nozzle', source: 'local' },
    ],
    process: [{ id: 'proc', name: '0.20mm Standard @Creality K2 Pro', source: 'local' }],
    filament: [
      { id: 'abs', name: 'Bambulab ABS @K2', source: 'local', filament_type: 'ABS' },
      { id: 'petg-cf', name: 'Generic PETG-CF @K2-all', source: 'local', filament_type: 'PETG-CF' },
    ],
  },
  standard: { printer: [], process: [], filament: [] },
  cloud_status: 'ok',
  orca_cloud_status: 'ok',
};

function renderModal() {
  return render(
    <SliceJobTrackerProvider>
      <SliceModal source={{ kind: 'libraryFile', id: 7, filename: 'K2-Pro-handle.3mf' }} onClose={vi.fn()} />
    </SliceJobTrackerProvider>,
  );
}

describe('SliceModal with project settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFarm.get.mockResolvedValue(PROJECT);
    mockFarm.slice.mockResolvedValue({ job_id: 9, status: 'pending', status_url: '/api/v1/slice-jobs/9' });
    mockApi.getSlicerPresets.mockResolvedValue(PRESETS);
    mockApi.getSliceJob.mockResolvedValue({ job_id: 9, status: 'running' });
    mockApi.getLibraryFilePlates.mockResolvedValue({ file_id: 7, filename: 'x', plates: [], is_multi_plate: false });
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 7,
      filename: 'x',
      plate_id: 1,
      filaments: [{ slot_id: 1, type: 'PETG-CF', color: '#000000', used_grams: 0, used_meters: 0 }],
    });
    mockApi.listSlicerBundles.mockResolvedValue([]);
  });

  it('defaults to the project settings and slices through the farm route', async () => {
    const user = userEvent.setup();
    renderModal();

    expect(await screen.findByText(/OrcaSlicer · http:\/\/orca\.test:3003/)).toBeInTheDocument();
    expect(screen.getByText('0.20mm Standard @Creality K2 Pro')).toBeInTheDocument();
    expect(screen.getByText(/Generic PETG-CF @K2-all/)).toBeInTheDocument();
    expect((screen.getByLabelText('Project settings (3MF)') as HTMLInputElement).checked).toBe(true);
    // No preset dropdowns and no preview slice in this mode.
    expect(screen.queryAllByRole('combobox')).toHaveLength(0);
    expect(mockApi.getLibraryFileFilamentRequirements).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /^Slice$/ }));
    await waitFor(() => expect(mockFarm.slice).toHaveBeenCalledWith(7, null));
    expect(mockApi.sliceLibraryFile).not.toHaveBeenCalled();
  });

  it('pre-picks the 3MF printer and filament when presets are chosen', async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(await screen.findByLabelText('Choose presets'));
    await waitFor(() => expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(4));
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    await waitFor(() => expect(selects[0].value).toBe('local:k2-04')); // same model and nozzle, not the first entry
    expect(selects[1].value).toBe('local:proc');
    await waitFor(() => expect(selects[3].value).toBe('local:petg-cf')); // the 3MF's filament, not ABS
  });
});

describe('pickProjectPrinter', () => {
  it('needs the printer model', () => {
    expect(pickProjectPrinter(PRESETS, { ...PROJECT, printer_model_raw: null })).toBeNull();
    expect(pickProjectPrinter(PRESETS, { ...PROJECT, nozzle_diameter: '0.6' })).toEqual({
      source: 'local',
      id: 'k2-06',
    });
  });
});
