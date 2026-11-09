// Fork (printfarm): 3MF Workspace, a full-screen page opened from the File Manager ("Edit 3MF"), laid out as the
// slicer clients: Prepare (sidebar with printer, filaments, objects and process settings; 3D plate view) and
// Preview (the sliced G-code). Settings are saved as a copy (the original is never written), sliced with its own
// settings on the existing sidecars and printed through the existing print modal. The 3D view selects, zooms,
// measures and shows object info as OrcaSlicer's; its menus and tools that would change the project are phase 2.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
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
import { emptyMenu, multiMenu, objectMenu, partMenu, plateMenu } from './menus';
import type { Selection } from './objects';
import { type OrcaLayout, loadOrcaLayout, orcaText } from './orcaLayout';
import { PreviewPane } from './PreviewPane';
import { Sidebar } from './Sidebar';
import {
  CanvasButtons,
  ContextMenu,
  Labels,
  MainToolbar,
  type MenuItem,
  Navigator,
  ObjectInfo,
  PlateBar,
  PlateButtons,
} from './Viewport';
import {
  CANVAS_DEFAULTS,
  type CanvasOptions,
  type ObjectStats,
  VIEW_KEYS,
  applyDisplay,
  drawMeasure,
  fitTo,
  highlight,
  hover,
  inRectangle,
  objectStats,
  pick,
  setSection,
  setView,
  toScreen,
  onBed,
  unmirror,
  useCanvasClicks,
  viewDirection,
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
  // the slicer's own layout: Bambu Studio's for its projects, OrcaSlicer's otherwise
  const slicer = docQuery.data?.slicer;
  const layoutQuery = useQuery({
    queryKey: ['orcaLayout', slicer],
    queryFn: () => loadOrcaLayout(slicer),
    enabled: !!docQuery.data,
    staleTime: Infinity,
  });
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
  const [canvas, setCanvas] = useState<CanvasOptions>(CANVAS_DEFAULTS);
  const [section, setSectionRatio] = useState<number | null>(null);
  const sectionNormal = useRef(new THREE.Vector3(0, 1, 0)); // a horizontal cut until "Set viewing angle"
  // several objects selected (Ctrl+click, Shift+drag); ``selection`` is the one the panels show
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [rubber, setRubber] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [sidebarHidden, setSidebarHidden] = useState(false);
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
  const shownIds = selectedIds.length ? selectedIds : selectedId ? [selectedId] : [];
  const shownKey = shownIds.join(',');
  useEffect(() => {
    if (view) highlight(view, shownKey ? shownKey.split(',') : [], canvas.outline);
  }, [view, shownKey, canvas.outline]);
  useEffect(() => {
    if (view?.grid) view.grid.visible = canvas.grid;
    if (view) applyDisplay(view, canvas, stats);
  }, [view, canvas, stats]);
  useEffect(() => {
    if (view) setSection(view, section == null ? null : { normal: sectionNormal.current, ratio: section });
  }, [view, section]);
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
  const deselect = () => {
    setSelection(null);
    setSelectedIds([]);
  };
  const openEmptyMenu = (at: { x: number; y: number }) =>
    setMenu({ at, items: emptyMenu(tr, canvas.labels, { labels: () => setCanvas((c) => ({ ...c, labels: !c.labels })) }) });
  const openMenu = (at: { x: number; y: number }, sel: Exclude<Selection, null>) => {
    if (sel.kind === 'object' && selectedIds.length > 1 && selectedIds.includes(sel.id)) {
      setMenu({
        at,
        items: multiMenu(tr, t('farm.workspace.deleteKey'), selectedIds.every((id) => stats.get(id)?.printable ?? true), {
          zoom: () => fitTo(view!, null),
          deselect,
        }),
      });
      return;
    }
    if (sel.kind === 'object' && sel.part) {
      setMenu({
        at,
        items: partMenu(tr, t('farm.workspace.deleteKey'), {
          select: () => select(sel),
          zoom: () => zoomTo(sel.id),
          settings: () => {
            setPerObject(true);
            select(sel);
          },
        }),
      });
      return;
    }
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
      if (hit && (e.ctrlKey || e.metaKey)) {
        // Ctrl+click adds to (or takes out of) the selection
        const base = selectedIds.length ? selectedIds : selectedId ? [selectedId] : [];
        setSelectedIds(base.includes(hit.objectId) ? base.filter((id) => id !== hit.objectId) : [...base, hit.objectId]);
        select({ kind: 'object', id: hit.objectId, plate: selectedPlate });
      } else if (hit) {
        setSelectedIds([]);
        select({ kind: 'object', id: hit.objectId, plate: selectedPlate });
      } else deselect();
    },
    (e) => {
      const hit = view && pick(view, e.clientX, e.clientY);
      const at = { x: e.clientX, y: e.clientY };
      if (hit) {
        const sel = { kind: 'object' as const, id: hit.objectId, plate: selectedPlate };
        if (!selectedIds.includes(hit.objectId)) {
          setSelectedIds([]);
          select(sel);
        }
        openMenu(at, sel);
      } else if (view && currentPlate != null && onBed(view, e.clientX, e.clientY)) openMenu(at, { kind: 'plate', plate: currentPlate });
      else openEmptyMenu(at);
    },
  );
  // the 3D view's pointer: hover light, Shift+drag rectangle selection, double click (OrcaSlicer's canvas)
  const live = useRef({ selectedIds, hovered: null as string | null });
  live.current.selectedIds = selectedIds;
  useEffect(() => {
    const canvasEl = view?.renderer.domElement;
    const host = canvasEl?.parentElement;
    if (!view || !canvasEl || !host) return;
    let frame = 0;
    let start: { x: number; y: number } | null = null;
    const move = (e: PointerEvent) => {
      if (start) {
        setRubber({ x0: start.x, y0: start.y, x1: e.clientX, y1: e.clientY });
        return;
      }
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const id = pick(view, e.clientX, e.clientY)?.objectId ?? null;
        if (id !== live.current.hovered) hover(view, (live.current.hovered = id));
      });
    };
    const down = (e: PointerEvent) => {
      if (!e.shiftKey || e.button !== 0) return;
      start = { x: e.clientX, y: e.clientY };
      view.controls.enabled = false; // Shift+drag selects instead of panning
      e.stopPropagation();
    };
    const up = (e: PointerEvent) => {
      if (!start) return;
      const ids = inRectangle(view, start, { x: e.clientX, y: e.clientY });
      start = null;
      setRubber(null);
      view.controls.enabled = true;
      setSelectedIds(ids);
      if (ids.length) select({ kind: 'object', id: ids[0], plate: selectedPlate });
    };
    const leave = () => hover(view, (live.current.hovered = null));
    const dbl = (e: MouseEvent) => setPerObject(!!pick(view, e.clientX, e.clientY)); // Objects / Global panel
    host.addEventListener('pointerdown', down, true);
    canvasEl.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    canvasEl.addEventListener('pointerleave', leave);
    canvasEl.addEventListener('dblclick', dbl);
    return () => {
      cancelAnimationFrame(frame);
      host.removeEventListener('pointerdown', down, true);
      canvasEl.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      canvasEl.removeEventListener('pointerleave', leave);
      canvasEl.removeEventListener('dblclick', dbl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- select reads the plate shown; the view is what matters
  }, [view]);
  const toggleTool = (key: string) => {
    setTool((cur) => (cur === key ? null : key));
    setMeasure([]);
  };
  // OrcaSlicer's keys (Shortcuts.cpp): Ctrl+0..7 views, Z zoom to the selection, U measure, Ctrl+A select all,
  // Ctrl+E labels, Ctrl+Shift+Enter wireframe, Tab Prepare / Preview, Shift+Tab sidebar, Esc leaves a tool / selection
  const dialogOpen = saveOpen || !!preset || !!menu || printOpen;
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    const el = e.target as HTMLElement | null;
    if (dialogOpen || e.altKey) return;
    if (el?.closest?.('input, textarea, select, [role="listbox"], [role="menu"], [role="combobox"]')) return;
    const ctrl = e.ctrlKey || e.metaKey;
    if (e.key === 'Tab' && !ctrl) {
      e.preventDefault();
      if (e.shiftKey) setSidebarHidden((h) => !h);
      else setMode((m) => (m === 'prepare' ? 'preview' : 'prepare'));
      return;
    }
    if (mode !== 'prepare' || !view) return;
    const handled = () => e.preventDefault();
    if (ctrl && e.key in VIEW_KEYS) {
      handled();
      const preset = VIEW_KEYS[e.key];
      setView(view, preset === 'plate' ? 'iso' : preset);
    } else if (ctrl && e.shiftKey && e.key === 'Enter') {
      handled();
      setCanvas((c) => ({ ...c, wireframe: !c.wireframe }));
    } else if (ctrl && (e.key === 'e' || e.key === 'E')) {
      handled();
      setCanvas((c) => ({ ...c, labels: !c.labels }));
    } else if (ctrl && (e.key === 'a' || e.key === 'A')) {
      handled();
      const ids = [...stats.keys()];
      setSelectedIds(ids);
      if (ids.length) select({ kind: 'object', id: ids[0], plate: selectedPlate });
    } else if (ctrl) return;
    else if (e.key === 'z' || e.key === 'Z') fitTo(view, selectedId);
    else if (e.key === 'u' || e.key === 'U') toggleTool('measure');
    else if (e.key === 'Escape') {
      if (tool) toggleTool(tool);
      else deselect();
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
          className={`${pane === 'settings' ? 'flex' : 'hidden'} ${sidebarHidden ? 'lg:hidden' : 'lg:flex'} flex-col w-full lg:w-[24rem] flex-shrink-0 border-r border-bambu-dark-tertiary bg-bambu-dark-secondary min-h-0`}
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
            onEmptyMenu={openEmptyMenu}
            selectedIds={shownIds}
            onToggle={(id) => {
              // Ctrl / Shift click in the list: add to or take out of the selection, as in 3D
              const base = shownIds;
              setSelectedIds(base.includes(id) ? base.filter((x) => x !== id) : [...base, id]);
            }}
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
                    options={canvas}
                    onOption={(key, on) => {
                      setCanvas((c) => ({ ...c, [key]: on }));
                      // focus mode off: every plate at once (on: the plate chosen in the bar)
                      if (key === 'focus') setSelectedPlate(on ? (doc.plates[0]?.index ?? null) : null);
                    }}
                    onFit={() => fitTo(view, selectedId)}
                    section={section}
                    onSection={setSectionRatio}
                    onSectionAngle={() => {
                      sectionNormal.current = viewDirection(view);
                      setSectionRatio((r) => (r == null ? 1 : r - 1e-9)); // re-apply with the new plane
                    }}
                  />
                  {canvas.navigator && (
                    <Navigator
                      direction={() => {
                        const d = viewDirection(view);
                        return { x: d.x, y: d.y, z: d.z };
                      }}
                      onView={(p) => setView(view, p)}
                    />
                  )}
                  {canvas.labels && (
                    <Labels
                      place={() =>
                        [...stats.entries()].flatMap(([id, s]) => {
                          const at = toScreen(view, s.centroid);
                          const name = doc.objects.find((o) => o.id === id)?.name ?? `#${id}`;
                          return at ? [{ id, name, ...at }] : [];
                        })
                      }
                    />
                  )}
                  {rubber && (
                    <div
                      className="pointer-events-none fixed border border-bambu-green bg-bambu-green/10"
                      style={{
                        left: Math.min(rubber.x0, rubber.x1),
                        top: Math.min(rubber.y0, rubber.y1),
                        width: Math.abs(rubber.x1 - rubber.x0),
                        height: Math.abs(rubber.y1 - rubber.y0),
                      }}
                    />
                  )}
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
                  part={selection?.kind === 'object' ? selection.part : null}
                  count={Math.max(selectedIds.length, 1)}
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
