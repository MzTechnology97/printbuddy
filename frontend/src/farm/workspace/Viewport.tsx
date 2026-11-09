// Fork (printfarm): around the 3D view, as OrcaSlicer's Prepare view (v2.5.0): its toolbar on top (the main
// tools, then the object tools, in its order with its icons and tooltips), the canvas buttons bottom left
// (section, view menu, zoom), the plate's own buttons on the right, the object info box bottom right, the
// right-click menus, and the numbered plate bar. What only looks works now (views, zoom, grid, measure, info,
// selection); what would change the geometry or the plates is shown disabled, "phase 2" (it needs a
// model_settings.config writer, see docs/plans/3mf-workspace.md).
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronRight, X } from 'lucide-react';
import { api } from '../../api/client';
import type { WorkspacePlate } from './api';
import { toLocaleDecimal } from './draft';
import { type OrcaLayout, orcaFormat, orcaIcon, orcaPlural, orcaText } from './orcaLayout';
import { type CanvasOptions, type ObjectStats, type Vec3, type ViewPreset, navigatorView } from './viewTools';
import '../i18n';

/** An OrcaSlicer string (its Italian when the UI is Italian). */
function useOrcaText(layout: OrcaLayout) {
  const { i18n } = useTranslation();
  return (text: string) => orcaText(layout, text, i18n.language);
}

interface Tool {
  key: string;
  icon: string;
  label: string;
  shortcut?: string;
}

// GLCanvas3D::_init_main_toolbar, then GLGizmosManager's gizmos (the colour painting one shows with more than
// one filament), then the assembly view toolbar
const MAIN_TOOLS: Tool[][] = [
  [
    { key: 'add', icon: 'toolbar_open_dark', label: 'Add', shortcut: 'Ctrl+I' },
    { key: 'addplate', icon: 'toolbar_add_plate_dark', label: 'Add plate' },
    { key: 'orient', icon: 'toolbar_orient_dark', label: 'Auto orientation', shortcut: 'Q' },
    { key: 'arrange', icon: 'toolbar_arrange_dark', label: 'Arrange all objects', shortcut: 'A' },
  ],
  [
    { key: 'more', icon: 'instance_add_dark', label: 'Add instance', shortcut: '+' },
    { key: 'fewer', icon: 'instance_remove_dark', label: 'Remove instance', shortcut: '-' },
    { key: 'splitobjects', icon: 'split_objects_dark', label: 'Split to objects' },
    { key: 'splitvolumes', icon: 'split_parts_dark', label: 'Split to parts' },
    { key: 'layersediting', icon: 'toolbar_variable_layer_height_dark', label: 'Variable layer height' },
  ],
  [
    { key: 'move', icon: 'toolbar_move_dark', label: 'Move', shortcut: 'M' },
    { key: 'rotate', icon: 'toolbar_rotate_dark', label: 'Rotate', shortcut: 'R' },
    { key: 'scale', icon: 'toolbar_scale_dark', label: 'Scale', shortcut: 'S' },
    { key: 'flatten', icon: 'toolbar_flatten_dark', label: 'Lay on Face', shortcut: 'F' },
    { key: 'cut', icon: 'toolbar_cut_dark', label: 'Cut', shortcut: 'C' },
    { key: 'meshboolean', icon: 'toolbar_meshboolean_dark', label: 'Mesh Boolean', shortcut: 'B' },
    { key: 'support', icon: 'toolbar_support_dark', label: 'Support Painting', shortcut: 'L' },
    { key: 'seam', icon: 'toolbar_seam_dark', label: 'Seam painting', shortcut: 'P' },
    { key: 'fuzzy', icon: 'toolbar_fuzzy_skin_paint_dark', label: 'Paint-on fuzzy skin', shortcut: 'H' },
    { key: 'texture', icon: 'toolbar_texture_displacement', label: 'Texture displacement', shortcut: 'D' },
    { key: 'emboss', icon: 'toolbar_text_dark', label: 'Emboss', shortcut: 'T' },
    { key: 'measure', icon: 'toolbar_measure_dark', label: 'Measure', shortcut: 'U' },
    { key: 'assemble', icon: 'toolbar_assembly_dark', label: 'Assemble', shortcut: 'Y' },
    { key: 'brimears', icon: 'toolbar_brimears_dark', label: 'Brim Ears', shortcut: 'E' },
  ],
  [{ key: 'assembly_view', icon: 'toolbar_assemble_dark', label: 'Assembly View' }],
];
/** The tools that work now: they only look. */
export const WORKING_TOOLS = new Set(['measure']);

// where a button's tooltip shows: under the top toolbar, beside the canvas (left) and plate (right) buttons
const TIP_PLACE = {
  below: 'top-full left-1/2 -translate-x-1/2 mt-1',
  left: 'right-full top-1/2 -translate-y-1/2 mr-2',
  right: 'left-full top-1/2 -translate-y-1/2 ml-2',
};

/** A tooltip shown as soon as the pointer is over its wrapper (``group/tip``), as OrcaSlicer's over its icons. */
function Tip({ text, place }: { text: string; place: keyof typeof TIP_PLACE }) {
  return (
    <span
      role="tooltip"
      className={`pointer-events-none absolute z-[60] hidden group-hover/tip:block whitespace-nowrap rounded bg-black/85 px-2 py-1 text-[12px] text-white shadow-lg ${TIP_PLACE[place]}`}
    >
      {text}
    </span>
  );
}

function ToolButton({
  icon,
  label,
  enabled,
  active,
  onClick,
  size = 'w-7 h-7',
  tip = 'below',
}: {
  icon: string;
  label: string;
  enabled: boolean;
  active?: boolean;
  onClick?: () => void;
  size?: string;
  /** null: no tooltip (its panel is open beside it) */
  tip?: keyof typeof TIP_PLACE | null;
}) {
  const { t } = useTranslation();
  const title = enabled ? label : `${label}: ${t('farm.workspace.gizmos.soon')}`;
  return (
    // a disabled button takes no pointer: its wrapper gets the hover and shows the tooltip
    <span className="relative inline-flex group/tip">
      <button
        type="button"
        disabled={!enabled}
        aria-label={title}
        aria-pressed={active ?? undefined}
        onClick={onClick}
        className={`p-1 rounded ${enabled ? 'hover:bg-bambu-dark-tertiary' : 'opacity-60 cursor-not-allowed pointer-events-none'} ${
          active ? 'bg-bambu-green/30 ring-1 ring-bambu-green' : ''
        }`}
      >
        <img src={orcaIcon(icon)} alt="" className={size} aria-hidden />
      </button>
      {tip && <Tip text={title} place={tip} />}
    </span>
  );
}

export function MainToolbar({ layout, active, onTool }: { layout: OrcaLayout; active: string | null; onTool: (key: string) => void }) {
  const { t } = useTranslation();
  const tr = useOrcaText(layout);
  return (
    <div
      className="absolute top-0 left-1/2 -translate-x-1/2 max-w-full overflow-x-auto flex items-center gap-0.5 rounded-b-md bg-bambu-dark-secondary/95 px-2 py-1 border border-t-0 border-bambu-dark-tertiary"
      role="toolbar"
      aria-label={t('farm.workspace.gizmos.title')}
    >
      {MAIN_TOOLS.map((group, g) => (
        <div key={g} className="flex items-center gap-0.5">
          {g > 0 && <span className="mx-1 h-6 w-px bg-bambu-dark-tertiary" aria-hidden />}
          {group.map((tool) => (
            <ToolButton
              key={tool.key}
              icon={tool.icon}
              label={`${tr(tool.label)}${tool.shortcut ? ` [${tool.shortcut}]` : ''}`}
              enabled={WORKING_TOOLS.has(tool.key)}
              active={WORKING_TOOLS.has(tool.key) ? active === tool.key : undefined}
              onClick={() => onTool(tool.key)}
              size="w-9 h-9"
            />
          ))}
        </div>
      ))}
    </div>
  );
}

const CANVAS_MENU: { key: keyof CanvasOptions; label: string; shortcut?: string }[] = [
  { key: 'navigator', label: '3D Navigator' },
  { key: 'zoomButton', label: 'Zoom button' },
  { key: 'overhangs', label: 'Overhangs' },
  { key: 'outline', label: 'Outline' },
  { key: 'wireframe', label: 'Wireframe', shortcut: 'Ctrl+Shift+Enter' },
  { key: 'xray', label: 'X-Ray' },
  { key: 'realistic', label: 'Realistic View' },
  { key: 'perspective', label: 'Perspective' },
  { key: 'axes', label: 'Axes' },
  { key: 'grid', label: 'Gridlines' },
  { key: 'focus', label: 'Focus mode' },
  { key: 'labels', label: 'Labels', shortcut: 'Ctrl+E' },
  { key: 'centerOfMass', label: 'Center of mass' },
];

/** Bottom left of the 3D view, as GLCanvas3D's canvas toolbar: section view, the canvas menu, zoom to fit. */
export function CanvasButtons({
  layout,
  options,
  onOption,
  onFit,
  section,
  onSection,
  onToggleSection,
  onSectionAngle,
}: {
  layout: OrcaLayout;
  options: CanvasOptions;
  onOption: (key: keyof CanvasOptions, on: boolean) => void;
  onFit: () => void;
  /** where the section plane is: 0 no section, up to 1 all cut (from the camera's side) */
  section: number;
  onSection: (ratio: number) => void;
  /** off, or back to the last cut (half way the first time) */
  onToggleSection: () => void;
  /** the plane faces the camera */
  onSectionAngle: () => void;
}) {
  const tr = useOrcaText(layout);
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState(false);
  const tips: [string, string][] = [
    [`Alt+${tr('Mouse wheel')}`, tr('Move section plane')],
    [tr('Mouse wheel on icon'), tr('Move section plane')],
    [tr('Right click on icon'), tr('Toggle section view')],
    [tr('Middle click on icon'), tr('Set viewing angle')],
  ];
  const items: MenuItem[] = CANVAS_MENU.map((m) => ({
    label: tr(m.label),
    shortcut: m.shortcut,
    checked: options[m.key],
    onClick: () => onOption(m.key, !options[m.key]),
  }));
  return (
    <div className="absolute left-3 bottom-24 flex flex-col gap-2 [&>span>span>button]:rounded-full [&>span>span>button]:bg-bambu-dark-secondary [&>span>span>button]:p-1.5">
      <span
        className="relative inline-flex"
        onWheel={(e) => onSection(Math.min(1, Math.max(0, section + (e.deltaY > 0 ? -0.01 : 0.01))))}
        onContextMenu={(e) => {
          e.preventDefault();
          onToggleSection();
        }}
        onMouseDown={(e) => {
          if (e.button !== 1) return;
          e.preventDefault(); // no autoscroll
          onSectionAngle();
        }}
      >
        <ToolButton
          icon="canvas_section_dark"
          label={tr('Section view')}
          enabled
          active={section > 0}
          size="w-5 h-5"
          tip={panel ? null : 'right'}
          onClick={() => setPanel(!panel)}
        />
        {panel && (
          <div
            className="absolute left-full bottom-0 ml-2 flex items-center gap-2 whitespace-nowrap rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary px-3 py-2 text-[12px] text-white shadow-xl"
            role="group"
            aria-label={tr('Section view')}
            onWheel={(e) => e.stopPropagation()}
            onContextMenu={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <span className="relative inline-flex group/tip">
              <img src={orcaIcon('help')} alt="?" className="w-4 h-4 max-w-none cursor-help invert" tabIndex={0} />
              <span
                role="tooltip"
                className="pointer-events-none absolute bottom-full left-0 mb-2 hidden group-hover/tip:grid group-focus-within/tip:grid grid-cols-[auto_auto] gap-x-4 gap-y-0.5 rounded bg-black/90 px-3 py-2 shadow-lg"
              >
                {tips.map(([key, what]) => (
                  <span key={key} className="contents">
                    <span className="font-semibold">{key}</span>
                    <span>{what}</span>
                  </span>
                ))}
              </span>
            </span>
            <span>{tr('Section view')}</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={section}
              onChange={(e) => onSection(Number(e.target.value))}
              aria-label={tr('Section view')}
              className="w-28 accent-bambu-green"
            />
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={Number(section.toFixed(2))}
              onChange={(e) => e.target.value !== '' && onSection(Math.min(1, Math.max(0, Number(e.target.value))))}
              aria-label={`${tr('Section view')} (0-1)`}
              className="w-14 rounded bg-bambu-dark border border-bambu-dark-tertiary px-1 tabular-nums"
            />
            <button onClick={onSectionAngle} className="rounded bg-bambu-dark-tertiary px-2 py-0.5 hover:bg-bambu-green/40">
              {tr('Set viewing angle')}
            </button>
            <button
              onClick={() => onSection(0)}
              disabled={section === 0}
              aria-label={tr('Reset')}
              title={tr('Reset')}
              className="rounded px-1 text-[14px] leading-none hover:bg-bambu-green/40 disabled:opacity-40"
            >
              ↺
            </button>
          </div>
        )}
      </span>
      <span className="relative inline-flex">
        <ToolButton icon="canvas_menu_dark" label={tr('Gridlines')} enabled active={open} size="w-5 h-5" tip="right" onClick={() => setOpen(!open)} />
        {open && <ContextMenu items={items} onClose={() => setOpen(false)} />}
      </span>
      {options.zoomButton && (
        <span className="inline-flex">
          <ToolButton
            icon="canvas_zoom_dark"
            label={`${tr('Fit camera to scene or selected object.')} [Z]`}
            enabled
            size="w-5 h-5"
            tip="right"
            onClick={onFit}
          />
        </span>
      )}
    </div>
  );
}

const FACES: { key: ViewPreset; label: string; transform: string }[] = [
  { key: 'front', label: 'front', transform: 'translateZ(28px)' },
  { key: 'rear', label: 'rear', transform: 'rotateY(180deg) translateZ(28px)' },
  { key: 'right', label: 'right', transform: 'rotateY(90deg) translateZ(28px)' },
  { key: 'left', label: 'left', transform: 'rotateY(-90deg) translateZ(28px)' },
  { key: 'top', label: 'top', transform: 'rotateX(90deg) translateZ(28px)' },
  { key: 'bottom', label: 'bottom', transform: 'rotateX(-90deg) translateZ(28px)' },
];

/** OrcaSlicer's 3D navigator: a cube turning with the camera; a click on a face looks from that side (on an
 * edge or a corner, from between), dragging it turns the view. */
export function Navigator({
  direction,
  onView,
  onOrbit,
}: {
  direction: () => { x: number; y: number; z: number } | null;
  onView: (p: ViewPreset | Vec3) => void;
  /** turn the view by a drag of ``dx``, ``dy`` (radians) */
  onOrbit: (dx: number, dy: number) => void;
}) {
  const { t } = useTranslation();
  const cube = useRef<HTMLDivElement>(null);
  const read = useRef(direction);
  read.current = direction;
  const orbitRef = useRef(onOrbit);
  orbitRef.current = onOrbit;
  const dragged = useRef(false);
  const startDrag = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const start = { x: e.clientX, y: e.clientY };
    let last = start;
    dragged.current = false;
    const move = (ev: PointerEvent) => {
      if (!dragged.current && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 3) return;
      dragged.current = true;
      orbitRef.current((ev.clientX - last.x) * 0.01, (ev.clientY - last.y) * 0.01);
      last = { x: ev.clientX, y: ev.clientY };
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  useEffect(() => {
    let frame = 0;
    let last = '';
    const turn = () => {
      frame = requestAnimationFrame(turn);
      // nothing to do while the view is hidden (the phone's other pane) or the camera hasn't moved
      if (!cube.current || cube.current.offsetParent === null) return;
      const d = read.current();
      if (!d) return;
      const azimuth = (Math.atan2(d.x, d.z) * 180) / Math.PI;
      const elevation = (Math.asin(Math.max(-1, Math.min(1, d.y))) * 180) / Math.PI;
      const transform = `rotateX(${(-elevation).toFixed(2)}deg) rotateY(${(-azimuth).toFixed(2)}deg)`;
      if (transform !== last) cube.current.style.transform = last = transform;
    };
    turn();
    return () => cancelAnimationFrame(frame);
  }, []);
  return (
    <div
      className="absolute left-3 bottom-[13.5rem] w-14 h-14 cursor-grab active:cursor-grabbing touch-none"
      style={{ perspective: 300 }}
      role="group"
      aria-label={t('farm.workspace.navigator.title')}
      onPointerDown={startDrag}
    >
      <div ref={cube} className="relative w-full h-full" style={{ transformStyle: 'preserve-3d' }}>
        {FACES.map((f) => (
          <button
            key={f.key}
            onClick={(e) => {
              if (dragged.current) return; // the end of a drag, not a click
              const el = e.currentTarget;
              // the keyboard (detail 0) picks the face; the pointer, where on the face (offsetX is in its own plane)
              onView(e.detail === 0 ? f.key : navigatorView(f.key, e.nativeEvent.offsetX / el.offsetWidth, e.nativeEvent.offsetY / el.offsetHeight));
            }}
            title={t(`farm.workspace.navigator.${f.label}`)}
            className="absolute inset-0 flex items-center justify-center border border-bambu-gray/60 bg-bambu-dark-tertiary/90 text-[8px] text-white hover:bg-bambu-green/60"
            style={{ transform: f.transform, backfaceVisibility: 'hidden' }}
          >
            {t(`farm.workspace.navigator.${f.label}`)}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Object names over the 3D view (canvas menu "Labels", Ctrl+E), following the camera. */
export function Labels({ place }: { place: () => { id: string; name: string; x: number; y: number }[] }) {
  const [shown, setShown] = useState<{ id: string; name: string; x: number; y: number }[]>([]);
  const read = useRef(place);
  read.current = place;
  useEffect(() => {
    let frame = 0;
    let last = '';
    const tick = () => {
      frame = requestAnimationFrame(tick);
      const now = read.current();
      // a new state (a render) only when a label moved
      const key = now.map((l) => `${l.id}:${Math.round(l.x)}:${Math.round(l.y)}:${l.name}`).join('|');
      if (key !== last) {
        last = key;
        setShown(now);
      }
    };
    tick();
    return () => cancelAnimationFrame(frame);
  }, []);
  return (
    <>
      {shown.map((l) => (
        <span
          key={l.id}
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded bg-black/70 px-1.5 py-0.5 text-[11px] text-white whitespace-nowrap"
          style={{ left: l.x, top: l.y }}
        >
          {l.name}
        </span>
      ))}
    </>
  );
}

// PartPlate.cpp: the buttons beside the plate, in its order
const PLATE_TOOLS = [
  { key: 'remove', icon: 'plate_close_dark', label: 'Remove current plate (if not last one)' },
  { key: 'orient', icon: 'plate_orient_dark', label: 'Auto orient objects on current plate' },
  { key: 'arrange', icon: 'plate_arrange_dark', label: 'Arrange objects on current plate' },
  { key: 'lock', icon: 'plate_unlocked_dark', label: 'Lock current plate' },
  { key: 'name', icon: 'plate_name_edit_dark', label: 'Edit current plate name' },
  { key: 'front', icon: 'plate_move_front_dark', label: 'Move plate to the front' },
  { key: 'settings', icon: 'plate_settings_dark', label: 'Customize current plate' },
];

/** The plate's own buttons; only "Customize current plate" works (it shows the plate's settings). */
export function PlateButtons({ layout, onSettings }: { layout: OrcaLayout; onSettings: () => void }) {
  const tr = useOrcaText(layout);
  return (
    <div className="absolute right-2 top-12 flex flex-col gap-1" role="toolbar" aria-label={tr('Plate')}>
      {PLATE_TOOLS.map((tool) => (
        <ToolButton
          key={tool.key}
          icon={tool.icon}
          label={tr(tool.label)}
          enabled={tool.key === 'settings'}
          tip="left"
          onClick={tool.key === 'settings' ? onSettings : undefined}
        />
      ))}
    </div>
  );
}

/** Bottom right, as OrcaSlicer's object info (Plater.cpp): with one instance or one part selected its name, size,
 * volume, triangles and mesh errors; with several only how many. */
export function ObjectInfo({
  layout,
  name,
  part,
  count = 1,
  stats,
  onClose,
}: {
  layout: OrcaLayout;
  name: string;
  /** the part selected, if one (its name replaces the object's) */
  part?: string | null;
  /** how many objects are selected (more than one: only that count) */
  count?: number;
  stats?: ObjectStats;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const tr = useOrcaText(layout);
  const n = (v: number) => toLocaleDecimal(String(Math.round(v * 100) / 100), i18n.language);
  const f = (text: string, ...values: (string | number)[]) => orcaFormat(layout, text, i18n.language, ...values);
  return (
    <div
      className="absolute right-2 bottom-20 w-72 rounded border border-bambu-dark-tertiary border-l-4 border-l-bambu-green bg-bambu-dark-secondary/95 px-3 py-1.5 text-[12px] text-bambu-gray-light"
      role="region"
      aria-label={tr('Info')}
    >
      <button onClick={onClose} aria-label={tr('Close')} title={tr('Close')} className="absolute right-1 top-1 p-0.5 text-bambu-gray hover:text-white">
        <X className="w-3.5 h-3.5" />
      </button>
      {count > 1 || !stats ? (
        <p>{f('Number of currently selected objects: %1%\n', count)}</p>
      ) : (
        <>
          <p>{part ? f('Part name: %1%\n', part) : f('Object name: %1%\n', name)}</p>
          <p>{f('Size: %1% x %2% x %3% mm\n', ...stats.size.map(n))}</p>
          <p>{f('Volume: %1% mm³\n', n(stats.volume))}</p>
          <p>{f('Triangles: %1%\n', stats.triangles)}</p>
          {stats.openEdges > 0 && (
            <div className="text-red-400">
              <p>{orcaPlural(layout, 'Error: %1$d non-manifold edge.', 'Error: %1$d non-manifold edges.', stats.openEdges, i18n.language)}</p>
              <p>{tr('Tips:')}</p>
              <p>{tr('Use "Fix Model" to repair the mesh.')}</p>
            </div>
          )}
          {!stats.printable && <p className="text-amber-400">{t('farm.workspace.notPrintable')}</p>}
        </>
      )}
    </div>
  );
}

export interface MenuItem {
  label: string;
  /** Shown on the right, as the slicer's accelerator. */
  shortcut?: string;
  /** A submenu arrow (its submenu comes with phase 2). */
  submenu?: boolean;
  checked?: boolean;
  icon?: string;
  /** Works when set; otherwise disabled, "phase 2". */
  onClick?: () => void;
  /** A line above it. */
  separator?: boolean;
}

/** A right-click menu at a point (``at``), or beside its button: arrows, Enter, Esc and a click outside close
 * it; disabled items say they come in phase 2. */
export function ContextMenu({ items, at, onClose }: { items: MenuItem[]; at?: { x: number; y: number }; onClose: () => void }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const [place, setPlace] = useState<React.CSSProperties>(
    at ? { position: 'fixed', left: at.x, top: at.y } : { position: 'absolute', left: '100%', bottom: 0, marginLeft: 6 },
  );
  const soon = t('farm.workspace.gizmos.soon');

  // keep it inside the window
  useLayoutEffect(() => {
    if (!at || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    setPlace({
      position: 'fixed',
      left: Math.max(4, Math.min(at.x, window.innerWidth - r.width - 4)),
      top: Math.max(4, Math.min(at.y, window.innerHeight - r.height - 4)),
    });
  }, [at]);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])')?.focus();
    const outside = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && close.current();
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && close.current();
    document.addEventListener('mousedown', outside);
    window.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', outside);
      window.removeEventListener('keydown', esc);
    };
  }, []);

  const run = (item: MenuItem) => {
    item.onClick?.();
    close.current();
  };
  const onKey = (e: React.KeyboardEvent) => {
    const enabled = [...(ref.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])') ?? [])];
    const i = enabled.indexOf(document.activeElement as HTMLElement);
    const next = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: enabled.length - 1 }[e.key];
    if (next != null && enabled.length) {
      e.preventDefault();
      enabled[(next + enabled.length) % enabled.length].focus();
    } else if (e.key === 'Escape') {
      e.stopPropagation(); // the menu, not a dialog behind it
      close.current();
    }
  };

  return (
    <div
      ref={ref}
      role="menu"
      style={place}
      onKeyDown={onKey}
      onContextMenu={(e) => e.preventDefault()}
      className="z-[70] min-w-[15rem] rounded border border-bambu-dark-tertiary bg-bambu-dark-secondary py-1 text-[12px] shadow-xl"
    >
      {items.map((item, i) => {
        const enabled = !!item.onClick;
        return (
          <div key={i}>
            {item.separator && i > 0 && <div className="my-1 h-px bg-bambu-dark-tertiary" role="separator" />}
            <div
              role={item.checked != null ? 'menuitemcheckbox' : 'menuitem'}
              aria-checked={item.checked ?? undefined}
              aria-disabled={!enabled || undefined}
              tabIndex={enabled ? -1 : undefined}
              title={enabled ? undefined : soon}
              onClick={() => enabled && run(item)}
              onKeyDown={(e) => {
                if (enabled && (e.key === 'Enter' || e.key === ' ')) {
                  e.preventDefault();
                  run(item);
                }
              }}
              className={`flex items-center gap-2 h-[22px] pl-2 pr-3 outline-none ${
                enabled ? 'text-white cursor-pointer hover:bg-bambu-green/30 focus:bg-bambu-green/30' : 'text-bambu-gray/60 cursor-default'
              }`}
            >
              <span className="w-4 flex-shrink-0 inline-flex justify-center">
                {item.checked ? (
                  <Check className="w-3 h-3" />
                ) : item.icon ? (
                  <img src={orcaIcon(item.icon)} alt="" className="w-3.5 h-3.5" aria-hidden />
                ) : null}
              </span>
              <span className="flex-1 whitespace-nowrap">{item.label}</span>
              {item.submenu ? (
                <ChevronRight className="w-3 h-3" />
              ) : item.shortcut ? (
                <span className="text-bambu-gray">{item.shortcut}</span>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

interface PlateBarProps {
  fileId: number;
  plates: WorkspacePlate[];
  selected: number | null;
  onSelect: (plate: number | null) => void;
  /** Plates with a slice (Preview): the others are marked. */
  sliced?: Set<number>;
}

export function PlateBar({ fileId, plates, selected, onSelect, sliced }: PlateBarProps) {
  const { t } = useTranslation();
  if (plates.length === 0) return null;
  return (
    <div
      className={`absolute ${sliced ? 'bottom-10' : 'bottom-2'} left-2 right-12 flex justify-center gap-2 overflow-x-auto pointer-events-none`}
      role="tablist"
      aria-label={t('farm.workspace.plates')}
    >
      {plates.map((p) => {
        const active = selected === p.index;
        const name = t('farm.workspace.plate', { index: p.index }) + (p.name ? ` · ${p.name}` : '');
        return (
          <button
            key={p.index}
            role="tab"
            aria-selected={active}
            title={name}
            onClick={() => onSelect(active && !sliced ? null : p.index)}
            className={`pointer-events-auto relative flex-shrink-0 w-14 h-14 rounded-md border bg-bambu-dark-secondary/90 overflow-hidden ${
              active ? 'border-bambu-green ring-1 ring-bambu-green' : 'border-bambu-dark-tertiary hover:border-bambu-gray'
            } ${sliced && !sliced.has(p.index) ? 'opacity-60' : ''}`}
          >
            <img
              src={api.getLibraryFilePlateThumbnail(fileId, p.index)}
              alt=""
              className="absolute inset-0 w-full h-full object-contain"
              onError={(e) => ((e.target as HTMLImageElement).style.visibility = 'hidden')}
            />
            <span className="absolute top-0.5 left-0.5 min-w-[1.1rem] rounded bg-black/70 px-1 text-[11px] font-semibold text-white">
              {p.index}
            </span>
          </button>
        );
      })}
    </div>
  );
}
