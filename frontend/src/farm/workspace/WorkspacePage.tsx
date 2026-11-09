// Fork (printfarm): 3MF Workspace, a full-screen page opened from the File Manager ("Edit 3MF"), laid out as the
// slicer clients: Prepare (sidebar with printer, filaments, objects and process settings; 3D plate view) and
// Preview (the sliced G-code). Settings are saved as a copy (the original is never written), sliced with its own
// settings on the existing sidecars and printed through the existing print modal.
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Box, Cog, Download, Loader2, Printer, Save, X } from 'lucide-react';
import { api } from '../../api/client';
import { Button } from '../../components/Button';
import { ModelViewer } from '../../components/ModelViewer';
import { PrintModal } from '../../components/PrintModal';
import { useSliceJobTracker } from '../../contexts/SliceJobTrackerContext';
import { useToast } from '../../contexts/ToastContext';
import { farmProjectApi } from '../projectSliceApi';
import { type WorkspaceDocument, workspaceApi } from './api';
import { PresetDialog, SaveCopyDialog } from './Dialogs';
import { useSettingsDraft } from './draft';
import { type OrcaLayout, loadOrcaLayout } from './orcaLayout';
import { PreviewPane } from './PreviewPane';
import { Sidebar } from './Sidebar';
import { GizmoToolbar, PlateBar } from './Viewport';
import '../i18n';

type Mode = 'prepare' | 'preview';
type Pane = 'settings' | 'view';

/** The printer's bed from the project (``printable_area`` corners, ``printable_height``); undefined when unreadable. */
function bedOf(doc: WorkspaceDocument): { x: number; y: number; z: number } | undefined {
  const get = (key: string) => doc.settings.find((s) => s.key === key)?.value;
  const area = get('printable_area');
  const height = get('printable_height');
  const corners = (Array.isArray(area) ? area : []).map((c) => c.split('x').map(Number));
  const z = Number(Array.isArray(height) ? height[0] : height);
  if (!corners.length || corners.some((c) => c.length !== 2 || c.some((n) => !Number.isFinite(n))) || !(z > 0)) return undefined;
  return { x: Math.max(...corners.map((c) => c[0])), y: Math.max(...corners.map((c) => c[1])), z };
}
const SLICER_NAMES: Record<string, string> = { orcaslicer: 'OrcaSlicer', bambu_studio: 'Bambu Studio' };
// OrcaSlicer's dark theme (src/slic3r/GUI/Widgets/StateColor.cpp, v2.5.0), on Printbuddy's colour tokens
const SLICER_DARK = {
  '--color-bambu-dark': '#242428',
  '--color-bambu-dark-secondary': '#2D2D31',
  '--color-bambu-dark-tertiary': '#3E3E45',
  '--color-bambu-gray': '#909090',
  '--color-bambu-gray-light': '#B2B3B5',
  '--color-bambu-green': '#009688',
  '--color-bambu-green-light': '#26A69A',
  '--color-bambu-green-dark': '#00675B',
} as React.CSSProperties;

export function WorkspacePage() {
  const { id } = useParams();
  const fileId = Number(id);
  const { t } = useTranslation();
  const docQuery = useQuery({
    queryKey: ['farmWorkspace', fileId],
    queryFn: () => workspaceApi.get(fileId),
    enabled: Number.isFinite(fileId),
    retry: false,
  });
  const layoutQuery = useQuery({ queryKey: ['orcaLayout'], queryFn: loadOrcaLayout, staleTime: Infinity });
  if (docQuery.isLoading || layoutQuery.isLoading) {
    return (
      <div className="h-screen flex items-center justify-center gap-2 bg-bambu-dark text-bambu-gray">
        <Loader2 className="w-5 h-5 animate-spin" /> {t('farm.workspace.loading')}
      </div>
    );
  }
  if (!docQuery.data || layoutQuery.isError) {
    const error = docQuery.error ?? layoutQuery.error;
    return (
      <div className="h-screen flex flex-col items-center justify-center gap-4 bg-bambu-dark text-white p-4 text-center">
        <AlertTriangle className="w-8 h-8 text-amber-400" />
        <p>{error instanceof Error ? error.message : String(error ?? '')}</p>
        <div className="flex gap-2">
          {/* a page part missing after an update (old tab): reloading gets the current one */}
          {layoutQuery.isError && <Button onClick={() => window.location.reload()}>{t('common.retry')}</Button>}
          <Button variant="secondary" onClick={() => window.history.back()}>
            {t('farm.workspace.close')}
          </Button>
        </div>
      </div>
    );
  }
  if (!layoutQuery.data) return null; // a static asset of this build: it is there
  // keyed by file: opening the saved copy starts with no changes
  return <Workspace key={docQuery.data.file.id} doc={docQuery.data} layout={layoutQuery.data} />;
}

function Workspace({ doc, layout }: { doc: WorkspaceDocument; layout: OrcaLayout }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { trackJob } = useSliceJobTracker();
  const fileId = doc.file.id;
  const { draft, set, reset, resetAll, invalid, count } = useSettingsDraft(doc.settings);
  const [mode, setMode] = useState<Mode>('prepare');
  const [pane, setPane] = useState<Pane>('view');
  const [selectedPlate, setSelectedPlate] = useState<number | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [preset, setPreset] = useState<'printer' | 'filament' | null>(null);
  const [sliceJob, setSliceJob] = useState<{ id: number; plate: number | null } | null>(null);
  const jobId = sliceJob?.id ?? null;
  const [printOpen, setPrintOpen] = useState(false);

  const projectQuery = useQuery({
    queryKey: ['farmProjectSettings', fileId],
    queryFn: () => farmProjectApi.get(fileId),
    enabled: doc.kind === 'project',
    retry: false,
  });
  const project = projectQuery.data;
  const colours = useMemo(() => {
    const value = doc.settings.find((s) => s.key === 'filament_colour')?.value;
    return Array.isArray(value) ? value : value ? [value] : [];
  }, [doc.settings]);
  const byKey = useMemo(() => new Map(doc.settings.map((s) => [s.key, s])), [doc.settings]);
  const bed = useMemo(() => bedOf(doc), [doc]);
  const presets = {
    printer: project?.printer_preset ?? (byKey.get('printer_settings_id')?.value as string | undefined) ?? null,
    process: project?.process_preset ?? (byKey.get('print_settings_id')?.value as string | undefined) ?? null,
  };

  useEffect(() => {
    if (!count) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [count]);

  const close = () => {
    if (count && !window.confirm(t('farm.workspace.unsavedConfirm'))) return;
    navigate('/files');
  };

  const saveMutation = useMutation({
    mutationFn: (name: string) => workspaceApi.saveCopy(fileId, doc.sha256, draft, name || undefined),
    onSuccess: (saved) => {
      showToast(t('farm.workspace.save.saved', { name: saved.filename }), 'success');
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      setSaveOpen(false);
      navigate(`/files/${saved.library_file_id}/workspace`);
    },
  });

  const plate = selectedPlate ?? doc.plates[0]?.index ?? null;
  const sliceMutation = useMutation({
    mutationFn: () => farmProjectApi.slice(fileId, plate),
    onSuccess: (job) => {
      trackJob(job.job_id, 'libraryFile', doc.file.filename); // the app-wide progress toasts
      setSliceJob({ id: job.job_id, plate });
      setMode('preview');
    },
  });
  const jobQuery = useQuery({
    queryKey: ['sliceJob', jobId],
    queryFn: () => api.getSliceJob(jobId!),
    enabled: jobId != null,
    refetchInterval: (q) => (q.state.data?.status === 'completed' || q.state.data?.status === 'failed' ? false : 1500),
  });
  const job = jobQuery.data;
  const result = job?.status === 'completed' && job.result && 'library_file_id' in job.result ? job.result : null;
  useEffect(() => {
    if (result) queryClient.invalidateQueries({ queryKey: ['farmWorkspace', fileId] }); // its outputs
  }, [result, queryClient, fileId]);

  // The plate's slice: just made here, else the latest one kept in the library.
  const sliced = useMemo(() => new Set(doc.outputs.map((o) => o.plate)), [doc.outputs]);
  const kept = doc.outputs.find((o) => o.plate === (plate ?? 1));
  const output =
    result && sliceJob?.plate === plate ? { library_file_id: result.library_file_id, current: true } : (kept ?? null);

  const sliceable = doc.kind === 'project' && !!project?.sliceable;
  const slicing = sliceMutation.isPending || (jobId != null && !job) || job?.status === 'pending' || job?.status === 'running';
  const canSlice = sliceable && count === 0 && !slicing;
  const sliceTitle = count ? t('farm.workspace.slice.saveFirst') : !sliceable ? t('farm.workspace.slice.notSliceable') : undefined;
  const percent = job?.progress?.total_percent;
  const exportFile = () =>
    mode === 'preview' && output
      ? api.downloadLibraryFile(output.library_file_id)
      : api.downloadLibraryFile(fileId, doc.file.filename);

  return (
    <div className="h-screen flex flex-col bg-bambu-dark text-white" style={SLICER_DARK}>
      {/* Top bar, as the clients: the file, Prepare / Preview, then save, slice, print, export */}
      <header className="flex flex-wrap items-center gap-2 px-3 py-2 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary">
        <div className="flex items-center gap-2 min-w-0 w-full sm:w-auto sm:flex-1">
          <Button variant="ghost" size="sm" onClick={close} aria-label={t('farm.workspace.close')}>
            <X className="w-4 h-4" />
          </Button>
          <Box className="w-4 h-4 text-bambu-green flex-shrink-0" />
          <div className="min-w-0">
            <h1 className="text-sm font-semibold truncate" title={doc.file.filename}>
              {doc.file.filename}
            </h1>
            <p className="text-[11px] text-bambu-gray truncate">
              {SLICER_NAMES[doc.slicer] ?? doc.slicer}
              {doc.copy_of && ` · ${t('farm.workspace.copyOf', { name: doc.copy_of.copy_of_filename })}`}
            </p>
          </div>
        </div>
        <div className="flex rounded-lg bg-bambu-dark p-0.5" role="tablist" aria-label={t('farm.workspace.title')}>
          {(['prepare', 'preview'] as Mode[]).map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={mode === m}
              onClick={() => setMode(m)}
              className={`px-4 py-1.5 rounded-md text-sm ${mode === m ? 'bg-bambu-green text-white' : 'text-bambu-gray-light hover:text-white'}`}
            >
              {t(`farm.workspace.${m}`)}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 flex-wrap sm:flex-1 sm:justify-end">
          {count > 0 && (
            <Button variant="ghost" size="sm" onClick={resetAll}>
              {t('farm.workspace.resetAll')}
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={() => setSaveOpen(true)} disabled={!doc.editable || count === 0}>
            <Save className="w-4 h-4" />
            {t('farm.workspace.save.button')}
            {count > 0 && ` (${count})`}
          </Button>
          <Button size="sm" onClick={() => sliceMutation.mutate()} disabled={!canSlice} title={sliceTitle}>
            {slicing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Cog className="w-4 h-4" />}
            {slicing && percent != null ? `${Math.round(percent)}%` : t('farm.workspace.slicePlate')}
            {plate != null && doc.plates.length > 1 && !slicing && ` ${plate}`}
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setPrintOpen(true)} disabled={!output?.current}>
            <Printer className="w-4 h-4" />
            {t('farm.workspace.slice.print')}
          </Button>
          <Button variant="ghost" size="sm" onClick={exportFile} title={t(`farm.workspace.export.${mode}`)}>
            <Download className="w-4 h-4" />
            {t('farm.workspace.export.button')}
          </Button>
        </div>
      </header>

      {(doc.kind !== 'project' || doc.warnings.length > 0 || doc.file.watched || job?.status === 'failed' || sliceMutation.isError) && (
        <div className="px-3 py-1.5 text-xs border-b border-bambu-dark-tertiary space-y-0.5" role="status">
          {doc.kind !== 'project' && <p className="text-amber-400">{t(`farm.workspace.kind.${doc.kind}`)}</p>}
          {doc.warnings.length > 0 && (
            <p className="text-amber-400">
              {t('farm.workspace.warningsReadOnly')} {doc.warnings.join('; ')}
            </p>
          )}
          {doc.file.watched && <p className="text-bambu-gray">{t('farm.workspace.watched')}</p>}
          {(job?.status === 'failed' || sliceMutation.isError) && (
            <p className="text-red-400">
              {t('farm.workspace.slice.failed', {
                error: job?.error_detail ?? (sliceMutation.error instanceof Error ? sliceMutation.error.message : ''),
              })}
            </p>
          )}
        </div>
      )}

      <nav className="flex lg:hidden border-b border-bambu-dark-tertiary">
        {(['settings', 'view'] as Pane[]).map((key) => (
          <button
            key={key}
            onClick={() => setPane(key)}
            aria-pressed={pane === key}
            className={`flex-1 py-2 text-sm ${pane === key ? 'text-white border-b-2 border-bambu-green' : 'text-bambu-gray'}`}
          >
            {t(`farm.workspace.panes.${key}`)}
          </button>
        ))}
      </nav>
      <div className="flex-1 min-h-0 flex">
        <aside
          className={`${pane === 'settings' ? 'flex' : 'hidden'} lg:flex flex-col w-full lg:w-[24rem] flex-shrink-0 border-r border-bambu-dark-tertiary bg-bambu-dark-secondary min-h-0`}
        >
          <Sidebar
            layout={layout}
            doc={doc}
            presets={presets}
            selectedPlate={selectedPlate}
            draft={draft}
            onChange={set}
            onReset={reset}
            onEditPreset={setPreset}
          />
        </aside>
        <main className={`${pane === 'view' ? 'flex' : 'hidden'} lg:flex flex-col flex-1 min-w-0 relative`}>
          {mode === 'prepare' ? (
            <div className="relative flex-1 min-h-0">
              <ModelViewer
                url={api.getLibraryFileDownloadUrl(fileId)}
                fileType="3mf"
                buildVolume={bed}
                filamentColors={colours}
                selectedPlateId={selectedPlate}
                className="w-full h-full"
              />
              <GizmoToolbar />
              <PlateBar fileId={fileId} plates={doc.plates} selected={selectedPlate} onSelect={setSelectedPlate} />
            </div>
          ) : (
            <div className="relative flex-1 min-h-0">
              <PreviewPane
                plate={plate}
                output={output}
                bed={bed}
                colours={colours}
                canSlice={canSlice}
                onSlice={() => sliceMutation.mutate()}
              />
              {doc.plates.length > 1 && (
                <PlateBar fileId={fileId} plates={doc.plates} selected={plate} onSelect={setSelectedPlate} sliced={sliced} />
              )}
            </div>
          )}
        </main>
      </div>

      {saveOpen && (
        <SaveCopyDialog
          filename={doc.file.filename}
          draft={draft}
          settings={byKey}
          invalid={invalid}
          saving={saveMutation.isPending}
          error={saveMutation.isError ? saveMutation.error : null}
          onSave={(name) => saveMutation.mutate(name)}
          onClose={() => setSaveOpen(false)}
        />
      )}
      {preset && (
        <PresetDialog
          layout={layout}
          kind={preset}
          name={preset === 'printer' ? presets.printer : null}
          settings={doc.settings}
          draft={draft}
          editable={doc.editable}
          onChange={set}
          onReset={reset}
          onClose={() => setPreset(null)}
        />
      )}
      {printOpen && output?.current && (
        <PrintModal
          mode="reprint"
          libraryFileId={output.library_file_id}
          archiveName={doc.file.filename}
          onClose={() => setPrintOpen(false)}
          onSuccess={() => {
            setPrintOpen(false);
            queryClient.invalidateQueries({ queryKey: ['queue'] });
          }}
        />
      )}
    </div>
  );
}
