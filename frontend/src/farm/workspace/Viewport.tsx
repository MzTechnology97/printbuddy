// Fork (printfarm): around the 3D view, as OrcaSlicer's Prepare view (v2.5.0): its toolbar on top (the main
// tools, then the object tools, in its order with its icons and tooltips), the canvas buttons bottom left
// (section, view menu, zoom), the plate's own buttons on the right, the object info box bottom right, the
// right-click menus, and the numbered plate bar. What only looks works now (views, zoom, grid, measure, info,
// selection); what would change the geometry or the plates is shown disabled, "phase 2" (it needs a
// model_settings.config writer, see docs/plans/3mf-workspace.md).
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';
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
  why = false,
}: {
  icon: string;
  label: string;
  enabled: boolean;
  active?: boolean;
  onClick?: () => void;
  size?: string;
  /** null: no tooltip (its panel is open beside it) */
  tip?: keyof typeof TIP_PLACE | null;
  /** the label already says why it is greyed */
  why?: boolean;
}) {
  const { t } = useTranslation();
  const title = enabled || why ? label : `${label}: ${t('farm.workspace.gizmos.soon')}`;
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

export function MainToolbar({
  layout,
  active,
  onTool,
  ready = {},
}: {
  layout: OrcaLayout;
  active: string | null;
  onTool: (key: string) => void;
  /** tools that work in this state (true), or why one can't now (its tooltip, as Orca's) */
  ready?: Record<string, true | string>;
}) {
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
              label={
                typeof ready[tool.key] === 'string'
                  ? `${tr(tool.label)}:\n${ready[tool.key]}`
                  : `${tr(tool.label)}${tool.shortcut ? ` [${tool.shortcut}]` : ''}`
              }
              enabled={WORKING_TOOLS.has(tool.key) || ready[tool.key] === true}
              why={typeof ready[tool.key] === 'string'}
              active={WORKING_TOOLS.has(tool.key) || tool.key in ready ? active === tool.key : undefined}
              onClick={() => onTool(tool.key)}
              size="w-9 h-9"
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/** One number field of a gizmo's window: committed on Enter or when it loses focus, as Orca's ImGui inputs. */
function GizmoNumber({
  label,
  shown: tag,
  value,
  onCommit,
  language,
}: {
  label: string;
  /** what shows beside the field (its axis); ``label`` is its accessible name */
  shown?: string;
  value: number;
  onCommit: (v: number) => void;
  language?: string;
}) {
  const shown = toLocaleDecimal(value.toFixed(2), language);
  const [text, setText] = useState(shown);
  useEffect(() => setText(shown), [shown]);
  const commit = () => {
    const v = Number(text.replace(',', '.'));
    if (Number.isFinite(v) && Math.abs(v - value) > 1e-6) onCommit(v);
    setText(shown); // a relative field is back at 0 after its turn; the others show the new value when it comes
  };
  return (
    <label className="flex items-center gap-1">
      <span className="w-3 text-bambu-gray">{tag ?? label}</span>
      <input
        value={text}
        inputMode="decimal"
        aria-label={label}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit();
          if (e.key === 'Escape') setText(shown);
          e.stopPropagation(); // the canvas keys (M, Z, Del...) stay out of the field
        }}
        className="w-20 rounded bg-bambu-dark border border-bambu-dark-tertiary px-1 py-0.5 text-right text-white"
      />
    </label>
  );
}

type Cs = 'world' | 'object' | 'part';
type Vec3T = [number, number, number];

function GizmoWindow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      className="absolute top-14 left-1/2 -translate-x-1/2 rounded-md border border-bambu-dark-tertiary bg-bambu-dark-secondary/95 p-2 text-[12px] text-white space-y-2"
      role="dialog"
      aria-label={label}
    >
      {children}
    </div>
  );
}

/** The coordinate system combo (World / Object; Part comes with the parts). */
function CoordinatesCombo({
  layout,
  value,
  onChange,
  object = true,
  part = false,
}: {
  layout: OrcaLayout;
  value: Cs;
  onChange: (cs: Cs) => void;
  object?: boolean;
  /** a part selected: its own axes too (Orca's Part, in the Scale window) */
  part?: boolean;
}) {
  const tr = useOrcaText(layout);
  return (
    <select
      className="rounded bg-bambu-dark border border-bambu-dark-tertiary px-1 py-0.5"
      title={tr('Coordinate system used for transform actions.')}
      aria-label={tr('Coordinate system used for transform actions.')}
      value={value}
      onChange={(e) => onChange(e.target.value as Cs)}
    >
      <option value="world">{tr('World')}</option>
      {object && <option value="object">{tr('Object')}</option>}
      {part && <option value="part">{tr('Part')}</option>}
    </select>
  );
}

function Row({
  caption,
  values,
  unit,
  onCommit,
  after,
}: {
  caption: string;
  values: Vec3T;
  unit: string;
  onCommit: (axis: 0 | 1 | 2, v: number) => void;
  after?: ReactNode;
}) {
  const { i18n } = useTranslation();
  return (
    <div className="flex items-center gap-2">
      <span className="w-24 truncate text-bambu-gray" title={caption}>{caption}</span>
      {(['X', 'Y', 'Z'] as const).map((axis, i) => (
        <GizmoNumber key={axis} label={`${caption} ${axis}`} shown={axis} value={values[i]} language={i18n.language} onCommit={(v) => onCommit(i as 0 | 1 | 2, v)} />
      ))}
      <span className="text-bambu-gray">{unit}</span>
      {after}
    </div>
  );
}

function ResetButton({ title, onClick, visible }: { title: string; onClick: () => void; visible: boolean }) {
  // Orca keeps the button's place while it is hidden, so the window doesn't jump
  return (
    <button type="button" title={title} aria-label={title} onClick={onClick} className={`rounded px-1 hover:bg-bambu-dark-tertiary ${visible ? '' : 'invisible'}`}>
      ↺
    </button>
  );
}

function Done({ layout, onDone, left }: { layout: OrcaLayout; onDone: () => void; left?: ReactNode }) {
  const tr = useOrcaText(layout);
  return (
    <div className="flex items-center justify-between gap-2">
      <span>{left}</span>
      <button type="button" onClick={onDone} className="rounded bg-bambu-green px-2 py-0.5 hover:bg-bambu-green/80">
        {tr('Done')}
      </button>
    </div>
  );
}

type AlignAxisType = { axis: 0 | 1 | 2; kind: 'min' | 'center' | 'max' | 'distribute' };
/** An align or distribute button: its icon, label (with its axis) and, greyed, why. */
export interface AlignButtonSpec {
  type: AlignAxisType;
  icon: string;
  label: string;
  enabled: boolean;
  why: string | null;
}
/** The Move window's align half (align.ts). */
export interface MoveAlign {
  /** the selection can be aligned within itself (two items): "Selection" is offered */
  canWithin: boolean;
  toParent: boolean;
  /** what the parent is: the plate, or the object holding the parts */
  parentLabel: 'Plate' | 'Object';
  onToParent: (toParent: boolean) => void;
  align: AlignButtonSpec[];
  distribute: AlignButtonSpec[];
  onRun: (type: AlignAxisType) => void;
  /** the button under the pointer, for the 3D view's preview (null: none) */
  onHover: (type: AlignAxisType | null) => void;
}

function AlignButton({ b, onRun, onHover }: { b: AlignButtonSpec; onRun: (t: AlignAxisType) => void; onHover: (t: AlignAxisType | null) => void }) {
  // greyed but still hovered, as Orca's: its tooltip says why
  return (
    <button
      type="button"
      aria-label={b.label}
      aria-disabled={!b.enabled}
      title={b.why ? `${b.label}
${b.why}` : b.label}
      onClick={() => b.enabled && onRun(b.type)}
      onMouseEnter={() => onHover(b.enabled ? b.type : null)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(b.enabled ? b.type : null)}
      onBlur={() => onHover(null)}
      className={`rounded p-0.5 ${b.enabled ? 'hover:bg-bambu-dark-tertiary' : 'opacity-30 cursor-default'}`}
    >
      <img src={orcaIcon(b.icon)} alt="" className="w-4 h-4" aria-hidden />
    </button>
  );
}

/** OrcaSlicer's Move window (GizmoObjectManipulation::do_render_move_window): the coordinate system, the position
 * (World) or a move along the object's own axes (Object: "Relative", from 0); align and distribute, within the
 * selection or to its plate (object); Drop and Done. */
export function MoveWindow({
  layout,
  position,
  cs = 'world',
  relative = cs === 'object',
  onCs,
  canDrop,
  onPosition,
  onDrop,
  onDone,
  align,
}: {
  layout: OrcaLayout;
  position: Vec3T;
  cs?: Cs;
  /** Object coordinates for an instance: a move from 0 ("Relative"); for a part, its place in the object */
  relative?: boolean;
  onCs?: (cs: Cs) => void;
  canDrop: boolean;
  onPosition: (axis: 0 | 1 | 2, value: number) => void;
  onDrop: () => void;
  onDone: () => void;
  align?: MoveAlign;
}) {
  const tr = useOrcaText(layout);
  return (
    <GizmoWindow label={tr('Move')}>
      <CoordinatesCombo layout={layout} value={cs} onChange={(v) => onCs?.(v)} />
      <Row caption={tr(relative ? 'Relative' : 'Position')} values={relative ? [0, 0, 0] : position} unit={tr('mm')} onCommit={onPosition} />
      {align && (
        <div className="space-y-1 border-t border-bambu-dark-tertiary pt-2">
          <div className="flex items-center gap-2">
            <select
              className="w-24 rounded bg-bambu-dark border border-bambu-dark-tertiary px-1 py-0.5"
              title={tr('Reference for alignment')}
              aria-label={tr('Reference for alignment')}
              value={align.toParent ? 'parent' : 'selection'}
              onChange={(e) => align.onToParent(e.target.value === 'parent')}
            >
              {align.canWithin && <option value="selection">{tr('Selection')}</option>}
              <option value="parent">{tr(align.parentLabel)}</option>
            </select>
            {([0, 1, 2] as const).map((axis) => (
              <span key={axis} className="flex w-24 justify-center gap-0.5">
                {align.align
                  .filter((b) => b.type.axis === axis)
                  .map((b) => (
                    <AlignButton key={b.type.kind} b={b} onRun={align.onRun} onHover={align.onHover} />
                  ))}
              </span>
            ))}
          </div>
          {!align.toParent && (
            <div className="flex items-center gap-2">
              <span className="w-24 truncate text-bambu-gray">{tr('Distribute')}</span>
              {align.distribute.map((b) => (
                <span key={b.type.axis} className="flex w-24 justify-center">
                  <AlignButton b={b} onRun={align.onRun} onHover={align.onHover} />
                </span>
              ))}
            </div>
          )}
        </div>
      )}
      <Done
        layout={layout}
        onDone={onDone}
        left={
          <button
            type="button"
            disabled={!canDrop}
            title={tr('Move the selection along Z so that its lowest point touches the bed.')}
            onClick={onDrop}
            className="rounded border border-bambu-dark-tertiary px-2 py-0.5 hover:bg-bambu-dark-tertiary disabled:opacity-40"
          >
            {tr('Drop')}
          </button>
        }
      />
    </GizmoWindow>
  );
}

/** OrcaSlicer's Rotate window (do_render_rotate_window): World; a turn from now (its fields from 0) and back to when
 * the tool opened; the absolute rotation and back to none; Done. */
export function RotateWindow({
  layout,
  absolute,
  changed,
  turned,
  onRelative,
  onAbsolute,
  onResetRelative,
  onResetAbsolute,
  onDone,
}: {
  layout: OrcaLayout;
  /** the instance's rotation (one instance selected), else null */
  absolute: Vec3T | null;
  /** turned since the tool opened: the first reset shows */
  changed: boolean;
  /** turned at all: the second reset shows */
  turned: boolean;
  onRelative: (axis: 0 | 1 | 2, degrees: number) => void;
  onAbsolute: (axis: 0 | 1 | 2, degrees: number) => void;
  onResetRelative: () => void;
  onResetAbsolute: () => void;
  onDone: () => void;
}) {
  const tr = useOrcaText(layout);
  return (
    <GizmoWindow label={tr('Rotate')}>
      <div className="text-bambu-gray" title={tr('Coordinate system used for transform actions.')}>
        {tr('World')}
      </div>
      <Row
        caption={tr('Relative')}
        values={[0, 0, 0]}
        unit="°"
        onCommit={onRelative}
        after={<ResetButton title={tr('Reset current rotation to the value when open the rotation tool.')} onClick={onResetRelative} visible={changed} />}
      />
      {absolute && (
        <Row
          caption={tr('Absolute')}
          values={absolute}
          unit="°"
          onCommit={onAbsolute}
          after={<ResetButton title={tr('Reset current rotation to real zeros.')} onClick={onResetAbsolute} visible={turned} />}
        />
      )}
      <Done layout={layout} onDone={onDone} />
    </GizmoWindow>
  );
}

/** OrcaSlicer's Scale window (do_render_scale_input_window): the coordinate system, the scale (%) and back to 100%,
 * the size (mm), Uniform scale, Done. */
export function ScaleWindow({
  layout,
  cs,
  onCs,
  objectCs,
  partCs = false,
  scale,
  size,
  uniform,
  onUniform,
  onScale,
  onSize,
  onReset,
  onDone,
}: {
  layout: OrcaLayout;
  cs: Cs;
  onCs: (cs: Cs) => void;
  /** Object coordinates offered (not for several objects) */
  objectCs: boolean;
  /** Part coordinates offered (a part selected) */
  partCs?: boolean;
  scale: Vec3T;
  size: Vec3T;
  uniform: boolean;
  onUniform: (on: boolean) => void;
  onScale: (axis: 0 | 1 | 2, percent: number) => void;
  onSize: (axis: 0 | 1 | 2, mm: number) => void;
  onReset: () => void;
  onDone: () => void;
}) {
  const tr = useOrcaText(layout);
  const scaled = scale.some((v) => Math.abs(v - 100) > 0.1);
  return (
    <GizmoWindow label={tr('Scale')}>
      <CoordinatesCombo layout={layout} value={cs} onChange={onCs} object={objectCs} part={partCs} />
      <Row
        caption={tr('Noun\u0004Scale')}
        values={scale}
        unit="%"
        onCommit={(axis, v) => v > 0 && onScale(axis, v)}
        after={<ResetButton title={tr('Reset scale')} onClick={onReset} visible={scaled} />}
      />
      <Row caption={tr('Size')} values={size} unit={tr('mm')} onCommit={(axis, v) => v > 0 && onSize(axis, v)} />
      <Done
        layout={layout}
        onDone={onDone}
        left={
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={uniform} onChange={(e) => onUniform(e.target.checked)} />
            {tr('Uniform scale')}
          </label>
        }
      />
    </GizmoWindow>
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

/** What the plate's buttons act on: the plate (its number among ``count``, locked or not) and what each does. */
export interface PlateActions {
  index: number;
  count: number;
  locked: boolean;
  /** absent: the plates can't be moved in this file (its model is too large to rewrite) */
  onRemove?: () => void;
  onLock: () => void;
  onName: () => void;
  onFront?: () => void;
  /** Auto orient objects on current plate (absent: the plate is empty, or the model can't be rewritten) */
  onOrient?: () => void;
}

/** The plate's own buttons (PartPlate.cpp): remove (not the last plate), auto orient, lock / unlock, its name, to the
 * front (not the first), its settings; arrange comes later. Without ``plate`` (a file
 * that can't be saved) only "Customize current plate" works, showing the plate's settings. */
export function PlateButtons({ layout, onSettings, plate }: { layout: OrcaLayout; onSettings: () => void; plate?: PlateActions }) {
  const tr = useOrcaText(layout);
  const act: Record<string, (() => void) | undefined> = {
    settings: onSettings,
    remove: plate && plate.count > 1 ? plate.onRemove : undefined,
    lock: plate?.onLock,
    name: plate?.onName,
    front: plate && plate.index > 1 ? plate.onFront : undefined,
    orient: plate?.onOrient,
  };
  return (
    <div className="absolute right-2 top-12 flex flex-col gap-1" role="toolbar" aria-label={tr('Plate')}>
      {PLATE_TOOLS.map((tool) => {
        const locked = tool.key === 'lock' && !!plate?.locked;
        return (
          <ToolButton
            key={tool.key}
            icon={locked ? 'plate_locked_dark' : tool.icon}
            label={tr(locked ? 'Unlock current plate' : tool.label)}
            enabled={!!act[tool.key]}
            // greyed for this plate (the last one, the first one), not to come: the label says why
            why={!!plate && (tool.key === 'remove' || tool.key === 'front')}
            active={tool.key === 'lock' && plate ? locked : undefined}
            tip="left"
            onClick={act[tool.key]}
          />
        );
      })}
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
  /** A real submenu: opens on hover, a click, Enter or the right arrow (the left arrow goes back). */
  items?: MenuItem[];
  /** Why it is greyed, as its tooltip (default: phase 2). */
  hint?: string;
  checked?: boolean;
  icon?: string;
  /** Works when set; otherwise disabled, "phase 2". */
  onClick?: () => void;
  /** A line above it. */
  separator?: boolean;
}

/** A right-click menu at a point (``at``), or beside its button: arrows, Enter, Esc and a click outside close
 * it; disabled items say they come in phase 2. */
export function ContextMenu({
  items,
  at,
  onClose,
  nested,
  onBack,
}: {
  items: MenuItem[];
  at?: { x: number; y: number };
  onClose: () => void;
  /** A submenu: the root menu handles a click outside and Esc. */
  nested?: boolean;
  /** Back to the parent menu (left arrow). */
  onBack?: () => void;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const [sub, setSub] = useState<{ index: number; at: { x: number; y: number } } | null>(null);
  // what had the focus when the menu opened (its trigger): it gets it back on Esc or a chosen item
  const returnTo = useRef<HTMLElement | null>(nested ? null : (document.activeElement as HTMLElement | null));
  const done = () => {
    returnTo.current?.focus?.();
    close.current();
  };
  const openSub = (index: number, el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    setSub({ index, at: { x: r.right, y: r.top - 4 } });
  };
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
    if (nested) return;
    const outside = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && close.current();
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && done();
    document.addEventListener('mousedown', outside);
    window.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', outside);
      window.removeEventListener('keydown', esc);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, as the menu opens
  }, []);

  const run = (item: MenuItem) => {
    returnTo.current?.focus?.(); // before the action: a dialog it opens takes the focus from there
    item.onClick?.();
    close.current();
  };
  const onKey = (e: React.KeyboardEvent) => {
    // this menu's own items only (a submenu inside it handles its keys itself)
    const enabled = [...(ref.current?.querySelectorAll<HTMLElement>(':scope > div > [role^="menuitem"]:not([aria-disabled="true"])') ?? [])];
    const i = enabled.indexOf(document.activeElement as HTMLElement);
    const next = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: enabled.length - 1 }[e.key];
    e.stopPropagation(); // the menu, not a menu or a dialog behind it
    if (next != null && enabled.length) {
      e.preventDefault();
      setSub(null);
      enabled[(next + enabled.length) % enabled.length].focus();
    } else if (e.key === 'ArrowLeft' && onBack) {
      e.preventDefault();
      onBack();
    } else if (e.key === 'Escape') {
      done();
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
        const parent = !!item.items?.length;
        const enabled = !!item.onClick || parent;
        return (
          <div key={i}>
            {item.separator && i > 0 && <div className="my-1 h-px bg-bambu-dark-tertiary" role="separator" />}
            <div
              role={item.checked != null ? 'menuitemcheckbox' : 'menuitem'}
              aria-checked={item.checked ?? undefined}
              aria-disabled={!enabled || undefined}
              aria-haspopup={parent || undefined}
              aria-expanded={parent ? sub?.index === i : undefined}
              tabIndex={enabled ? -1 : undefined}
              title={enabled ? undefined : (item.hint ?? soon)}
              onMouseEnter={(e) => (parent ? openSub(i, e.currentTarget) : setSub(null))}
              onClick={(e) => (parent ? openSub(i, e.currentTarget) : enabled && run(item))}
              onKeyDown={(e) => {
                if (parent && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowRight')) {
                  e.preventDefault();
                  e.stopPropagation();
                  openSub(i, e.currentTarget);
                } else if (enabled && !parent && (e.key === 'Enter' || e.key === ' ')) {
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
              {item.submenu || parent ? (
                <ChevronRight className="w-3 h-3" />
              ) : item.shortcut ? (
                <span className="text-bambu-gray">{item.shortcut}</span>
              ) : null}
            </div>
            {parent && sub?.index === i && (
              <ContextMenu
                items={item.items!}
                at={sub.at}
                nested
                onClose={() => close.current()}
                onBack={() => {
                  setSub(null);
                  ref.current?.querySelectorAll<HTMLElement>(':scope > div > [role^="menuitem"]')[i]?.focus();
                }}
              />
            )}
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
            {/* a plate moved shows the file's picture of it; a new one has none yet */}
            {p.source !== null && (
              <img
                src={api.getLibraryFilePlateThumbnail(fileId, p.source ?? p.index)}
                alt=""
                className="absolute inset-0 w-full h-full object-contain"
                onError={(e) => ((e.target as HTMLImageElement).style.visibility = 'hidden')}
              />
            )}
            <span className="absolute top-0.5 left-0.5 min-w-[1.1rem] rounded bg-black/70 px-1 text-[11px] font-semibold text-white">
              {p.index}
            </span>
          </button>
        );
      })}
    </div>
  );
}
