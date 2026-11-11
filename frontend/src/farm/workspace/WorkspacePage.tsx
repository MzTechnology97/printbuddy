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
import { AlertTriangle, Loader2 } from 'lucide-react';
import { api, getAuthToken } from '../../api/client';
import { Button } from '../../components/Button';
import { ModelViewer, type ViewerScene } from '../../components/ModelViewer';
import { PrintModal } from '../../components/PrintModal';
import { useSliceJobTracker } from '../../contexts/SliceJobTrackerContext';
import { useToast } from '../../contexts/ToastContext';
import { farmProjectApi } from '../projectSliceApi';
import { type OfferedPreset, type PresetRef, type SwitchReason, type WorkspaceDocument, workspaceApi } from './api';
import { PresetDialog, SaveCopyDialog, ShortcutsDialog } from './Dialogs';
import { ComparePresetsDialog, SavePresetDialog, UnsavedChangesDialog } from './PresetDialogs';
import { SECTION_TITLES, projectPreset, withSwitched } from './presetTools';
import { type LiveState, usePresetSwitch } from './presetSwitch';
import { toLocaleDecimal, useSettingsDraft } from './draft';
import { useMoveGizmo } from './moveGizmo';
import {
  IDENTITY,
  type Placed,
  type Spec,
  applyPlacements,
  currentSpec,
  fromSpec,
  itemKey,
  moved,
  placementsToSave,
  positionOf,
  selectedItems,
  toSpec,
} from './placement';
import {
  type Change,
  type Coordinates,
  about,
  absoluteRotation,
  boundingSphere,
  boxOf,
  changed,
  changedParts,
  inObjectAxes,
  isPartKey,
  partWorld,
  targetKey,
  rotationAbout,
  rotationOf,
  rotationSync,
  targetPoints,
  withLinearOf,
  withoutRotation,
} from './transforms';
import {
  ALIGN_BUTTONS,
  DISTRIBUTE_BUTTONS,
  type AlignSelection,
  type AlignType,
  alignMoves,
  alignSuffix,
  alignTargets,
  canAlign,
  drawAlignPreview,
  isPartsSelection,
  modelTargets,
} from './align';
import { facePlanes, laidOnFace, useLayOnFace } from './layOnFace';
import { useRotateGizmo, useScaleGizmo } from './transformGizmos';
import { type PrintAction, PRINT_ACTIONS, PRINT_LABELS, PRINT_LATER, fileMenu, mainMenu, stripMnemonic, topBarShortcut } from './menuBar';
import { emptyMenu, instanceMenu, multiMenu, objectMenu, partMenu, plateMenu } from './menus';
import { type Selection, instancesOf } from './objects';
import { type OrcaLayout, loadOrcaLayout, orcaText } from './orcaLayout';
import { PreviewPane } from './PreviewPane';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import {
  CanvasButtons,
  ContextMenu,
  Labels,
  MainToolbar,
  type MenuItem,
  MoveWindow,
  RotateWindow,
  ScaleWindow,
  Navigator,
  ObjectInfo,
  PlateBar,
  PlateButtons,
} from './Viewport';
import {
  CANVAS_DEFAULTS,
  type CanvasOptions,
  type ObjectStats,
  type Target,
  VIEW_KEYS,
  applyDisplay,
  drawCollisionVolumes,
  drawMeasure,
  drawPlates,
  fitTo,
  highlight,
  hover,
  inRectangle,
  objectStats,
  orbit,
  parseVolumes,
  pick,
  setSection,
  setView,
  targetStats,
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
// the shortcuts list's rows the workspace does (Shortcuts.cpp ids; the dialog's fixed rows by their wording)
const WORKING_SHORTCUTS = new Set([
  'SelectAll',
  'SelectAllPlates',
  'GizmoMeasure',
  'ViewDefault',
  'ViewTop',
  'ViewBottom',
  'ViewFront',
  'ViewRear',
  'ViewLeft',
  'ViewRight',
  'ViewPlate',
  'ZoomIn',
  'ZoomOut',
  'ShowLabels',
  'ShowCollisionVolumes',
  'ShowWireframe',
  'SwitchView',
  'CollapseSidebar',
  'KeyboardShortcuts',
]);
const WORKING_MOUSE = new Set([
  'Select a part',
  'Select multiple objects',
  'Select objects by rectangle',
  'Deselect All',
  'Rotate View',
  'Pan View',
  'Zoom View',
  'Move section plane',
]);
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

function Workspace({ doc: source, layout }: { doc: WorkspaceDocument; layout: OrcaLayout }) {
  const { t, i18n } = useTranslation();
  // OrcaSlicer's preset combos (presetSwitch.ts): the page shows the switched project; ``livePresets`` is the page now
  const livePresets = useRef<LiveState | null>(null);
  const presetSwitch = usePresetSwitch(source.settings, () => livePresets.current!, (message) => showToast(message, 'error'));
  const { pending } = presetSwitch;
  // the switches are part of the draft's undo history: a switch is one step, preset and values together
  const { settings, switched, switchTo, placed, place, draft, set, reset, resetAll, invalid, count: edits, undo, redo, canUndo, canRedo } =
    useSettingsDraft(source.settings, withSwitched);
  const doc = useMemo(() => ({ ...source, settings }), [source, settings]);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { trackJob } = useSliceJobTracker();
  const fileId = doc.file.id;
  const count = edits + switched.refs.length + Object.keys(placed).length; // unsaved: edits, preset switches, placements
  livePresets.current = { settings, draft, switched, switchTo };
  const [mode, setMode] = useState<Mode>('prepare');
  const [pane, setPane] = useState<Pane>('view');
  // focus mode (on at first, as OrcaSlicer) shows one plate, the first to begin with; off: null, every plate
  const [selectedPlate, setSelectedPlate] = useState<number | null>(doc.plates[0]?.index ?? null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [preset, setPreset] = useState<{ kind: 'printer' | 'filament'; filament: number } | null>(null);
  const [perObject, setPerObject] = useState(false);
  const [selection, setSelection] = useState<Selection>(null);
  const [menu, setMenu] = useState<{ at: { x: number; y: number }; items: MenuItem[] } | null>(null);
  const [view, setViewScene] = useState<ViewerScene | null>(null);
  const [stats, setStats] = useState<Map<string, ObjectStats>>(new Map());
  const [canvas, setCanvas] = useState<CanvasOptions>(CANVAS_DEFAULTS);
  // the section view (GLCanvas3D's): 0 none, up to 1; its plane faces the camera from the first cut until
  // "Set viewing angle" turns it again; a toggle goes back to the last cut (half way the first time)
  const [section, setSectionState] = useState(0);
  const sectionNormal = useRef<THREE.Vector3 | null>(null);
  const lastSection = useRef(0);
  const [sectionAngle, setSectionAngle] = useState(0);
  const [collision, setCollision] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
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
  // OrcaSlicer's Save preset (of a section, a filament's for the filament window) and Compare presets
  const [savePreset, setSavePreset] = useState<{ section: 'process' | 'filament' | 'printer'; filament: number } | null>(null);
  const [compareOpen, setCompareOpen] = useState(false);
  const [picker, setPicker] = useState<{ section: PresetRef['section']; filament: number; at: { x: number; y: number } } | null>(null);

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
  const isSwitched = (section: PresetRef['section']) => switched.refs.some((r) => r.section === section);
  const presets = {
    printer: (isSwitched('printer') ? null : project?.printer_preset) ?? (byKey.get('printer_settings_id')?.value as string | undefined) ?? null,
    process: (isSwitched('process') ? null : project?.process_preset) ?? (byKey.get('print_settings_id')?.value as string | undefined) ?? null,
  };
  const identity = useMemo(() => {
    const one = (key: string) => {
      const v = source.settings.find((s) => s.key === key)?.value;
      return (Array.isArray(v) ? v.join(',') : v) ?? '';
    };
    const parents = source.settings.find((s) => s.key === 'inherits_group')?.value;
    return {
      printer: one('printer_settings_id'),
      printer_parent: Array.isArray(parents) ? (parents[parents.length - 1] ?? '') : '',
      model: one('printer_model'),
      variant: one('printer_variant'),
      nozzle: one('nozzle_diameter'),
    };
  }, [source.settings]);
  const offered = useQuery({
    queryKey: ['workspaceOffered', picker?.section, identity],
    queryFn: () => workspaceApi.offered(picker!.section, identity),
    enabled: picker != null,
  });
  const pickPreset = (entry: OfferedPreset) =>
    picker &&
    presetSwitch.pick({ section: picker.section, index: picker.filament, source: entry.source, id: entry.id, name: entry.id == null ? entry.name : undefined });
  const switchReasons: Record<SwitchReason, string> = {
    unresolved: t('farm.workspace.presets.switch.unresolved'),
    incompatible: t('farm.workspace.presets.switch.incompatible'),
    condition: t('farm.workspace.presets.switch.condition'),
    other_printer: t('farm.workspace.presets.switch.otherPrinter'),
  };
  const pickerItems = (): MenuItem[] => {
    if (offered.isError) return [{ label: t('farm.workspace.presets.switch.failed'), hint: t('farm.workspace.presets.switch.failed') }];
    if (!offered.data) return [{ label: t('farm.workspace.presets.switch.loading'), hint: t('farm.workspace.presets.switch.loading') }];
    const current = picker ? projectPreset(doc.settings, picker.section, picker.filament) : null;
    const entry = (p: OfferedPreset, i: number, list: OfferedPreset[]): MenuItem => ({
      label: p.name,
      checked: p.name === current,
      separator: i > 0 && list[i - 1].source !== p.source,
      hint: p.reason ? switchReasons[p.reason] : undefined,
      onClick: p.reason ? undefined : () => pickPreset(p),
    });
    const items = offered.data.presets.map(entry);
    return items.length ? items : [{ label: t('farm.workspace.presets.switch.none'), hint: t('farm.workspace.presets.switch.none') }];
  };

  // --- 3D view: selection, menus, tools (they only look; see Viewport.tsx) ---
  const tr = useCallback((text: string) => orcaText(layout, text, i18n.language), [layout, i18n.language]);
  // Tab.cpp's "Save current %s" and ParamsPanel's "Compare presets" tooltips
  const presetTips = (section: 'process' | 'filament' | 'printer') => ({
    save: tr('Save current %s').replace('%s', tr(SECTION_TITLES[section])),
    compare: tr('Compare presets'),
  });
  const focusRef = useRef(true);
  focusRef.current = canvas.focus;
  const onScene = useCallback((scene: ViewerScene) => {
    unmirror(scene);
    scene.controls.mouseButtons.MIDDLE = THREE.MOUSE.PAN; // OrcaSlicer's middle drag pans (its default)
    // focus mode off: every plate beside the first, as OrcaSlicer lays them out
    drawPlates(scene, focusRef.current ? 0 : doc.plates.length);
    // the whole bed, as the slicer opens a plate (or the object a menu asked to zoom to)
    if (zoomAfterBuild.current) fitTo(scene, zoomAfterBuild.current);
    else setView(scene, 'iso');
    zoomAfterBuild.current = null;
    setViewScene(scene);
    setStats(objectStats(scene));
    setMeasure([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the plates of this document; focus is read when built
  }, []);
  const selectedId = selection?.kind === 'object' ? selection.id : null;
  const shownIds = selectedIds.length ? selectedIds : selectedId ? [selectedId] : [];
  const instances = useMemo(() => instancesOf(doc.plates), [doc.plates]);
  // the parts selected together, the selection's own first (Alt+click adds another part of the same instance while no
  // tool or Move is open: Orca's Volume selection)
  const [moreParts, setMoreParts] = useState<{ inst: string; parts: string[] }>({ inst: '', parts: [] });
  const partInst = !selectedIds.length && selection?.kind === 'object' && selection.partId ? itemKey(selection.id, selection.instance ?? 0) : null;
  const partIds =
    partInst && selection?.kind === 'object' && selection.partId
      ? [selection.partId, ...(moreParts.inst === partInst ? moreParts.parts.filter((p) => p !== selection.partId) : [])]
      : [];
  // what the 3D view lights: the objects selected together, else the object (its instance, its parts) selected
  const targets: Target[] = selectedIds.length
    ? selectedIds.map((objectId) => ({ objectId }))
    : selection?.kind === 'object'
      ? partIds.length > 1
        ? partIds.map((partId) => ({ objectId: selection.id, instance: selection.instance ?? null, partId }))
        : [{ objectId: selection.id, instance: selection.instance ?? null, partId: selection.partId ?? null }]
      : [];
  const targetsKey = JSON.stringify(targets);
  // OrcaSlicer's Move, Rotate and Scale (their gizmos and windows): the instances selected (one, or every instance of
  // the objects selected), or a part (Alt+click), placed anew in the copy (transforms.ts). Each drag or field is one
  // undo step.
  const moveObjects = selectedIds.length ? selectedIds : selection?.kind === 'object' && !selection.partId ? [selection.id] : [];
  const moveInstance = !selectedIds.length && selection?.kind === 'object' ? selection.instance : null;
  const moveKeys = useMemo(
    () => (doc.editable && !doc.cannot_place ? selectedItems(view, moveObjects, moveInstance) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the objects selected, by id
    [view, doc.editable, doc.cannot_place, moveObjects.join('|'), moveInstance],
  );
  const moveTip = useRef<[number, number, number]>([0, 0, 0]);
  const [dragTip, setDragTip] = useState<{ text: string; x: number; y: number } | null>(null);
  // GizmoObjectManipulation's coordinate system and Uniform scale (on, as Orca starts)
  const [cs, setCs] = useState<Coordinates>('world');
  const [uniform, setUniform] = useState(true);
  const autoDrop = (objectId: string) => doc.objects.find((o) => o.id === objectId)?.auto_drop !== false;
  // a part (one instance shows it; every instance has it, as Orca's ModelVolume): the tools work on its transform
  // as that instance shows it (M·C), and the copy saves its own (C)
  const partTargets =
    doc.editable && !doc.cannot_place && view && partInst && selection?.kind === 'object'
      ? partIds.map((p) => targetKey(selection.id, selection.instance ?? 0, p))
      : [];
  const partTarget = partTargets.length === 1 ? partTargets[0] : null;
  const part = view && partTarget ? partWorld(view, placed, partTarget) : null;
  const partInstance = view && partTargets.length ? currentSpec(view, placed, partTargets[0].split('/')[0]) : null;
  const tkeys = partTargets.length ? partTargets : moveKeys; // what the tools, gizmos and boxes work on
  const lead = part
    ? part.world
    : view && partTargets.length
      ? (partWorld(view, placed, partTargets[0])?.world ?? null)
      : view && moveKeys.length
        ? currentSpec(view, placed, moveKeys[0])
        : null;
  const single = tkeys.length === 1;
  // Object coordinates for one instance, one part or the parts of one instance; several instances on the bed's axes
  const objectCs = cs !== 'world' && (single || partTargets.length > 1);
  const axes = partTargets.length ? partInstance : lead; // the Object axes: the instance's (a part's too, as Orca's Instance)
  // the instance's axes for parts in Object coordinates (several parts have no Part axes of their own)
  const partFrame = partTargets.length && partInstance && (cs === 'object' || (cs === 'part' && !part)) ? rotationOf(partInstance) : undefined;
  /** A change shown while dragging (no bed rule yet, as Orca's drag) or made (``done``: one undo step). */
  const transform = (change: Change, done: boolean) => {
    if (!view) return;
    const drop = done ? autoDrop : () => false;
    const next = partTargets.length
      ? changedParts(view, placed, partTargets, (w, t) => change.apply(w, t), change.drop === 'move' ? 'move' : 'transform', drop)
      : changed(view, placed, change, drop);
    applyPlacements(view, next);
    if (done) place(next);
  };
  const move = (d: [number, number, number], done: boolean) =>
    transform({ keys: moveKeys, apply: (spec) => moved(spec, objectCs && axes ? inObjectAxes(axes, d) : d), sync: 'none', drop: 'move' }, done);
  const box = (keys = tkeys, inCs: Coordinates = 'world', with_: Placed = placed, frame?: THREE.Matrix4) =>
    view ? boxOf(view, with_, keys, inCs, frame) : null;
  const center = (b: THREE.Box3 | null) => (b ? (b.getCenter(new THREE.Vector3()).toArray() as [number, number, number]) : null);
  const redrawGizmo = useMoveGizmo(view, tool === 'move' ? tkeys : [], {
    orient: () => (objectCs && axes ? rotationOf(axes) : undefined),
    onMove: (delta, done) => {
      moveTip.current = delta;
      if (done && !delta.some((d) => d !== 0)) return transform({ keys: [], apply: (s) => s, sync: 'none', drop: 'move' }, false);
      move(delta, done);
    },
    onDrag: (axis, at) => {
      if (axis == null || !at) return setDragTip(null);
      // GLGizmoMove3D::get_tooltip: the box centre of one instance, else the move so far
      const value = single ? (center(box())?.[axis] ?? 0) : moveTip.current[axis];
      setDragTip({ text: `${'XYZ'[axis]}: ${toLocaleDecimal(value.toFixed(2), i18n.language)}`, x: at.x, y: at.y });
    },
  });
  // Rotate (World, as its window says): about the selection's bounding sphere's centre
  const sphere = useMemo(
    () => (view && tool === 'rotate' && tkeys.length ? boundingSphere(view, placed, tkeys) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the instances (or part) selected, by key
    [view, tool, placed, tkeys.join('|')],
  );
  const rotateBy = (axis: 0 | 1 | 2, degrees: number, done: boolean, pivot = sphere?.center) => {
    if (!view || !pivot) return;
    const turned = lead ? about(lead, rotationAbout(axis, degrees), pivot) : null;
    const sync = lead && turned ? rotationSync('world', axis, lead, turned) : 'general';
    transform({ keys: moveKeys, apply: (spec) => about(spec, rotationAbout(axis, degrees), pivot), sync, drop: 'transform' }, done);
  };
  const redrawRotate = useRotateGizmo(view, tool === 'rotate' && tkeys.length > 0, {
    frame: () => sphere,
    onRotate: (axis, degrees, done, pivot) => rotateBy(axis, degrees, done, pivot),
    onDrag: (text, at) => setDragTip(text && at ? { text: text.replace('.', i18n.language?.startsWith('it') ? ',' : '.'), x: at.x, y: at.y } : null),
  });
  // where the selection was when the Rotate tool opened (its first reset)
  const opened = useRef<Placed>({});
  useEffect(() => {
    if (tool !== 'rotate' || !view) return;
    opened.current = partTarget && part ? { [partTarget]: part.own } : Object.fromEntries(moveKeys.map((k) => [k, currentSpec(view, placed, k)!]));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- when the tool opens, or the selection changes
  }, [tool, tkeys.join('|')]);
  /** A part's own transform changed (in the instance's axes), as the instance shows it then. */
  const asShown = (own: Spec): Spec => toSpec(fromSpec(partInstance ?? IDENTITY).multiply(fromSpec(own)));
  // Scale: about the selection's box centre (Selection::scale_and_translate's dragging_center)
  const scaleCs: Coordinates = !objectCs ? 'world' : part ? cs : 'object';
  const scaleBy = (relative: [number, number, number], offset: THREE.Vector3, done: boolean) => {
    const b = box(tkeys, scaleCs, placed, partFrame);
    if (!b || !lead) return;
    const c = b.getCenter(new THREE.Vector3());
    const pivot = scaleCs !== 'world' ? c.clone().applyMatrix4(partFrame ?? rotationOf(lead)) : c;
    const a = new THREE.Matrix4().makeTranslation(offset.x, offset.y, offset.z).multiply(new THREE.Matrix4().makeScale(...relative));
    transform({ keys: moveKeys, apply: (spec) => about(spec, a, pivot, scaleCs, partFrame), sync: 'general', drop: 'transform' }, done);
  };
  /** The Scale window's figures (update_settings_value): the size, and the scale against the instance unscaled. */
  const scaleFigures = () => {
    const now = box(tkeys, scaleCs, placed, partFrame);
    const size = now ? (now.getSize(new THREE.Vector3()).toArray() as [number, number, number]) : ([0, 0, 0] as [number, number, number]);
    if (part) {
      // a part (update_settings_value, single volume): its own scale in Part coordinates, 100% in the others
      const own = scaleCs === 'part' ? ([0, 1, 2].map((c) => 100 * Math.hypot(part.own[3 * c], part.own[3 * c + 1], part.own[3 * c + 2])) as [number, number, number]) : null;
      return { size, unscaled: size, scale: own ?? ([100, 100, 100] as [number, number, number]) };
    }
    if (!single || !lead || !view) return { size, unscaled: size, scale: [100, 100, 100] as [number, number, number] };
    const r = rotationOf(lead).setPosition(lead[9], lead[10], lead[11]);
    const bare: Spec = [r.elements[0], r.elements[1], r.elements[2], r.elements[4], r.elements[5], r.elements[6], r.elements[8], r.elements[9], r.elements[10], lead[9], lead[10], lead[11]];
    const ub = box(moveKeys, scaleCs, { ...placed, [moveKeys[0]]: bare });
    const unscaled = ub ? (ub.getSize(new THREE.Vector3()).toArray() as [number, number, number]) : size;
    return { size, unscaled, scale: size.map((v, i) => (unscaled[i] ? (100 * v) / unscaled[i] : 100)) as [number, number, number] };
  };
  const redrawScale = useScaleGizmo(view, tool === 'scale' && tkeys.length > 0, {
    frame: () => {
      const b = box(tkeys, scaleCs, placed, partFrame);
      return b && lead ? { box: b, orient: scaleCs !== 'world' ? (partFrame ?? rotationOf(lead)) : new THREE.Matrix4() } : null;
    },
    onScale: (scale, offset, done) => scaleBy(scale, offset, done),
    onDrag: (grabber, at) => {
      if (grabber == null || !at) return setDragTip(null);
      // GLGizmoScale3D::get_tooltip: the scale of one instance
      const s = scaleFigures().scale;
      const f = (v: number, n: number) => `${toLocaleDecimal(v.toFixed(n), i18n.language)}%`;
      const text = grabber >= 6 ? s.map((v, i) => `${'XYZ'[i]}: ${f(v, 2)}`).join('\n') : `${'XYZ'[Math.floor(grabber / 2)]}: ${f(s[Math.floor(grabber / 2)], 4)}`;
      setDragTip({ text, x: at.x, y: at.y });
    },
  });
  /** change_scale_value / change_size_value: the scale wanted against the instance unscaled, as a scale from now. */
  const scaleTo = (wanted: (fig: ReturnType<typeof scaleFigures>) => [number, number, number]) => {
    const fig = scaleFigures();
    const w = wanted(fig);
    scaleBy(single && !part ? (w.map((v, i) => (fig.size[i] ? (fig.unscaled[i] * v) / fig.size[i] : 1)) as [number, number, number]) : w, new THREE.Vector3(), true);
  };
  /** A part's scale or size typed (change_scale_value / change_size_value on a volume): by that much from now. */
  const partScale = (axis: 0 | 1 | 2, factor: number) =>
    scaleTo(() => [0, 1, 2].map((i) => (uniform || i === axis ? factor : 1)) as [number, number, number]);
  // Lay on Face (F): one whole instance (GLGizmoFlatten::on_is_activable), on the faces of its model parts' hull
  const modelPart = (objectId: string, partId: string) => {
    const type = doc.objects.find((o) => o.id === objectId)?.parts.find((p) => p.id === partId)?.type;
    return !type || type === 'normal_part';
  };
  const flattenKey = !partTargets.length && moveKeys.length === 1 ? moveKeys[0] : null;
  const flattenSpec = view && flattenKey ? currentSpec(view, placed, flattenKey) : null;
  const flattenAt = flattenSpec?.join(',') ?? '';
  // the faces change with the instance's scale (LᵀL) and its parts, not with where it is or how it is turned
  const gram = flattenSpec
    ? [0, 1, 2].flatMap((i) => [0, 1, 2].map((j) => [0, 1, 2].reduce((sum, k) => sum + flattenSpec[3 * i + k] * flattenSpec[3 * j + k], 0).toFixed(6)))
    : [];
  const flattenShape = `${gram.join(',')}|${JSON.stringify(Object.entries(placed).filter(([k]) => flattenKey && k.startsWith(`${flattenKey.split(':')[0]}/`)))}`;
  const planes = useMemo(
    () => {
      if (tool !== 'flatten' || !view || !flattenKey || !flattenSpec) return null;
      return facePlanes(targetPoints(view, placed, modelTargets(view, flattenKey, modelPart), fromSpec(flattenSpec).invert()), flattenSpec);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the instance's shape: ``flattenShape``
    [tool, view, flattenKey, flattenShape],
  );
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the instance's transform, by value
  const flattenInstance = useMemo(() => flattenSpec, [flattenAt]);
  useLayOnFace(view, planes, flattenInstance, (normal) => {
    // do_rotate("Gizmo-Place on Face"): the other instances turned alike, the object on the bed even when it was sinking
    if (flattenKey) transform({ keys: [flattenKey], apply: (spec) => laidOnFace(spec, normal), sync: 'general', drop: 'flatten' }, true);
  });
  // Align and distribute: within the selection, or to the plate (the instance, for parts) holding it
  const plateAt = (key: string) => {
    const [objectId, inst] = key.split(':');
    const plate = instances.get(objectId)?.find((i) => i.index === Number(inst))?.plate;
    return plate == null ? -1 : doc.plates.findIndex((p) => p.index === plate);
  };
  const alignSelection = (keys: string[], parts: string[], inObject: boolean): AlignSelection | null =>
    view && (keys.length || parts.length)
      ? { view, placed, instances: keys, parts, modelPart, objectCs: inObject, autoDrop, plateOf: plateAt, plateCount: doc.plates.length }
      : null;
  const runAlign = (sel: AlignSelection, type: AlignType, toParent: boolean) => {
    const moves = alignMoves(sel, type, toParent);
    const keys = Object.keys(moves ?? {});
    if (!view || !moves || !keys.length) return;
    // a part through Selection::translate on its volume, an instance on its instance; then do_move's bed rule
    const next = keys.some(isPartKey)
      ? changedParts(view, placed, keys, (w, k) => moved(w, moves[k]), 'move', autoDrop)
      : changed(view, placed, { keys, apply: (spec, k) => moved(spec, moves[k]), sync: 'none', drop: 'move' }, autoDrop);
    applyPlacements(view, next);
    place(next);
  };
  const moveAlign = tool === 'move' ? alignSelection(partTargets.length ? [] : moveKeys, partTargets, objectCs) : null;
  const canWithin = !!moveAlign && canAlign(moveAlign, { axis: 0, kind: 'min' }, false);
  const [alignParent, setAlignParent] = useState(false);
  const [alignHover, setAlignHover] = useState<AlignType | null>(null);
  // reset_align_state: within the selection whenever it can be aligned within, as the tool opens or the selection grows
  useEffect(() => {
    setAlignParent(!canWithin);
    setAlignHover(null);
  }, [tool, canWithin]);
  const toParent = alignParent || !canWithin;
  useEffect(
    () => {
      if (view) drawAlignPreview(view, moveAlign, alignHover, toParent);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the selection by key, and where it is now
    [view, alignHover, toParent, placed, tkeys.join('|'), cs],
  );
  /** A button of the Move window or a menu item: enabled, else why not (the helper's reason, or too few items). */
  const alignButton = (sel: AlignSelection, b: { type: AlignType; label: string; icon: string }, parent: boolean) => {
    const enabled = canAlign(sel, b.type, parent);
    const { targets, why } = alignTargets(sel, b.type, b.type.kind === 'distribute' ? false : parent);
    const reason = enabled ? null : (why ?? (targets.length ? 'Select at least 3 parts or objects' : null));
    return { type: b.type, icon: `${b.icon}_dark`, label: tr(b.label) + alignSuffix(b.type), enabled, why: reason ? tr(reason) : null };
  };
  /** The object menus' Align/Distribute (MenuFactory::append_menu_item_align_distribute): within the selection, World. */
  const alignMenu = (sel: AlignSelection | null): MenuItem[] | string => {
    if (!sel || !canAlign(sel, { axis: 0, kind: 'min' }, false)) return t('farm.workspace.align.nothing');
    const rows = [...DISTRIBUTE_BUTTONS, ...ALIGN_BUTTONS].map((b) => alignButton(sel, b, false));
    return rows.map((r, i) => ({
      label: r.label,
      icon: r.icon,
      separator: i === 3 || i === 6 || i === 9,
      onClick: r.enabled ? () => runAlign(sel, r.type, false) : undefined,
      hint: r.why ?? undefined,
    }));
  };
  useEffect(() => {
    if (!view) return;
    applyPlacements(view, placed);
    setStats(objectStats(view));
    redrawGizmo();
    redrawRotate();
    redrawScale();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- again when the placements or the scene change
  }, [view, placed, cs]);
  // a move a copy can't save is not offered: the backend says so with the document (cannot_place)
  const why = doc.cannot_place ? t('farm.workspace.move.tooLarge') : orcaText(layout, 'Please select at least one object.', i18n.language);
  const ready: true | string = tkeys.length ? true : why;
  const toolReady: Record<string, true | string> = doc.editable
    ? { move: ready, rotate: ready, scale: ready, flatten: flattenKey ? true : tkeys.length ? t('farm.workspace.flatten.single') : why }
    : {};
  /** A placement in the Save dialog: where the instance goes, and its rotation and scale when it has them. */
  const placementText = (spec: Spec) => {
    const n = (v: number) => toLocaleDecimal(Number(v.toFixed(2)).toString(), i18n.language);
    const parts = [positionOf(spec).map((v, i) => `${'XYZ'[i]} ${n(v)}`).join(' · ')];
    const turn = absoluteRotation(spec);
    if (turn.some((v) => v !== 0)) parts.push(`${tr('Rotate')} ${turn.map(n).join(' / ')}°`);
    const scale = [0, 1, 2].map((c) => 100 * Math.hypot(spec[3 * c], spec[3 * c + 1], spec[3 * c + 2]));
    if (scale.some((v) => Math.abs(v - 100) > 0.01)) parts.push(`${tr('NounScale')} ${scale.map(n).join(' / ')}%`);
    return parts.join(' · ');
  };
  useEffect(() => {
    if (view) highlight(view, JSON.parse(targetsKey) as Target[], canvas.outline);
  }, [view, targetsKey, canvas.outline, placed]);
  useEffect(() => {
    if (view) drawPlates(view, canvas.focus ? 0 : doc.plates.length);
  }, [view, canvas.focus, doc.plates.length]);
  useEffect(() => {
    if (view?.grid) view.grid.visible = canvas.grid;
    if (view) applyDisplay(view, canvas, stats);
  }, [view, canvas, stats]);
  const currentPlate = selectedPlate ?? doc.plates[0]?.index ?? null;
  useEffect(() => {
    if (view) setSection(view, section > 0 && sectionNormal.current ? { normal: sectionNormal.current, ratio: section } : null, currentPlate);
  }, [view, section, sectionAngle, currentPlate]);
  const sectionTo = (ratio: number) => {
    const r = Math.min(1, Math.max(0, ratio));
    if (r > 0 && !sectionNormal.current && view) sectionNormal.current = viewDirection(view);
    setSectionState((now) => {
      if (r === 0 && now > 0) lastSection.current = now;
      return r;
    });
  };
  const alignSection = () => {
    if (!view) return;
    sectionNormal.current = viewDirection(view);
    setSectionAngle((n) => n + 1);
  };
  // "Show all collision volumes" (Ctrl+Shift+K): the printer's own, at full height
  const volumes = useMemo(() => {
    const text = (key: string) => {
      const v = byKey.get(key)?.value;
      return (Array.isArray(v) ? v : v ? [v] : []).join('|');
    };
    const perExtruder = (byKey.get('bed_exclude_volume_mode')?.value as string | undefined) === 'per_extruder';
    return parseVolumes(perExtruder ? text('extruder_bed_exclude_volumes') : text('bed_exclude_volumes'), bed?.z ?? 256);
  }, [byKey, bed]);
  useEffect(() => {
    if (view) drawCollisionVolumes(view, collision ? volumes : []);
  }, [view, collision, volumes]);
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
  const deselect = () => {
    setSelection(null);
    setSelectedIds([]);
  };
  const openEmptyMenu = (at: { x: number; y: number }) =>
    setMenu({ at, items: emptyMenu(tr, canvas.labels, { labels: () => setCanvas((c) => ({ ...c, labels: !c.labels })) }) });
  const openMenu = (at: { x: number; y: number }, sel: Exclude<Selection, null>, menuInstances?: string[]) => {
    if (sel.kind === 'object' && sel.instance != null && !sel.part) {
      // an instance node of the list (the 3D view's right click opens the object's menu)
      setMenu({ at, items: instanceMenu(tr, 1) });
      return;
    }
    if (sel.kind === 'object' && selectedIds.length > 1 && selectedIds.includes(sel.id)) {
      setMenu({
        at,
        items: multiMenu(
          tr,
          t('farm.workspace.deleteKey'),
          selectedIds.every((id) => stats.get(id)?.printable ?? true),
          { zoom: () => fitTo(view!, null), deselect },
          doc.editable && !doc.cannot_place ? alignMenu(alignSelection(selectedItems(view, selectedIds, null), [], false)) : undefined,
        ),
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
      items: objectMenu(
        tr,
        t('farm.workspace.deleteKey'),
        stats.get(sel.id)?.printable ?? true,
        {
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
        },
        doc.editable && !doc.cannot_place ? alignMenu(alignSelection(menuInstances ?? selectedItems(view, [sel.id], null), [], false)) : undefined,
      ),
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
      if (hit && e.altKey && hit.partId) {
        const partName = (id: string) => doc.objects.find((o) => o.id === hit.objectId)?.parts.find((p) => p.id === id)?.name ?? null;
        const inst = itemKey(hit.objectId, hit.instance ?? 0);
        if ((tool == null || tool === 'move') && partIds.length && partInst === inst) {
          // Alt+click on another part of the selected parts' instance adds it, on a selected one takes it out (not the last)
          const ids = partIds.includes(hit.partId) ? (partIds.length > 1 ? partIds.filter((p) => p !== hit.partId) : partIds) : [...partIds, hit.partId];
          setMoreParts({ inst, parts: ids.slice(1) });
          if (ids[0] !== partIds[0]) select({ kind: 'object', id: hit.objectId, plate: selectedPlate, instance: hit.instance, partId: ids[0], part: partName(ids[0]) });
          return;
        }
        // Alt+click selects the part under the pointer
        const part = { name: partName(hit.partId) };
        setMoreParts({ inst: '', parts: [] });
        setSelectedIds([]);
        select({ kind: 'object', id: hit.objectId, plate: selectedPlate, instance: hit.instance, partId: hit.partId, part: part?.name ?? null });
      } else if (hit && (e.ctrlKey || e.metaKey)) {
        // Ctrl+click adds to (or takes out of) the selection
        const base = selectedIds.length ? selectedIds : selectedId ? [selectedId] : [];
        setSelectedIds(base.includes(hit.objectId) ? base.filter((id) => id !== hit.objectId) : [...base, hit.objectId]);
        select({ kind: 'object', id: hit.objectId, plate: selectedPlate });
      } else if (hit) {
        // a click selects the instance under the pointer
        setSelectedIds([]);
        select({ kind: 'object', id: hit.objectId, plate: selectedPlate, instance: hit.instance });
      } else deselect();
    },
    (e) => {
      const hit = view && pick(view, e.clientX, e.clientY);
      const at = { x: e.clientX, y: e.clientY };
      if (hit) {
        const sel = { kind: 'object' as const, id: hit.objectId, plate: selectedPlate };
        const mine = !selectedIds.includes(hit.objectId);
        if (mine) {
          setSelectedIds([]);
          select({ ...sel, instance: hit.instance });
        }
        // the 3D view's right click selects the instance under the pointer: the menu's Align/Distribute works on it
        openMenu(at, sel, mine ? [itemKey(hit.objectId, hit.instance ?? 0)] : undefined);
      } else if (view && currentPlate != null && onBed(view, e.clientX, e.clientY)) openMenu(at, { kind: 'plate', plate: currentPlate });
      else openEmptyMenu(at);
    },
  );
  // the 3D view's pointer: hover light, Shift+drag rectangle selection, double click (OrcaSlicer's canvas)
  // the listeners below are set once per view: they read what changes (the plate shown, the section) from here
  const live = useRef({ hovered: null as string | null, section, sectionTo, select, plate: selectedPlate });
  live.current.section = section;
  live.current.sectionTo = sectionTo;
  live.current.select = select;
  live.current.plate = selectedPlate;
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
    // the canvas takes the keyboard when clicked: OrcaSlicer's canvas keys (Tab, Ctrl+0..7...) work while it has focus
    canvasEl.tabIndex = 0;
    const down = (e: PointerEvent) => {
      if (e.target === canvasEl) canvasEl.focus({ preventScroll: true });
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
      if (ids.length) live.current.select({ kind: 'object', id: ids[0], plate: live.current.plate });
    };
    const leave = () => hover(view, (live.current.hovered = null));
    const dbl = (e: MouseEvent) => setPerObject(!!pick(view, e.clientX, e.clientY)); // Objects / Global panel
    // Alt+wheel moves the section plane (GLCanvas3D::on_mouse_wheel), before the camera's own zoom sees it
    const wheel = (e: WheelEvent) => {
      if (!e.altKey) return;
      e.preventDefault();
      e.stopPropagation();
      live.current.sectionTo(live.current.section + (e.deltaY > 0 ? -0.01 : 0.01));
    };
    host.addEventListener('wheel', wheel, { capture: true, passive: false });
    host.addEventListener('pointerdown', down, true);
    canvasEl.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    canvasEl.addEventListener('pointerleave', leave);
    canvasEl.addEventListener('dblclick', dbl);
    return () => {
      cancelAnimationFrame(frame);
      host.removeEventListener('wheel', wheel, { capture: true });
      host.removeEventListener('pointerdown', down, true);
      canvasEl.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      canvasEl.removeEventListener('pointerleave', leave);
      canvasEl.removeEventListener('dblclick', dbl);
    };
  }, [view]);
  const toggleTool = (key: string) => {
    setTool((cur) => (cur === key ? null : key));
    setMeasure([]);
  };
  // OrcaSlicer's keys (Shortcuts.cpp): Ctrl+0..7 views, Z zoom to the selection, I / O zoom in / out, U measure,
  // Ctrl+A select all (Ctrl+Shift+A on all plates), Ctrl+E labels, Ctrl+Shift+K collision volumes,
  // Ctrl+Shift+Enter wireframe, Tab Prepare / Preview, Shift+Tab sidebar, ? the shortcuts list, Esc leaves a tool
  // or the selection
  const dialogOpen = saveOpen || !!preset || !!menu || printOpen || shortcutsOpen;
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    const el = e.target as HTMLElement | null;
    if (dialogOpen || e.altKey) return;
    if (el?.isContentEditable || el?.closest?.('input, textarea, select, [role="listbox"], [role="menu"], [role="combobox"]')) return;
    const ctrl = e.ctrlKey || e.metaKey;
    // the canvas's own keys only while it has focus: elsewhere Tab moves the focus and Ctrl+0 / Ctrl+E... stay the browser's
    const onCanvas = !!view && el === view.renderer.domElement;
    if (e.key === 'Tab' && !ctrl) {
      // the Preview's G-code canvas switches back too (gcode/GcodePreview.tsx marks it)
      if (!onCanvas && el?.dataset?.previewCanvas == null) return;
      e.preventDefault();
      if (e.shiftKey) setSidebarHidden((h) => !h);
      else setMode((m) => (m === 'prepare' ? 'preview' : 'prepare'));
      return;
    }
    if (e.key === '?' && !ctrl) {
      e.preventDefault();
      setShortcutsOpen(true);
      return;
    }
    // the top bar's keys (Shortcuts.cpp), anywhere but in a text field: undo / redo, save, slice, export, print
    if (ctrl && !e.altKey) {
      const action = topBarShortcut(e.key, e.shiftKey, {
        undo: canUndo ? undo : undefined,
        redo: canRedo ? redo : undefined,
        save: canSave ? () => setSaveOpen(true) : undefined,
        slice: sliceRun,
        print: printRun,
        exportSliced: printActions.exportSliced,
        slicing: busy,
      });
      if (action) {
        e.preventDefault(); // only when the key does something here: else it stays the browser's
        if (action === 'slicing') showToast(t('farm.workspace.topbar.slicingNoReload'), 'info');
        else action();
        return;
      }
    }
    if (mode !== 'prepare' || !view) return;
    if (!onCanvas && e.key !== 'Escape') return;
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
    } else if (ctrl && e.shiftKey && (e.key === 'k' || e.key === 'K')) {
      handled();
      setCollision((c) => !c);
    } else if (ctrl && (e.key === 'a' || e.key === 'A')) {
      handled();
      // Ctrl+Shift+A: the objects of every plate (those of other plates light up when their plate shows)
      const ids = e.shiftKey ? doc.objects.map((o) => o.id ?? '').filter(Boolean) : [...stats.keys()];
      setSelectedIds(ids);
      if (ids.length) select({ kind: 'object', id: ids[0], plate: selectedPlate });
    } else if (ctrl) return;
    else if (e.key === 'z' || e.key === 'Z') fitTo(view, selectedId);
    else if (e.key === 'i' || e.key === 'I' || e.key === 'o' || e.key === 'O') {
      // zoom in / out towards the camera's target
      const d = view.camera.position.clone().sub(view.controls.target);
      view.camera.position.copy(view.controls.target).addScaledVector(d, e.key.toLowerCase() === 'i' ? 0.8 : 1.25);
      view.controls.update();
    }
    else if (e.key === 'u' || e.key === 'U') toggleTool('measure');
    else if ((e.key === 'm' || e.key === 'M') && toolReady.move === true) toggleTool('move');
    else if ((e.key === 'r' || e.key === 'R') && toolReady.rotate === true) toggleTool('rotate');
    else if ((e.key === 's' || e.key === 'S') && toolReady.scale === true) toggleTool('scale');
    else if ((e.key === 'f' || e.key === 'F') && toolReady.flatten === true) toggleTool('flatten');
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
  // the info box (Plater.cpp): one instance or one part; a whole object only when it has one instance
  // memoised: targetStats walks the meshes, and the page re-renders on every pointer move of a Shift+drag
  const info = useMemo(() => {
    if (selection?.kind !== 'object' || !view) return null;
    if (selectedIds.length > 1) return { count: selectedIds.length };
    const partId =
      selection.partId ?? (selection.part ? selectedObject?.parts.find((p) => p.name === selection.part)?.id ?? null : null);
    const whole = selection.instance == null && !partId;
    if (whole && (instances.get(selection.id)?.length ?? 1) > 1) return null;
    const s = whole ? stats.get(selection.id) : targetStats(view, { objectId: selection.id, instance: selection.instance ?? 0, partId });
    return s ? { count: 1, stats: s, part: partId ? selection.part : null } : null;
  }, [selection, selectedIds.length, view, stats, instances, selectedObject]);
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
    mutationFn: (name: string) =>
      workspaceApi.saveCopy(fileId, doc.sha256, draft, name || undefined, switched.refs, placementsToSave(placed).transforms, placementsToSave(placed).parts),
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
  const canSave = doc.editable && count > 0;

  // Slice ▾: the plate shown, or every plate one after the other ("Slice all")
  const [sliceMode, setSliceMode] = useState<'plate' | 'all'>('plate');
  const [sliceAll, setSliceAll] = useState<{ at: number; total: number } | null>(null);
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);
  const runSliceAll = async () => {
    const plates = doc.plates.map((p) => p.index);
    for (let i = 0; i < plates.length && alive.current; i++) {
      setSliceAll({ at: i, total: plates.length });
      setSelectedPlate(plates[i]);
      try {
        const started = await farmProjectApi.slice(fileId, plates[i]);
        trackJob(started.job_id, 'libraryFile', doc.file.filename);
        setSliceJob({ id: started.job_id, plate: plates[i] });
        setMode('preview');
        for (;;) {
          const state = await api.getSliceJob(started.job_id);
          if (state.status === 'completed' || state.status === 'failed') break;
          await new Promise((r) => setTimeout(r, 1500));
          if (!alive.current) return;
        }
      } catch {
        break; // the failure shows as for one plate
      }
    }
    setSliceAll(null);
    queryClient.invalidateQueries({ queryKey: ['farmWorkspace', fileId] });
  };
  const busy = slicing || sliceAll != null;
  const sliceRun = canSlice && !busy ? (sliceMode === 'all' ? () => void runSliceAll() : () => sliceMutation.mutate()) : undefined;

  // Print ▾: what the Print button does, remembered as OrcaSlicer does ("remember_print_select")
  const [printAction, setPrintAction] = useState<PrintAction>(() => {
    try {
      const saved = localStorage.getItem('pb.workspace.printAction') as PrintAction | null;
      return saved && PRINT_ACTIONS.includes(saved) && !PRINT_LATER[saved] ? saved : 'printPlate';
    } catch {
      return 'printPlate';
    }
  });
  const choosePrint = (a: PrintAction) => {
    setPrintAction(a);
    try {
      localStorage.setItem('pb.workspace.printAction', a);
    } catch {
      // private window: not remembered
    }
  };
  const exportGcode = async (id: number) => {
    const token = getAuthToken();
    const r = await fetch(api.getLibraryFileGcodeUrl(id), { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!r.ok) return showToast(t('farm.workspace.topbar.exportFailed'), 'error');
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = `${doc.file.filename.replace(/\.(gcode\.)?3mf$/i, '')}${plate != null ? ` - plate ${plate}` : ''}.gcode`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const sliced1 = output?.current ? output.library_file_id : null;
  const printActions: Record<PrintAction, (() => void) | undefined> = {
    printPlate: sliced1 != null ? () => setPrintOpen(true) : undefined,
    printAll: undefined,
    exportSliced: sliced1 != null ? () => void api.downloadLibraryFile(sliced1) : undefined,
    exportAllSliced: undefined,
    exportGcode: sliced1 != null ? () => void exportGcode(sliced1) : undefined,
  };
  const printRun = printActions[printAction];
  const hints = { phase: (n: number) => t('farm.workspace.topbar.phase', { phase: n }), later: t('farm.workspace.topbar.later') };
  const trm = (text: string) => stripMnemonic(tr(text));
  const trc = (context: string, text: string) => {
    const v = tr(`${context}\u0004${text}`);
    return v.includes('\u0004') ? stripMnemonic(text) : stripMnemonic(v);
  };
  const menuState = {
    canUndo,
    canRedo,
    canSave,
    dirty: count > 0,
    hasSlice: sliced1 != null,
    prepare: mode === 'prepare' && !!view,
    perspective: canvas.perspective,
    navigator: canvas.navigator,
    grid: canvas.grid,
    labels: canvas.labels,
    collision,
    overhangs: canvas.overhangs,
    outline: canvas.outline,
  };
  const menuActions = {
    open: () => (!count || window.confirm(t('farm.workspace.unsavedConfirm'))) && navigate('/files'),
    save: () => setSaveOpen(true),
    download3mf: () => void api.downloadLibraryFile(fileId, doc.file.filename),
    exportSliced: () => printActions.exportSliced?.(),
    exportGcode: () => printActions.exportGcode?.(),
    quit: close,
    undo,
    redo,
    resetAll: () => {
      resetAll();
      presetSwitch.reset();
    },
    selectAll: () => {
      const ids = [...stats.keys()];
      setSelectedIds(ids);
      if (ids.length) select({ kind: 'object', id: ids[0], plate: selectedPlate });
    },
    deselectAll: () => deselect(),
    view: (preset: Parameters<typeof setView>[1]) => view && setView(view, preset),
    perspective: (on: boolean) => setCanvas((c) => ({ ...c, perspective: on })),
    toggle: (option: 'navigator' | 'grid' | 'labels' | 'collision' | 'overhangs' | 'outline') =>
      option === 'collision' ? setCollision((c) => !c) : setCanvas((c) => ({ ...c, [option]: !c[option] })),
    shortcuts: () => setShortcutsOpen(true),
  };

  return (
    <div className="h-screen flex flex-col bg-bambu-dark text-white" style={SLICER_DARK}>
      {/* Top bar, as the clients: the file, Prepare / Preview, then save, slice, print, export */}
      <TopBar
        title={doc.file.filename}
        subtitle={(SLICER_NAMES[doc.slicer] ?? doc.slicer) + (doc.copy_of ? ` · ${t('farm.workspace.copyOf', { name: doc.copy_of.copy_of_filename })}` : '')}
        dirty={count > 0}
        file={fileMenu(trm, hints, menuState, menuActions, { download3mf: t('farm.workspace.topbar.download3mf') })}
        main={mainMenu(trm, trc, hints, menuState, menuActions, { resetAll: t('farm.workspace.resetAll') })}
        labels={{
          close: t('farm.workspace.close'),
          file: trm('File'),
          menu: t('farm.workspace.topbar.menu'),
          save: trm('Save Project'),
          undo: trm('Undo'),
          redo: trm('Redo'),
          tabs: t('farm.workspace.title'),
        }}
        onClose={close}
        onSave={canSave ? () => setSaveOpen(true) : undefined}
        onUndo={canUndo ? undo : undefined}
        onRedo={canRedo ? redo : undefined}
        modes={(['prepare', 'preview'] as Mode[]).map((m) => ({ key: m, label: t(`farm.workspace.${m}`) }))}
        mode={mode}
        onMode={(m) => setMode(m as Mode)}
        slice={{
          label: busy
            ? `${sliceAll ? `${sliceAll.at + 1}/${sliceAll.total} · ` : ''}${percent != null ? `${Math.round(percent)}%` : ''}`
            : trm(sliceMode === 'all' ? 'Slice all' : 'Slice plate') + (sliceMode === 'plate' && plate != null && doc.plates.length > 1 ? ` ${plate}` : ''),
          run: sliceRun,
          busy,
          title: sliceTitle,
          more: t('farm.workspace.topbar.sliceOptions'),
          options: [
            { label: trm('Slice all'), checked: sliceMode === 'all', onClick: doc.plates.length > 1 ? () => setSliceMode('all') : undefined },
            { label: trm('Slice plate'), checked: sliceMode === 'plate', onClick: () => setSliceMode('plate') },
          ],
        }}
        print={{
          label: trm(PRINT_LABELS[printAction]),
          run: printRun,
          title: printRun ? undefined : t('farm.workspace.topbar.sliceFirst'),
          more: t('farm.workspace.topbar.printOptions'),
          options: PRINT_ACTIONS.map((a) => {
            const later = PRINT_LATER[a];
            return {
              label: trm(PRINT_LABELS[a]),
              checked: printAction === a,
              onClick: later ? undefined : () => choosePrint(a),
              hint: later === 'later' ? hints.later : later ? hints.phase(later) : undefined,
            };
          }),
        }}
      />

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
            onSavePreset={doc.kind === 'project' ? () => setSavePreset({ section: 'process', filament: 0 }) : undefined}
            onComparePresets={() => setCompareOpen(true)}
            presetTips={presetTips('process')}
            onPickPreset={doc.editable && !presetSwitch.busy ? (section, filament, at) => setPicker({ section, filament, at }) : undefined}
            pickLabel={(name) => t('farm.workspace.presets.switch.pick', { name })}
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
              <MainToolbar layout={layout} active={tool} onTool={toggleTool} ready={toolReady} />
              {tool === 'move' && view && tkeys.length > 0 && (
                <MoveWindow
                  layout={layout}
                  cs={objectCs ? 'object' : 'world'}
                  relative={objectCs && !part}
                  onCs={setCs}
                  position={part && objectCs ? positionOf(part.own) : single && lead ? positionOf(lead) : (center(box()) ?? [0, 0, 0])}
                  canDrop={Math.abs(box()?.min.z ?? 0) > 0.001}
                  onPosition={(axis, value) => {
                    if (part && objectCs) {
                      // a part in Object coordinates: its place in the object, typed
                      const own = [...part.own];
                      own[9 + axis] = value;
                      return transform({ keys: [], apply: () => asShown(own), sync: 'none', drop: 'move' }, true);
                    }
                    const d: [number, number, number] = [0, 0, 0];
                    // World: to the position typed; Object: by that much along the object's own axis ("Relative")
                    d[axis] = objectCs ? value : value - (single && lead ? positionOf(lead) : (center(box()) ?? [0, 0, 0]))[axis];
                    move(d, true);
                  }}
                  onDrop={() => {
                    // Selection::drop: the selection (an instance, or a part) down by its lowest point
                    const low = box()?.min.z ?? 0;
                    transform({ keys: moveKeys, apply: (spec) => moved(spec, [0, 0, -low]), sync: 'none', drop: 'move' }, true);
                  }}
                  onDone={() => toggleTool('move')}
                  align={
                    moveAlign
                      ? {
                          canWithin,
                          toParent,
                          parentLabel: isPartsSelection(moveAlign, true) ? 'Object' : 'Plate',
                          onToParent: setAlignParent,
                          align: ALIGN_BUTTONS.map((b) => alignButton(moveAlign, b, toParent)),
                          distribute: DISTRIBUTE_BUTTONS.map((b) => alignButton(moveAlign, b, false)),
                          onRun: (type) => runAlign(moveAlign, type, toParent),
                          onHover: setAlignHover,
                        }
                      : undefined
                  }
                />
              )}
              {tool === 'rotate' && view && tkeys.length > 0 && (
                <RotateWindow
                  layout={layout}
                  // a part: its own rotation (get_volume_transformation), else the instance's
                  absolute={part ? absoluteRotation(part.own) : single && lead ? absoluteRotation(lead) : null}
                  changed={tkeys.some((k) => {
                    const now = part ? part.own : currentSpec(view, placed, k);
                    return !!opened.current[k] && !!now && now.slice(0, 9).some((v, i) => Math.abs(v - opened.current[k][i]) > 1e-9);
                  })}
                  turned={absoluteRotation(part ? part.own : (lead ?? IDENTITY)).some((v) => v !== 0) && single}
                  onRelative={(axis, degrees) => rotateBy(axis, degrees, true)}
                  onAbsolute={(axis, degrees) => lead && rotateBy(axis, degrees - absoluteRotation(part ? part.own : lead)[axis], true)}
                  onResetRelative={() =>
                    part && partTarget
                      ? transform({ keys: [], apply: () => asShown(withLinearOf(part.own, opened.current[partTarget] ?? part.own)), sync: 'general', drop: 'transform' }, true)
                      : transform({ keys: moveKeys, apply: (spec, k) => (opened.current[k] ? withLinearOf(spec, opened.current[k]) : spec), sync: 'general', drop: 'transform' }, true)
                  }
                  onResetAbsolute={() =>
                    transform({ keys: moveKeys, apply: part ? () => asShown(withoutRotation(part.own)) : withoutRotation, sync: 'general', drop: 'transform' }, true)
                  }
                  onDone={() => toggleTool('rotate')}
                />
              )}
              {tool === 'scale' && view && tkeys.length > 0 && (
                <ScaleWindow
                  layout={layout}
                  cs={scaleCs}
                  onCs={setCs}
                  objectCs={single}
                  partCs={!!part}
                  scale={scaleFigures().scale}
                  size={scaleFigures().size}
                  uniform={uniform}
                  onUniform={setUniform}
                  onScale={(axis, percent) =>
                    part ? partScale(axis, percent / (scaleFigures().scale[axis] || 100)) : scaleTo((fig) =>
                      single
                        ? (fig.scale.map((v, i) => (uniform || i === axis ? percent / 100 : v / 100)) as [number, number, number])
                        : ([0, 1, 2].map((i) => (uniform || i === axis ? percent / 100 : 1)) as [number, number, number]),
                    )
                  }
                  onSize={(axis, mm) =>
                    part ? partScale(axis, mm / (scaleFigures().size[axis] || 1)) : scaleTo((fig) => {
                      const ref = single ? fig.unscaled : fig.size;
                      const k = mm / (ref[axis] || 1);
                      const sizeNow = fig.size.map((v, i) => v / (ref[i] || 1));
                      return (single ? sizeNow : [1, 1, 1]).map((v, i) => (uniform || i === axis ? k : v)) as [number, number, number];
                    })
                  }
                  onReset={() =>
                    part
                      ? scaleTo((fig) => fig.scale.map((v) => 100 / (v || 100)) as [number, number, number])
                      : scaleTo(() => [1, 1, 1])
                  }
                  onDone={() => toggleTool('scale')}
                />
              )}
              {dragTip && (
                <div
                  className="fixed z-50 pointer-events-none rounded bg-bambu-dark-secondary/95 border border-bambu-dark-tertiary px-1.5 py-0.5 text-[12px] text-white"
                  style={{ left: dragTip.x + 14, top: dragTip.y + 14 }}
                >
                  {dragTip.text}
                </div>
              )}
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
                    onSection={sectionTo}
                    onToggleSection={() => sectionTo(section > 0 ? 0 : lastSection.current > 0 ? lastSection.current : 0.5)}
                    onSectionAngle={alignSection}
                  />
                  {canvas.navigator && (
                    <Navigator
                      direction={() => {
                        const d = viewDirection(view);
                        return { x: d.x, y: d.y, z: d.z };
                      }}
                      onView={(p) => setView(view, Array.isArray(p) ? new THREE.Vector3(...p).normalize() : p)}
                      onOrbit={(dx, dy) => orbit(view, dx, dy)}
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
              {selectedObject && info && !infoClosed && (
                <ObjectInfo
                  layout={layout}
                  name={selectedObject.name ?? `#${selectedId}`}
                  part={info.part}
                  count={info.count}
                  stats={info.stats}
                  onClose={() => setInfoClosed(true)}
                />
              )}
              {/* the plate switcher shows in focus mode (GLCanvas3D); off, every plate is in the 3D view */}
              {canvas.focus && (
                <PlateBar fileId={fileId} plates={doc.plates} selected={selectedPlate} onSelect={(p) => p != null && setSelectedPlate(p)} />
              )}
            </div>
          ) : (
            <div className="relative flex-1 min-h-0">
              <PreviewPane
                plate={plate}
                output={output}
                bed={bed}
                colours={colours}
                layout={layout}
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
          switches={[
            ...switched.refs.map((r) => ({
              section: tr(SECTION_TITLES[r.section]) + (r.section === 'filament' ? ` ${r.index + 1}` : ''),
              name: projectPreset(doc.settings, r.section, r.index) ?? '—',
            })),
            // each instance placed anew: the object (and its instance when it has more) and where it goes now
            ...Object.entries(placed).map(([key, spec]) => {
              if (key.includes('/')) {
                const [id, partId] = key.split('/');
                const obj = doc.objects.find((o) => o.id === id);
                return { section: `${obj?.name ?? id} › ${obj?.parts.find((p) => p.id === partId)?.name ?? partId}`, name: placementText(spec) };
              }
              const [id, instance] = key.split(':');
              const name = doc.objects.find((o) => o.id === id)?.name ?? id;
              const many = (instances.get(id)?.length ?? 1) > 1;
              return {
                section: many ? `${name} #${Number(instance) + 1}` : name,
                name: placementText(spec),
              };
            }),
          ]}
          invalid={invalid}
          saving={saveMutation.isPending}
          error={saveMutation.isError ? saveMutation.error : null}
          onSave={(name) => saveMutation.mutate(name)}
          onClose={() => setSaveOpen(false)}
        />
      )}
      {menu && <ContextMenu items={menu.items} at={menu.at} onClose={() => setMenu(null)} />}
      {picker && <ContextMenu key={offered.status} items={pickerItems()} at={picker.at} onClose={() => setPicker(null)} />}
      {pending && (
        <UnsavedChangesDialog
          layout={layout}
          preset={projectPreset(doc.settings, pending.ref.section, pending.ref.index) ?? '—'}
          keys={pending.keys}
          onTransfer={() => presetSwitch.commit(pending, true)}
          onDiscard={() => presetSwitch.commit(pending, false)}
          onClose={presetSwitch.cancel}
        />
      )}
      {shortcutsOpen && (
        <ShortcutsDialog
          layout={layout}
          context={mode === 'prepare' ? 'Plater' : 'Preview'}
          works={(row) => (row.id ? WORKING_SHORTCUTS.has(row.id) : WORKING_MOUSE.has(row.label))}
          onClose={() => setShortcutsOpen(false)}
        />
      )}
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
          onSavePreset={doc.kind === 'project' ? () => setSavePreset({ section: preset.kind, filament: preset.filament }) : undefined}
          onComparePresets={() => setCompareOpen(true)}
          presetTips={presetTips(preset.kind)}
        />
      )}
      {savePreset && (
        <SavePresetDialog
          layout={layout}
          section={savePreset.section}
          filament={savePreset.filament}
          settings={doc.settings}
          draft={draft}
          invalid={invalid}
          onClose={() => setSavePreset(null)}
          onSaved={(name) => {
            setSavePreset(null);
            showToast(t('farm.workspace.presets.saved', { name }), 'success');
          }}
        />
      )}
      {compareOpen && (
        <ComparePresetsDialog
          layout={layout}
          settings={doc.settings}
          draft={draft}
          editable={doc.editable}
          onTransfer={set}
          onClose={() => setCompareOpen(false)}
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
