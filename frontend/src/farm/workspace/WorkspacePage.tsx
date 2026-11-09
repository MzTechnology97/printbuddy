// Fork (printfarm): 3MF Workspace, a full-screen page opened from the File Manager ("Edit 3MF").
// Open a project, change its settings, save a copy (the original is never written), slice the saved file with
// its own settings on the existing sidecars, preview the G-code and print through the existing print modal.
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Box, Code2, Cog, Loader2, Printer, Save, X } from 'lucide-react';
import { api } from '../../api/client';
import { Button } from '../../components/Button';
import { GcodeViewer } from '../../components/GcodeViewer';
import { ModelViewer } from '../../components/ModelViewer';
import { PrintModal } from '../../components/PrintModal';
import { useSliceJobTracker } from '../../contexts/SliceJobTrackerContext';
import { useToast } from '../../contexts/ToastContext';
import { formatDuration } from '../../utils/date';
import { farmProjectApi } from '../projectSliceApi';
import { type WorkspaceDocument, workspaceApi } from './api';
import { formatValue, useSettingsDraft } from './draft';
import { ProjectTree } from './ProjectTree';
import { SettingsPanel } from './SettingsPanel';
import '../i18n';

type Pane = 'plates' | 'view' | 'settings';

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
  if (docQuery.isLoading) {
    return (
      <div className="h-screen flex items-center justify-center gap-2 bg-bambu-dark text-bambu-gray">
        <Loader2 className="w-5 h-5 animate-spin" /> {t('farm.workspace.loading')}
      </div>
    );
  }
  if (!docQuery.data) {
    return (
      <div className="h-screen flex flex-col items-center justify-center gap-4 bg-bambu-dark text-white p-4 text-center">
        <AlertTriangle className="w-8 h-8 text-amber-400" />
        <p>{docQuery.error instanceof Error ? docQuery.error.message : String(docQuery.error ?? '')}</p>
        <Button variant="secondary" onClick={() => window.history.back()}>
          {t('farm.workspace.close')}
        </Button>
      </div>
    );
  }
  // keyed by file: opening the saved copy starts with no changes
  return <Workspace key={docQuery.data.file.id} doc={docQuery.data} />;
}

function Workspace({ doc }: { doc: WorkspaceDocument }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { trackJob } = useSliceJobTracker();
  const fileId = doc.file.id;
  const { draft, set, reset, resetAll, invalid, count } = useSettingsDraft(doc.settings);
  const [pane, setPane] = useState<Pane>('view');
  const [center, setCenter] = useState<'3d' | 'gcode'>('3d');
  const [selectedPlate, setSelectedPlate] = useState<number | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [jobId, setJobId] = useState<number | null>(null);
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
  const label = useMemo(() => new Map(doc.settings.map((s) => [s.key, s])), [doc.settings]);
  const bed = useMemo(() => bedOf(doc), [doc]);

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
    mutationFn: () => workspaceApi.saveCopy(fileId, doc.sha256, draft, saveName.trim() || undefined),
    onSuccess: (saved) => {
      showToast(t('farm.workspace.save.saved', { name: saved.filename }), 'success');
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      setSaveOpen(false);
      navigate(`/files/${saved.library_file_id}/workspace`);
    },
  });

  const slicePlate = selectedPlate ?? doc.plates[0]?.index ?? null;
  const sliceMutation = useMutation({
    mutationFn: () => farmProjectApi.slice(fileId, slicePlate),
    onSuccess: (job) => {
      trackJob(job.job_id, 'libraryFile', doc.file.filename); // the app-wide progress toasts
      setJobId(job.job_id);
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
    if (result) setCenter('gcode');
  }, [result]);

  const sliceable = doc.kind === 'project' && !!project?.sliceable;
  const slicing = sliceMutation.isPending || (jobId != null && !job) || job?.status === 'pending' || job?.status === 'running';
  const sliceTitle = count ? t('farm.workspace.slice.saveFirst') : !sliceable ? t('farm.workspace.slice.notSliceable') : undefined;
  const percent = job?.progress?.total_percent;

  const paneButton = (key: Pane) => (
    <button
      key={key}
      onClick={() => setPane(key)}
      aria-pressed={pane === key}
      className={`flex-1 py-2 text-sm ${pane === key ? 'text-white border-b-2 border-bambu-green' : 'text-bambu-gray'}`}
    >
      {t(`farm.workspace.panes.${key}`)}
    </button>
  );

  return (
    <div className="h-screen flex flex-col bg-bambu-dark text-white">
      {/* Header: project, printer and profile, save, slice, close */}
      <header className="flex flex-wrap items-center gap-2 px-3 py-2 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary">
        <div className="min-w-0 w-full sm:w-auto sm:flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <Box className="w-4 h-4 text-bambu-green flex-shrink-0" />
            <h1 className="text-base font-semibold truncate" title={doc.file.filename}>
              {doc.file.filename}
            </h1>
            <span className="text-xs px-1.5 py-0.5 rounded bg-bambu-dark-tertiary text-bambu-gray-light flex-shrink-0">
              {SLICER_NAMES[doc.slicer] ?? doc.slicer}
            </span>
          </div>
          <div className="text-xs text-bambu-gray truncate">
            {project
              ? [project.printer_preset ?? project.printer_model_raw, project.process_preset].filter(Boolean).join(' · ')
              : t('farm.workspace.title')}
            {doc.copy_of && ` · ${t('farm.workspace.copyOf', { name: doc.copy_of.copy_of_filename })}`}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {count > 0 && (
            <Button variant="ghost" size="sm" onClick={resetAll}>
              {t('farm.workspace.resetAll')}
            </Button>
          )}
          <Button size="sm" onClick={() => setSaveOpen(true)} disabled={!doc.editable || count === 0}>
            <Save className="w-4 h-4" />
            {t('farm.workspace.save.button')}
            {count > 0 && ` (${count})`}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => sliceMutation.mutate()}
            disabled={!sliceable || count > 0 || slicing}
            title={sliceTitle}
          >
            {slicing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Cog className="w-4 h-4" />}
            {t('farm.workspace.slice.button')}
            {slicePlate != null && doc.plates.length > 1 && ` · ${t('farm.workspace.plate', { index: slicePlate })}`}
          </Button>
          <Button variant="ghost" size="sm" onClick={close} aria-label={t('farm.workspace.close')}>
            <X className="w-4 h-4" />
          </Button>
        </div>
      </header>

      {(doc.kind !== 'project' || doc.warnings.length > 0 || doc.file.watched) && (
        <div className="px-3 py-2 text-xs border-b border-bambu-dark-tertiary space-y-0.5">
          {doc.kind !== 'project' && <p className="text-amber-400">{t(`farm.workspace.kind.${doc.kind}`)}</p>}
          {doc.warnings.length > 0 && (
            <p className="text-amber-400">
              {t('farm.workspace.warningsReadOnly')} {doc.warnings.join('; ')}
            </p>
          )}
          {doc.file.watched && <p className="text-bambu-gray">{t('farm.workspace.watched')}</p>}
        </div>
      )}
      {(slicing || job?.status === 'failed' || result || sliceMutation.isError) && (
        <div className="px-3 py-2 text-sm border-b border-bambu-dark-tertiary flex flex-wrap items-center gap-3" role="status">
          {slicing && (
            <span className="text-bambu-gray-light">
              {t('farm.workspace.slice.running')} {percent != null ? `${Math.round(percent)}%` : ''}
            </span>
          )}
          {(job?.status === 'failed' || sliceMutation.isError) && (
            <span className="text-red-400">
              {t('farm.workspace.slice.failed', {
                error: job?.error_detail ?? (sliceMutation.error instanceof Error ? sliceMutation.error.message : ''),
              })}
            </span>
          )}
          {result && (
            <>
              <span className="text-bambu-green">
                {t('farm.workspace.slice.done', {
                  time: formatDuration(result.print_time_seconds),
                  grams: result.filament_used_g.toFixed(1),
                })}
              </span>
              <Button size="sm" onClick={() => setPrintOpen(true)}>
                <Printer className="w-4 h-4" />
                {t('farm.workspace.slice.print')}
              </Button>
            </>
          )}
        </div>
      )}

      <nav className="flex lg:hidden border-b border-bambu-dark-tertiary">
        {(['plates', 'view', 'settings'] as Pane[]).map(paneButton)}
      </nav>
      <div className="flex-1 min-h-0 flex">
        <aside
          className={`${pane === 'plates' ? 'flex' : 'hidden'} lg:flex flex-col w-full lg:w-64 flex-shrink-0 border-r border-bambu-dark-tertiary bg-bambu-dark-secondary`}
        >
          <ProjectTree
            fileId={fileId}
            plates={doc.plates}
            objects={doc.objects}
            filamentColours={colours}
            selectedPlate={selectedPlate}
            onSelectPlate={setSelectedPlate}
          />
        </aside>
        <main className={`${pane === 'view' ? 'flex' : 'hidden'} lg:flex flex-col flex-1 min-w-0`}>
          <div className="flex gap-1 p-2">
            <Button variant={center === '3d' ? 'primary' : 'ghost'} size="sm" onClick={() => setCenter('3d')}>
              <Box className="w-4 h-4" /> {t('farm.workspace.view3d')}
            </Button>
            {result && (
              <Button variant={center === 'gcode' ? 'primary' : 'ghost'} size="sm" onClick={() => setCenter('gcode')}>
                <Code2 className="w-4 h-4" /> {t('farm.workspace.gcode')}
              </Button>
            )}
          </div>
          <div className="flex-1 min-h-0">
            {center === 'gcode' && result ? (
              <GcodeViewer
                gcodeUrl={api.getLibraryFileGcodeUrl(result.library_file_id)}
                buildVolume={bed}
                filamentColors={colours}
                className="w-full h-full"
              />
            ) : (
              <ModelViewer
                url={api.getLibraryFileDownloadUrl(fileId)}
                fileType="3mf"
                buildVolume={bed}
                filamentColors={colours}
                selectedPlateId={selectedPlate}
                className="w-full h-full"
              />
            )}
          </div>
        </main>
        <aside
          className={`${pane === 'settings' ? 'flex' : 'hidden'} lg:flex flex-col w-full lg:w-[26rem] flex-shrink-0 border-l border-bambu-dark-tertiary bg-bambu-dark-secondary min-h-0`}
        >
          <SettingsPanel
            settings={doc.settings}
            tabs={doc.tabs}
            draft={draft}
            editable={doc.editable}
            onChange={set}
            onReset={reset}
          />
        </aside>
      </div>

      {saveOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="ws-save-title">
          <div className="w-full max-w-lg max-h-[90vh] flex flex-col rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary">
            <div className="p-4 border-b border-bambu-dark-tertiary">
              <h2 id="ws-save-title" className="text-lg font-semibold">
                {t('farm.workspace.save.title')}
              </h2>
              <p className="text-sm text-bambu-gray mt-1">{t('farm.workspace.save.intro')}</p>
            </div>
            <ul className="p-4 space-y-1 overflow-y-auto text-sm">
              {Object.entries(draft).map(([key, value]) => {
                const s = label.get(key)!;
                return (
                  <li key={key} className={invalid.includes(key) ? 'text-red-400' : ''}>
                    <span className="text-white">{s.label}</span>{' '}
                    <span className="text-bambu-gray">
                      {formatValue(s, s.value, t('farm.workspace.inherit'))} → {formatValue(s, value, t('farm.workspace.inherit'))}
                    </span>
                  </li>
                );
              })}
            </ul>
            <div className="p-4 border-t border-bambu-dark-tertiary space-y-3">
              <label className="block text-sm">
                <span className="text-bambu-gray">{t('farm.workspace.save.name')}</span>
                <input
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  placeholder={t('farm.workspace.save.namePlaceholder', { name: doc.file.filename.replace(/\.3mf$/i, '') })}
                  className="mt-1 w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1.5 text-white"
                />
              </label>
              {invalid.length > 0 && <p className="text-sm text-red-400">{t('farm.workspace.save.invalid')}</p>}
              {saveMutation.isError && (
                <p className="text-sm text-red-400" role="alert">
                  {saveMutation.error instanceof Error ? saveMutation.error.message : String(saveMutation.error)}
                </p>
              )}
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => setSaveOpen(false)} disabled={saveMutation.isPending}>
                  {t('farm.workspace.close')}
                </Button>
                <Button onClick={() => saveMutation.mutate()} disabled={invalid.length > 0 || saveMutation.isPending}>
                  {saveMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  {saveMutation.isPending ? t('farm.workspace.save.saving') : t('farm.workspace.save.confirm')}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {printOpen && result && (
        <PrintModal
          mode="reprint"
          libraryFileId={result.library_file_id}
          archiveName={result.name}
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
