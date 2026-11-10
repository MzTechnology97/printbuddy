// Fork (printfarm): the Preview tab's G-code view, as OrcaSlicer's (GCodeViewer, IMSlider; v2.5.0): every move
// drawn by its own role and size, the legend with its view types, the layer slider with two thumbs on the
// right and the move slider at the bottom, and Orca's preview keys while the canvas or a slider has the focus.
import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { Loader2, Square } from 'lucide-react';
import { getAuthToken } from '../../../api/client';
import type { ViewerScene } from '../../../components/ModelViewer';
import { VIEW_KEYS, setView } from '../viewTools';
import { LegendPanel } from './LegendPanel';
import { type OptionKey, type RangeView, type ViewType, VIEW_TYPES, defaultViewType, paint, rangeOf, shortTime } from './legend';
import { sliceWarnings, warningText } from './checks';
import { MOVE, ROLES, type Toolpaths } from './parse';
import type { WorkerReply, WorkerRequest } from './parse.worker';
import { ToolpathScene } from './scene';
import { initialSliders, sliderReducer } from './sliders';

export interface PreviewLabels {
  loading: string;
  failed: string;
  tooLarge: string;
  memory: string;
  oneLayer: string;
  upperLayer: string;
  lowerLayer: string;
  move: string;
  /** "Layer 12, Z 2.40 mm" for screen readers. */
  layerText: (layer: number, z: string) => string;
}

interface Props {
  url: string;
  bed: { x: number; y: number; z: number };
  colours: string[];
  /** An OrcaSlicer string in the UI language. */
  o: (text: string) => string;
  phaseTitle: (phase: number) => string;
  labels: PreviewLabels;
  /** Shown above the legend (the slice is of an older version of the 3MF, ...). */
  notice?: React.ReactNode;
}

const OPTION_DEFAULTS: Record<OptionKey, boolean> = { travel: false, retract: false, unretract: false, wipe: false, seam: true };
/** The parsed columns may take this much (about 36 bytes a move): beyond it the plate is refused with a message
 * rather than risking the tab. */
const MAX_COLUMN_BYTES = 1_000_000_000;

class PreviewError extends Error {
  readonly reason: string;
  constructor(reason: string, detail: string) {
    super(detail);
    this.reason = reason;
  }
}

export function GcodePreview({ url, bed, colours, o, phaseTitle, labels, notice }: Props) {
  const [progress, setProgress] = useState<{ read: number; total: number } | null>(null);
  // read in a worker (streamed, off the page); kept for a minute so Prepare <-> Preview doesn't read it again
  const query = useQuery({
    queryKey: ['workspaceGcode', url],
    queryFn: ({ signal }) =>
      new Promise<Toolpaths>((resolve, reject) => {
        const worker = new Worker(new URL('./parse.worker.ts', import.meta.url), { type: 'module' });
        const stop = () => worker.terminate();
        signal.addEventListener('abort', () => {
          stop();
          reject(new PreviewError('aborted', 'aborted'));
        });
        worker.onmessage = (e: MessageEvent<WorkerReply>) => {
          const m = e.data;
          if (m.kind === 'progress') setProgress({ read: m.read, total: m.total });
          else {
            stop();
            if (m.kind === 'done') resolve(m.toolpaths);
            else reject(new PreviewError(m.reason, m.detail));
          }
        };
        worker.onerror = (e) => {
          stop();
          reject(new PreviewError('other', e.message || 'worker error'));
        };
        const request: WorkerRequest = { url: new URL(url, window.location.href).href, token: getAuthToken(), maxBytes: MAX_COLUMN_BYTES };
        worker.postMessage(request);
      }),
    staleTime: Infinity,
    gcTime: 60_000,
    retry: false,
  });
  if (query.error) {
    const reason = (query.error as PreviewError).reason;
    const text = reason === 'tooLarge' ? labels.tooLarge : reason === 'memory' ? labels.memory : `${labels.failed}: ${query.error.message}`;
    return (
      <div className="h-full flex items-center justify-center p-4 text-center text-red-400 text-sm" role="alert">
        {text}
      </div>
    );
  }
  if (!query.data) {
    const pct = progress?.total ? Math.round((progress.read / progress.total) * 100) : null;
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 text-bambu-gray text-sm" role="status">
        <span className="flex items-center gap-2">
          <Loader2 className="w-5 h-5 animate-spin" /> {labels.loading}
          {pct != null && ` ${pct}%`}
        </span>
        {pct != null && (
          <span className="block w-48 h-1 rounded bg-bambu-dark-tertiary overflow-hidden">
            <span className="block h-full bg-bambu-green" style={{ width: `${pct}%` }} />
          </span>
        )}
      </div>
    );
  }
  return <Viewer tp={query.data} bed={bed} colours={colours} o={o} phaseTitle={phaseTitle} labels={labels} notice={notice} />;
}

function Viewer({ tp, bed, colours: projectColours, o, phaseTitle, labels, notice }: Omit<Props, 'url'> & { tp: Toolpaths }) {
  const host = useRef<HTMLDivElement>(null);
  const [three, setThree] = useState<{ view: ViewerScene; paths: ToolpathScene; render: () => void } | null>(null);
  const layers = tp.layerStart.length - 1;
  const [view, setViewType] = useState<ViewType>(() => defaultViewType(tp));
  const [roles, setRoles] = useState<boolean[]>(() => ROLES.map(() => true));
  const [options, setOptions] = useState(OPTION_DEFAULTS);
  const [folded, setFolded] = useState(false);
  const [sl, dispatch] = useReducer(useMemo(() => sliderReducer(layers), [layers]), layers, initialSliders);
  const { lo, hi, oneLayer } = sl;
  // a new plate's G-code: back to all its layers
  useEffect(() => dispatch({ type: 'reset' }), [tp]);
  const [jump, setJump] = useState(false);
  const colours = useMemo(() => {
    const own = (tp.config.filament_colour ?? '').split(';').map((c) => c.trim()).filter(Boolean);
    return projectColours.length ? projectColours : own;
  }, [projectColours, tp]);

  // three.js: scene, camera, controls, bed and the toolpaths, built once per G-code
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1a1a);
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.domElement.tabIndex = 0;
    renderer.domElement.dataset.previewCanvas = '';
    renderer.domElement.className = 'block w-full h-full outline-none';
    el.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.PAN };
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const light = new THREE.DirectionalLight(0xffffff, 0.9);
    light.position.set(0.3, 1, 0.6);
    camera.add(light);
    scene.add(camera);
    // the bed: printable_area when the G-code has it, else the printer's size
    const area = (tp.config.printable_area ?? '').split(',').map((p) => p.split('x').map(Number)).filter((p) => p.length === 2 && p.every(Number.isFinite));
    const w = area.length ? Math.max(...area.map((p) => p[0])) : bed.x;
    const d = area.length ? Math.max(...area.map((p) => p[1])) : bed.y;
    const plate = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshBasicMaterial({ color: 0x2b2b30 }));
    plate.rotation.x = -Math.PI / 2;
    plate.position.set(w / 2, -0.05, d / 2);
    const grid = new THREE.GridHelper(Math.max(w, d), Math.round(Math.max(w, d) / 10), 0x3e3e45, 0x34343a);
    grid.scale.set(w / Math.max(w, d), 1, d / Math.max(w, d));
    grid.position.set(w / 2, -0.02, d / 2);
    scene.add(plate, grid);
    const paths = new ToolpathScene(tp, d);
    scene.add(paths.group);
    const v: ViewerScene = { scene, camera, controls, renderer, model: new THREE.Group(), grid, bed: { x: w, y: d, z: bed.z } };
    const render = () => renderer.render(scene, camera);
    controls.addEventListener('change', render);
    const resize = () => {
      const { clientWidth, clientHeight } = el;
      if (!clientWidth || !clientHeight) return;
      renderer.setSize(clientWidth, clientHeight, false);
      camera.aspect = clientWidth / clientHeight;
      camera.updateProjectionMatrix();
      render();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    resize();
    setView(v, 'iso');
    setThree({ view: v, paths, render });
    return () => {
      observer.disconnect();
      controls.dispose();
      paths.dispose();
      plate.geometry.dispose();
      (plate.material as THREE.Material).dispose();
      grid.dispose();
      renderer.dispose();
      renderer.forceContextLoss(); // free the GPU memory now, not when the browser gets round to it
      renderer.domElement.remove();
      setThree(null);
    };
  }, [tp, bed.x, bed.y, bed.z]);

  const warnings = useMemo(() => sliceWarnings(tp), [tp]);
  const roleMask = useMemo(() => roles.reduce((m, on, r) => (on ? m | (1 << r) : m), 0), [roles]);
  const range = useMemo(
    () => (VIEW_TYPES.some((t) => t.key === view && 'title' in t) ? rangeOf(tp, view as RangeView, roles, options) : null),
    [tp, view, roles, options],
  );
  useEffect(() => {
    if (!three) return;
    paint(tp, view, range, colours, three.paths.colors);
    three.paths.colorsChanged();
    three.render();
  }, [three, tp, view, range, colours]);

  // The move slider: the visible moves of the top layer shown (GCodeViewer::update_moves_slider)
  const movesOf = (layer: number) => visibleMoves(tp, layer, roles, options);
  const moves = useMemo(() => visibleMoves(tp, hi, roles, options), [tp, hi, roles, options]);
  const moveIndex = sl.move == null ? moves.length - 1 : Math.min(sl.move, moves.length - 1);
  const setMove = (index: number | null) => dispatch({ type: 'moveTo', index });
  const cut = moves.length ? moves[Math.max(0, moveIndex)] : tp.layerStart[hi + 1] - 1;

  useEffect(() => {
    if (!three) return;
    three.paths.show(tp.layerStart[lo], cut, roleMask, options);
    three.render();
  }, [three, tp, lo, cut, roleMask, options]);

  const stepActive = (delta: number) => dispatch({ type: 'step', delta });
  const stepMove = (delta: number) => dispatch({ type: 'move', delta, moves: (l) => movesOf(l).length });
  const toggleOneLayer = () => dispatch({ type: 'oneLayer' });

  // OrcaSlicer's preview keys (Shortcuts.cpp, GLCanvas3D::on_char / on_key) while the canvas or a slider has focus
  const onKey = (e: React.KeyboardEvent) => {
    const el = e.target as HTMLElement;
    // the move slider (a range input) and the layer thumbs take Orca's keys too; text fields and menus don't
    if (jump || el.closest('select, textarea, input:not([type="range"]), [role="dialog"], [role="menu"]')) return;
    const ctrl = e.ctrlKey || e.metaKey;
    const n = e.shiftKey || ctrl ? 5 : 1;
    const zoom = (k: number) => {
      if (!three) return;
      const { camera, controls } = three.view;
      const d = camera.position.clone().sub(controls.target);
      camera.position.copy(controls.target).addScaledVector(d, k);
      controls.update();
    };
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    const action: (() => void) | undefined =
      ctrl && e.key in VIEW_KEYS
        ? () => three && setView(three.view, VIEW_KEYS[e.key] === 'plate' ? 'iso' : (VIEW_KEYS[e.key] as Exclude<(typeof VIEW_KEYS)[string], 'plate'>))
        : ctrl
          ? undefined
          : {
              ArrowUp: () => stepActive(n),
              ArrowDown: () => stepActive(-n),
              ArrowRight: () => stepMove(n),
              ArrowLeft: () => stepMove(-n),
              Home: () => setMove(0),
              End: () => setMove(null),
              l: toggleOneLayer,
              g: e.shiftKey ? () => setJump(true) : undefined,
              i: () => zoom(0.8),
              o: () => zoom(1.25),
            }[key];
    if (action) {
      e.preventDefault();
      e.stopPropagation();
      action();
    }
  };
  return (
    <div className="relative h-full" onKeyDown={onKey}>
      <div ref={host} className="absolute inset-0" onPointerDown={() => three?.view.renderer.domElement.focus({ preventScroll: true })} />
      {notice}
      {/* the slice's warnings (Orca's notifications, bottom right above the move slider) */}
      {warnings.length > 0 && (
        <div className="absolute right-16 bottom-10 z-10 flex max-w-sm flex-col items-end gap-1" role="alert">
          {warnings.map((w) => (
            <p
              key={w.key}
              className={`whitespace-pre-line rounded border px-2 py-1 text-xs shadow ${
                w.error ? 'border-red-500/70 bg-red-950/90 text-red-200' : 'border-amber-500/70 bg-amber-950/90 text-amber-200'
              }`}
            >
              {warningText(w, o)}
            </p>
          ))}
        </div>
      )}
      <LegendPanel
        tp={tp}
        view={view}
        onView={(v) => {
          setViewType(v);
          setFolded(false);
          if (v === 'feature') setRoles(ROLES.map(() => true)); // reset_visible
          setMove(null);
        }}
        range={range}
        roles={roles}
        onRole={(r) => {
          setRoles((rs) => rs.map((on, i) => (i === r ? !on : on)));
          setMove(null);
        }}
        options={options}
        onOption={(k) => {
          setOptions((op) => ({ ...op, [k]: !op[k] }));
          setMove(null);
        }}
        colours={colours}
        folded={folded}
        onFold={() => setFolded((f) => !f)}
        o={o}
        phaseTitle={phaseTitle}
      />
      <LayerSlider
        tp={tp}
        lo={lo}
        hi={hi}
        active={oneLayer ? 'hi' : sl.active}
        oneLayer={oneLayer}
        onActive={(which) => dispatch({ type: 'active', which })}
        onChange={(l, h) => dispatch({ type: 'set', lo: l, hi: h })}
        onStep={stepActive}
        onOneLayer={toggleOneLayer}
        onJump={() => setJump(true)}
        o={o}
        phaseTitle={phaseTitle}
        labels={labels}
        filaments={tp.stats.usedExtruders.length > 1 ? colours : []}
      />
      <div className="absolute left-2 right-16 bottom-2 flex items-center gap-2 text-xs text-bambu-gray-light">
        <input
          type="range"
          min={0}
          max={Math.max(0, moves.length - 1)}
          value={Math.max(0, moveIndex)}
          disabled={moves.length === 0}
          onChange={(e) => setMove(Number(e.target.value))}
          onWheel={(e) => stepMove((e.deltaY < 0 ? 1 : -1) * (e.shiftKey || e.ctrlKey ? 5 : 1))}
          aria-label={labels.move}
          className="flex-1 accent-bambu-green cursor-pointer disabled:opacity-40"
        />
        <span className="min-w-[64px] text-right tabular-nums" title="G-code">
          {moves.length ? tp.line[cut] : ''}
        </span>
      </div>
      {jump && (
        <JumpDialog
          o={o}
          max={layers}
          onCancel={() => {
            setJump(false);
            three?.view.renderer.domElement.focus({ preventScroll: true });
          }}
          onOk={(layer) => {
            setJump(false);
            dispatch({ type: 'jump', layer: layer - 1 });
            three?.view.renderer.domElement.focus({ preventScroll: true });
          }}
        />
      )}
    </div>
  );
}

/** IMSlider's vertical slider: two thumbs (the lowest and highest layer shown), the active one with a ring,
 * labels with the layer number and its Z, the estimated time where the pointer hovers, the wheel, and the
 * right-click menu on the active thumb. */
function LayerSlider({
  tp, lo, hi, active, oneLayer, onActive, onChange, onStep, onOneLayer, onJump, o, phaseTitle, labels, filaments,
}: {
  tp: Toolpaths;
  lo: number;
  hi: number;
  active: 'lo' | 'hi';
  oneLayer: boolean;
  onActive: (a: 'lo' | 'hi') => void;
  onChange: (lo: number, hi: number) => void;
  onStep: (delta: number) => void;
  onOneLayer: () => void;
  onJump: () => void;
  o: (text: string) => string;
  phaseTitle: (phase: number) => string;
  labels: PreviewLabels;
  filaments: string[];
}) {
  const track = useRef<HTMLDivElement>(null);
  const layers = tp.layerStart.length - 1;
  const [hover, setHover] = useState<number | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const drag = useRef<'lo' | 'hi' | null>(null);
  // the time printed up to the end of each layer, for the hover tooltip
  const elapsed = useMemo(() => {
    let t = 0;
    return tp.layerTime.map((lt) => (t += lt));
  }, [tp]);
  const top = (v: number) => `${layers > 1 ? (1 - v / (layers - 1)) * 100 : 0}%`;
  const valueAt = (clientY: number) => {
    const r = track.current!.getBoundingClientRect();
    const f = r.height > 0 ? 1 - (clientY - r.top) / r.height : 1; // a hidden track has no height
    return Math.round(Math.max(0, Math.min(1, f)) * (layers - 1));
  };
  const set = (which: 'lo' | 'hi', v: number) => {
    if (oneLayer) onChange(v, v);
    else if (which === 'hi') onChange(lo, Math.max(v, lo));
    else onChange(Math.min(v, hi), hi);
  };
  const onWheel = (e: React.WheelEvent) => {
    onStep((e.deltaY < 0 ? 1 : -1) * (e.shiftKey || e.ctrlKey ? 5 : 1));
  };
  const label = (v: number) => (
    <>
      <span>{v + 1}</span>
      <span className="text-bambu-gray-light">{(tp.layerZ[v] ?? 0).toFixed(2)}</span>
    </>
  );
  const thumb = (which: 'lo' | 'hi', v: number) => (
    <div
      key={which}
      role="slider"
      tabIndex={0}
      aria-label={which === 'hi' ? labels.upperLayer : labels.lowerLayer}
      aria-orientation="vertical"
      aria-valuemin={1}
      aria-valuemax={layers}
      aria-valuenow={v + 1}
      aria-valuetext={labels.layerText(v + 1, (tp.layerZ[v] ?? 0).toFixed(2))}
      onFocus={() => onActive(which)}
      onKeyDown={(e) => {
        // the keys act on the thumb that has the focus (the arrows then go on to the preview's keys)
        onActive(which);
        // Home / End take this thumb to the first / last layer (elsewhere they move the move slider)
        if (e.key !== 'Home' && e.key !== 'End') return;
        e.preventDefault();
        e.stopPropagation();
        onActive(which);
        set(which, e.key === 'Home' ? 0 : layers - 1);
      }}
      onPointerDown={(e) => {
        e.stopPropagation();
        if (e.button !== 0) return;
        onActive(which);
        drag.current = which;
        (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        if (which === active) setMenu({ x: e.clientX, y: e.clientY });
      }}
      className="absolute left-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center cursor-grab"
      style={{ top: top(v) }}
    >
      <span
        className={`block w-3.5 h-3.5 rounded-full bg-bambu-green border-2 ${which === active ? 'border-white ring-2 ring-bambu-green/50' : 'border-bambu-dark'}`}
      />
      <span className="absolute right-5 flex flex-col items-end rounded bg-bambu-dark-secondary/95 px-1.5 py-0.5 leading-tight tabular-nums text-white">
        {label(v)}
      </span>
    </div>
  );
  return (
    <div className="absolute right-3 top-[10%] bottom-[12%] w-10 flex flex-col items-center text-xs select-none">
      <div
        ref={track}
        className="relative flex-1 w-full cursor-pointer"
        onWheel={onWheel}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          set(active, valueAt(e.clientY));
        }}
        onPointerMove={(e) => {
          setHover(valueAt(e.clientY));
          if (drag.current) set(drag.current, valueAt(e.clientY));
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerLeave={() => setHover(null)}
        data-testid="layer-slider"
      >
        <div className="absolute left-1/2 -translate-x-1/2 top-0 bottom-0 w-1 rounded bg-bambu-dark-tertiary" />
        <div className="absolute left-1/2 -translate-x-1/2 w-1 rounded bg-bambu-green" style={{ top: top(hi), bottom: `${layers > 1 ? (lo / (layers - 1)) * 100 : 0}%` }} />
        {hover != null && (
          <>
            <div className="absolute left-0 right-0 h-px bg-white/40 pointer-events-none" style={{ top: top(hover) }} />
            <span className="absolute right-full mr-14 -translate-y-1/2 whitespace-nowrap rounded bg-black/80 px-1.5 py-0.5 text-white pointer-events-none" style={{ top: top(hover) }}>
              {shortTime(elapsed[hover] ?? 0)}
            </span>
          </>
        )}
        {!oneLayer && thumb('lo', lo)}
        {thumb('hi', hi)}
      </div>
      <button
        type="button"
        onClick={onOneLayer}
        aria-pressed={oneLayer}
        title={`${labels.oneLayer} [L]`}
        aria-label={labels.oneLayer}
        className={`mt-2 p-1 rounded ${oneLayer ? 'bg-bambu-green text-white' : 'bg-bambu-dark-secondary text-bambu-gray-light hover:text-white'}`}
      >
        <Square className="w-3.5 h-3.5" />
      </button>
      {menu && (
        <SliderMenu
          at={menu}
          onClose={() => setMenu(null)}
          o={o}
          phaseTitle={phaseTitle}
          filaments={filaments}
          onJump={() => {
            setMenu(null);
            onJump();
          }}
        />
      )}
    </div>
  );
}

/** The layer slider's right-click menu (IMSlider::render_add_menu): only "Jump to layer" works for now; adding
 * pauses, custom G-code and filament changes writes the project, which is phase 2. */
function SliderMenu({ at, onClose, o, phaseTitle, filaments, onJump }: {
  at: { x: number; y: number };
  onClose: () => void;
  o: (t: string) => string;
  phaseTitle: (phase: number) => string;
  filaments: string[];
  onJump: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (e: PointerEvent) => !ref.current?.contains(e.target as Node) && onClose();
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('pointerdown', away);
    window.addEventListener('keydown', esc);
    return () => {
      window.removeEventListener('pointerdown', away);
      window.removeEventListener('keydown', esc);
    };
  }, [onClose]);
  const item = 'block w-full text-left px-3 py-1.5 text-xs whitespace-nowrap';
  return (
    <div ref={ref} role="menu" className="fixed z-50 min-w-[180px] rounded-md bg-bambu-dark-secondary border border-bambu-dark-tertiary py-1 shadow-xl text-white" style={{ left: at.x - 190, top: at.y }}>
      <button type="button" role="menuitem" disabled className={`${item} text-bambu-gray`} title={`${o('Insert a pause command at the beginning of this layer.')} (${phaseTitle(2)})`}>
        {o('Add Pause')}
      </button>
      <button type="button" role="menuitem" disabled className={`${item} text-bambu-gray`} title={`${o('Insert custom G-code at the beginning of this layer.')} (${phaseTitle(2)})`}>
        {o('Add Custom G-code')}
      </button>
      <button type="button" role="menuitem" className={`${item} hover:bg-white/10`} onClick={onJump}>
        {o('Jump to layer')}
      </button>
      {filaments.length > 1 && (
        <button type="button" role="menuitem" disabled className={`${item} text-bambu-gray`} title={phaseTitle(2)}>
          {o('Change Filament')} ▸
        </button>
      )}
    </div>
  );
}

/** IMSlider::render_go_to_layer_dialog: a layer number between 1 and the last; OK only for a valid one. */
function JumpDialog({ o, max, onOk, onCancel }: { o: (t: string) => string; max: number; onOk: (layer: number) => void; onCancel: () => void }) {
  const [text, setText] = useState('');
  const n = Number(text);
  const valid = /^\d+$/.test(text.trim()) && n >= 1 && n <= max;
  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-black/30" onPointerDown={onCancel}>
      <div role="dialog" aria-label={o('Jump to layer')} className="rounded-md bg-bambu-dark-secondary border border-bambu-dark-tertiary p-3 text-sm text-white shadow-xl space-y-2" onPointerDown={(e) => e.stopPropagation()}>
        <p className="font-semibold">{o('Jump to layer')}</p>
        <p className="text-xs">{`${o('Please enter the layer number.')} (1 - ${max}):`}</p>
        <input
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === 'Enter' && valid) onOk(n);
            if (e.key === 'Escape') onCancel();
          }}
          inputMode="numeric"
          className="w-full rounded bg-bambu-dark px-2 py-1 border border-bambu-dark-tertiary"
        />
        <div className="flex justify-end gap-2">
          <button type="button" disabled={!valid} onClick={() => onOk(n)} className="rounded bg-bambu-green px-3 py-1 text-xs disabled:opacity-40">
            {o('OK')}
          </button>
          <button type="button" onClick={onCancel} className="rounded bg-bambu-dark-tertiary px-3 py-1 text-xs">
            {o('Cancel')}
          </button>
        </div>
      </div>
    </div>
  );
}

/** The moves the move slider steps through on a layer: extrusions of the roles shown and the options shown. */
function visibleMoves(tp: Toolpaths, layer: number, roles: boolean[], options: Record<OptionKey, boolean>): number[] {
  const out: number[] = [];
  for (let i = tp.layerStart[layer]; i < tp.layerStart[layer + 1]; i++) {
    const t = tp.type[i];
    const shown =
      t === MOVE.extrude
        ? roles[tp.role[i]]
        : (t === MOVE.travel && options.travel) || (t === MOVE.wipe && options.wipe) || (t === MOVE.retract && options.retract) ||
          (t === MOVE.unretract && options.unretract) || (t === MOVE.seam && options.seam);
    if (shown) out.push(i);
  }
  return out;
}
