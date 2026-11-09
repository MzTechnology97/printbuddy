// Fork (printfarm): 3MF Workspace, a full-screen page opened from the File Manager ("Edit 3MF"), laid out as the
// slicer clients: Prepare (sidebar with printer, filaments, objects and process settings; 3D plate view) and
// Preview (the sliced G-code). Settings are saved as a copy (the original is never written), sliced with its own
// settings on the existing sidecars and printed through the existing print modal. The 3D view selects, zooms,
// measures and shows object info as OrcaSlicer's; its menus and tools that would change the project are phase 2.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type * as THREE from 'three';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Box, Cog, Download, Loader2, Printer, Save, X } from 'lucide-react';
import { api } from '../../api/client';
import { Button } from '../../components/Button';
import { ModelViewer, type ViewerScene } from '../../components/ModelViewer';
import { PrintModal } from '../../components/PrintModal';
import { useSliceJobTracker } from '../../contexts/SliceJobTrackerContext';
import { useToast } from '../../contexts/ToastContext';
import { farmProjectApi } from '../projectSliceApi';
import { type WorkspaceDocument, workspaceApi } from './api';
import { PresetDialog, SaveCopyDialog } from './Dialogs';
import { toLocaleDecimal, useSettingsDraft } from './draft';
import { objectMenu, plateMenu } from './menus';
import type { Selection } from './objects';
import { type OrcaLayout, loadOrcaLayout, orcaText } from './orcaLayout';
import { PreviewPane } from './PreviewPane';
import { Sidebar } from './Sidebar';
import {
  CanvasButtons,
  ContextMenu,
  MainToolbar,
  type MenuItem,
  ObjectInfo,
  PlateBar,
  PlateButtons,
  VIEW_KEYS,
} from './Viewport';
import {
  type ObjectStats,
  drawMeasure,
  fitTo,
  highlight,
  objectStats,
  pick,
  setView,
  unmirror,
  useCanvasClicks,
} from './viewTools';
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
  const { t, i18n } = useTranslation();
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
  const [preset, setPreset] = useState<{ kind: 'printer' | 'filament'; filament: number } | null>(null);
  const [perObject, setPerObject] = useState(false);
  const [selection, setSelection] = useState<Selection>(null);
  const [menu, setMenu] = useState<{ at: { x: number; y: number }; items: MenuItem[] } | null>(null);
  const [view, setViewScene] = useState<ViewerScene | null>(null);
  const [stats, setStats] = useState<Map<string, ObjectStats>>(new Map());
  const [grid, setGrid] = useState(true);
  const [tool, setTool] = useState<string | null>(null);
  const [measure, setMeasure] = useState<THREE.Vector3[]>([]);
  const [infoClosed, setInfoClosed] = useState(false);
  const zoomAfterBuild = useRef<string | null>(null);
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

  // --- 3D view: selection, menus, tools (they only look; see Viewport.tsx) ---
  const tr = useCallback((text: string) => orcaText(layout, text, i18n.language), [layout, i18n.language]);
  const onScene = useCallback((scene: ViewerScene) => {
    unmirror(scene);
    // the whole bed, as the slicer opens a plate (or the object a menu asked to zoom to)
    if (zoomAfterBuild.current) fitTo(scene, zoomAfterBuild.current);
    else setView(scene, 'iso');
    zoomAfterBuild.current = null;
    setViewScene(scene);
    setStats(objectStats(scene));
    setMeasure([]);
  }, []);
  const selectedId = selection?.kind === 'object' ? selection.id : null;
  useEffect(() => {
    if (view) highlight(view, selectedId);
  }, [view, selectedId]);
  useEffect(() => {
    if (view?.grid) view.grid.visible = grid;
  }, [view, grid]);
  useEffect(() => {
    if (view) drawMeasure(view, measure[0] ?? null, measure[1] ?? null);
  }, [view, measure]);

  const plateOf = (objectId: string) => doc.plates.find((p) => p.object_ids.includes(objectId))?.index ?? null;
  const select = (sel: Selection) => {
    setSelection(sel);
    setInfoClosed(false);
    const plate = sel?.kind === 'plate' ? sel.plate : sel?.kind === 'object' ? (sel.plate ?? plateOf(sel.id)) : null;
    // the 3D view follows to the plate when it shows one plate
    if (plate != null && (sel?.kind === 'plate' || (selectedPlate != null && selectedPlate !== plate))) setSelectedPlate(plate);
  };
  const zoomTo = (objectId: string) => {
    if (!view) return;
    if (stats.has(objectId)) fitTo(view, objectId);
    else {
      zoomAfterBuild.current = objectId; // on another plate: zoom once that plate is built
      setSelectedPlate(plateOf(objectId));
    }
  };
  const currentPlate = selectedPlate ?? doc.plates[0]?.index ?? null;
  const openMenu = (at: { x: number; y: number }, sel: Exclude<Selection, null>) => {
    if (sel.kind === 'plate') {
      const plateSel = { kind: 'plate' as const, plate: sel.plate };
      setMenu({
        at,
        items: plateMenu(tr, {
          selectAll: () => select(plateSel),
          settings: () => {
            setPerObject(true);
            select(plateSel);
          },
        }),
      });
      return;
    }
    const objSel = { kind: 'object' as const, id: sel.id, plate: sel.plate };
    setMenu({
      at,
      items: objectMenu(tr, t('farm.workspace.deleteKey'), stats.get(sel.id)?.printable ?? true, {
        select: () => select(objSel),
        zoom: () => {
          select(objSel);
          zoomTo(sel.id);
        },
        info: () => select(objSel),
        settings: () => {
          setPerObject(true);
          select(sel);
        },
      }),
    });
  };
  useCanvasClicks(
    view,
    (e) => {
      const hit = view && pick(view, e.clientX, e.clientY);
      if (tool === 'measure') {
        if (hit) setMeasure((m) => (m.length === 1 ? [m[0], hit.point] : [hit.point]));
        return;
      }
      if (hit) {
        select({ kind: 'object', id: hit.objectId, plate: selectedPlate });
        setPerObject(true);
      } else setSelection(null);
    },
    (e) => {
      const hit = view && pick(view, e.clientX, e.clientY);
      const at = { x: e.clientX, y: e.clientY };
      if (hit) {
        const sel = { kind: 'object' as const, id: hit.objectId, plate: selectedPlate };
        select(sel);
        openMenu(at, sel);
      } else if (currentPlate != null) openMenu(at, { kind: 'plate', plate: currentPlate });
    },
  );
  const toggleTool = (key: string) => {
    setTool((cur) => (cur === key ? null : key));
    setMeasure([]);
  };
  // OrcaSlicer's keys in the 3D view: 0-6 views, Z zoom to the selection, U measure, Esc leaves a tool / selection
  const dialogOpen = saveOpen || !!preset || !!menu || printOpen;
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    const el = e.target as HTMLElement | null;
    if (mode !== 'prepare' || !view || dialogOpen || e.ctrlKey || e.metaKey || e.altKey) return;
    if (el?.closest?.('input, textarea, select, [role="listbox"], [role="menu"], [role="combobox"]')) return;
    if (e.key in VIEW_KEYS) setView(view, VIEW_KEYS[e.key]);
    else if (e.key === 'z' || e.key === 'Z') fitTo(view, selectedId);
    else if (e.key === 'u' || e.key === 'U') toggleTool('measure');
    else if (e.key === 'Escape') {
      if (tool) toggleTool(tool);
      else setSelection(null);
    }
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  const selectedObject = selectedId != null ? doc.objects.find((o) => o.id === selectedId) : undefined;
  const selectedStats = selectedId != null ? stats.get(selectedId) : undefined;
  const distance = measure.length === 2 ? measure[0].distanceTo(measure[1]) : null;

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
            draft={draft}
            onChange={set}
            onReset={reset}
            onEditPreset={(kind, filament = 0) => setPreset({ kind, filament })}
            perObject={perObject}
            onPerObject={setPerObject}
            selection={selection}
            onSelect={select}
            onMenu={openMenu}
            stats={stats}
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
                onScene={onScene}
              />
              <MainToolbar layout={layout} active={tool} onTool={toggleTool} />
              {tool === 'measure' && (
                <div
                  className="absolute top-12 left-1/2 -translate-x-1/2 rounded bg-bambu-dark-secondary/95 border border-bambu-dark-tertiary px-3 py-1 text-[12px] text-white"
                  role="status"
                >
                  {distance != null
                    ? t('farm.workspace.measure.distance', { value: toLocaleDecimal(distance.toFixed(2), i18n.language) })
                    : t('farm.workspace.measure.hint')}
                </div>
              )}
              {view && (
                <>
                  <CanvasButtons
                    layout={layout}
                    grid={grid}
                    onGrid={setGrid}
                    onView={(p) => setView(view, p)}
                    onFit={() => fitTo(view, selectedId)}
                  />
                  {currentPlate != null && (
                    <PlateButtons
                      layout={layout}
                      onSettings={() => {
                        setPerObject(true);
                        select({ kind: 'plate', plate: currentPlate });
                      }}
                    />
                  )}
                </>
              )}
              {selectedObject && selectedStats && !infoClosed && (
                <ObjectInfo
                  layout={layout}
                  name={selectedObject.name ?? `#${selectedId}`}
                  stats={selectedStats}
                  onClose={() => setInfoClosed(true)}
                />
              )}
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
      {menu && <ContextMenu items={menu.items} at={menu.at} onClose={() => setMenu(null)} />}
      {preset && (
        <PresetDialog
          layout={layout}
          kind={preset.kind}
          filament={preset.filament}
          name={preset.kind === 'printer' ? presets.printer : null}
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
