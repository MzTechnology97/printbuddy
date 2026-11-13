// Fork (printfarm): the Process box's "Objects" view, as OrcaSlicer's object list: a search, the tree Plate N ->
// objects -> parts and modifiers, and "Instances" when an object has several; its columns in OrcaSlicer's order
// (mesh warning in the name, variable layer height, printable, filament chip, support and colour painting,
// sinking, a cog for settings of its own; a column only when some object needs it),
// and under it the settings of what is selected: a plate's "Plate Settings" (model_settings.config plate
// metadata, read only until the plates' batch) or an object's (part's) settings by tab, its own values first,
// editable as OrcaSlicer's object and part tabs: a value changed becomes its own, the reset arrow drops it, and
// the cog of the list (colEditing) drops them all. The copy writes them in model_settings.config.
// An object's height ranges (Height Range Modifier) show under it as "Layers" -> "Range a-b (mm)": the Layers row
// edits them all (GUI_ObjectLayers.cpp), a range its own Z and its settings (TabPrintLayer).
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, RotateCcw } from 'lucide-react';
import type { LayerRange, WorkspaceDocument, WorkspaceObject, WorkspacePlate, WorkspaceSetting } from './api';
import { type Draft, ON, toLocaleDecimal } from './draft';
import { type OrcaLayout, orcaFormat, orcaIcon, orcaPlural, orcaText } from './orcaLayout';
import { EnumSelect, SettingControl } from './SettingControl';
import type { PlateValues } from './PlateSettings';
import { SettingsPanel } from './SettingsPanel';
import type { OwnEdits } from './draft';
import {
  FILAMENT_PARTS,
  type Selection,
  editTarget,
  filamentEdits,
  filamentOf,
  hiddenPart,
  instancesOf,
  ownSettings,
  plateOwn,
  targetOwn,
} from './objects';
import { type RangeEdits, addAfter, editMax, editMin, parseZ, rangeLabel, rangesOf, removeRange, withOption } from './ranges';
import type { ObjectStats } from './viewTools';
import '../i18n';

const PART_ICONS: Record<string, string> = {
  normal_part: 'menu_add_part',
  negative_part: 'menu_add_negative',
  modifier_part: 'menu_add_modifier',
  support_blocker: 'menu_support_blocker',
  support_enforcer: 'menu_support_enforcer',
};

export function Chip({ colour, n, dim }: { colour: string | undefined; n: number | string; dim?: boolean }) {
  return (
    <span
      className={`inline-flex items-center justify-center w-4 h-4 rounded-sm text-[10px] font-semibold text-black border border-white/30 flex-shrink-0 ${
        dim ? 'opacity-60' : ''
      }`}
      style={{ backgroundColor: colour ?? '#777' }}
    >
      {n}
    </span>
  );
}

/** OrcaSlicer's Fila. cell: the filament's chip; a button opening the list when it can change. ``value`` 0: a
 * modifier's "default" (grey), a part's own none (its object's filament, dimmed). */
function FilamentCell({
  value,
  shown,
  colours,
  label,
  onOpen,
}: {
  value: number;
  shown: number;
  colours: string[];
  label: string;
  onOpen?: (at: { x: number; y: number }) => void;
}) {
  const chip = shown > 0 ? <Chip colour={colours[shown - 1]} n={shown} dim={value === 0} /> : <Chip colour="#9e9e9e" n="–" />;
  if (!onOpen) return chip;
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-haspopup="listbox"
      onClick={(e) => {
        e.stopPropagation();
        const r = e.currentTarget.getBoundingClientRect();
        onOpen({ x: r.left, y: r.bottom });
      }}
      className="flex-shrink-0 rounded hover:ring-1 hover:ring-white/60"
    >
      {chip}
    </button>
  );
}

function Cog({ title, onReset }: { title: string; onReset?: () => void }) {
  const icon = <img src={orcaIcon('cog')} alt={onReset ? '' : title} title={onReset ? undefined : title} className="w-3.5 h-3.5 opacity-90" />;
  return onReset ? (
    // OrcaSlicer's colEditing: a click drops every setting of its own (TabPrintModel::reset_model_config)
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={(e) => {
        e.stopPropagation();
        onReset();
      }}
      className="w-3.5 h-3.5 flex-shrink-0 rounded hover:bg-bambu-dark-tertiary"
    >
      {icon}
    </button>
  ) : (
    <span className="flex-shrink-0">{icon}</span>
  );
}

/** Editing the objects' own settings (draft.ts): the unsaved edits and how to make them. */
export interface OwnEditing {
  edits: OwnEdits;
  /** ``value``: the target's new own value; null: not its own any more; undefined: as the file has it */
  set: (target: string, key: string, value: string | null | undefined) => void;
  setMany: (target: string, values: Record<string, string | null | undefined>) => void;
  /** several targets in one undo step (a filament change: the object's and its parts') */
  setTargets?: (values: Record<string, Record<string, string | null | undefined>>) => void;
  /** a plate's own settings (plates.ts): set some (one undo step), or open the dialog's filament sequences */
  plate?: { apply: (plate: WorkspacePlate, values: PlateValues) => void; customize: (plate: WorkspacePlate) => void };
  /** the objects' height ranges (ranges.ts) */
  ranges?: RangeEditing;
}

/** Editing the objects' height ranges: the unsaved edits and how to make them. */
export interface RangeEditing {
  edits: RangeEdits;
  /** an object's ranges as they are to be; ``key``: the field typed in (its keystrokes make one undo step) */
  set: (obj: WorkspaceObject, next: LayerRange[], key?: string | null) => void;
  /** a new range's settings (get_default_layer_config) */
  defaults: (obj: WorkspaceObject) => Record<string, string>;
  /** the printer's minimum layer height of a filament (0: the first), splitting a range */
  minLayerHeight: (extruder: number) => number;
}

const PART_TABS = new Set(['normal_part', 'modifier_part']); // ParamsPanel's TabPrintPart; the others have no tab

/** A column's icon (or its empty cell, keeping the columns in line). */
function Cell({ icon, title }: { icon?: string | false; title?: string }) {
  return icon ? (
    <img src={orcaIcon(icon)} alt={title} title={title} className="w-4 h-4 flex-shrink-0" />
  ) : (
    <span className="w-4 flex-shrink-0" aria-hidden />
  );
}

interface Props {
  layout: OrcaLayout;
  doc: WorkspaceDocument;
  draft: Draft;
  colours: string[];
  stats: Map<string, ObjectStats>;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  onMenu: (at: { x: number; y: number }, selection: Exclude<Selection, null>) => void;
  /** right click on the list's empty space */
  onEmptyMenu?: (at: { x: number; y: number }) => void;
  /** the objects selected together (Ctrl / Shift click, or in 3D) */
  selectedIds?: string[];
  onToggle?: (objectId: string) => void;
  /** the objects' own settings can be edited (a project the copy can save) */
  own?: OwnEditing;
}

export function ObjectsPanel({
  layout,
  doc,
  draft,
  colours,
  stats,
  selection,
  onSelect,
  onMenu,
  onEmptyMenu,
  selectedIds = [],
  onToggle,
  own: editing,
}: Props) {
  const { t, i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const [query, setQuery] = useState('');
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const byId = useMemo(() => new Map(doc.objects.map((o) => [o.id ?? '', o])), [doc.objects]);
  const byKey = useMemo(() => new Map(doc.settings.map((s) => [s.key, s])), [doc.settings]);
  const q = query.trim().toLowerCase();
  const hit = (text: string | null | undefined) => !q || (text ?? '').toLowerCase().includes(q);
  const ownCount = t('farm.workspace.ownSettingsTitle');
  const edits = editing?.edits;
  // the keys a tab shows, the ones its reset drops: the process keys an object (a part) can have (TabPrintModel's m_keys)
  const tabKeys = (part: boolean) =>
    new Set(((part ? layout.region_keys : null) ?? layout.object_keys ?? []).filter((k) => layout.sections?.[k] === 'process'));
  /** Orca's colEditing on an object (with its hidden part) or a part: its own settings dropped, one undo step. */
  const resetAll = (obj: WorkspaceObject, partId: string | null) => {
    if (!editing) return;
    const id = obj.id ?? '';
    // an object's view holds its hidden part's settings too (objects.ts): both are dropped
    const hidden = hiddenPart(obj);
    const targets: [string, string | null][] = partId ? [[id, partId]] : hidden ? [[id, null], [id, hidden]] : [[id, null]];
    for (const [objectId, part] of targets) {
      const keys = tabKeys(!!part);
      const saved = targetOwn({ ...obj, id: objectId }, part);
      const now = targetOwn({ ...obj, id: objectId }, part, edits);
      const values: Record<string, string | null | undefined> = {};
      for (const key of new Set([...Object.keys(saved), ...Object.keys(now)])) {
        if (keys.has(key)) values[key] = key in saved ? null : undefined;
      }
      if (Object.keys(values).length) editing.setMany(part ? `${objectId}/${part}` : objectId, values);
    }
  };
  // OrcaSlicer's Fila. column: the list of a row (an object, a part, a modifier) and keys 0-9 on the selection
  const [picker, setPicker] = useState<{ obj: WorkspaceObject; partId: string | null; range?: number; at: { x: number; y: number } } | null>(
    null,
  );
  const rangeEdits = editing?.ranges?.edits;
  const canPick = !!editing?.setTargets && colours.length > 0;
  const pick = (obj: WorkspaceObject, partId: string | null, n: number, how: 'column' | 'keys') =>
    editing?.setTargets?.(filamentEdits(obj, partId, n, how, edits));
  const filamentKeys = (e: React.KeyboardEvent) => {
    // ObjectList::key_event: with more than one filament, a digit up to their count sets the selection's
    if (!canPick || colours.length < 2 || e.ctrlKey || e.metaKey || e.altKey || !/^[0-9]$/.test(e.key)) return;
    const n = Number(e.key);
    if (n > colours.length || selected?.kind !== 'object') return;
    e.preventDefault();
    const obj = byId.get(selected.id);
    if (selected.partId) {
      if (obj) pick(obj, selected.partId, n, 'keys');
      return;
    }
    for (const id of new Set([selected.id, ...selectedIds])) {
      const o = byId.get(id);
      if (o) pick(o, null, n, 'keys');
    }
  };
  const instances = useMemo(() => instancesOf(doc.plates), [doc.plates]);
  // OrcaSlicer shows a column only when some object needs it (GUI_ObjectList.cpp, update_..._column_visible)
  const all = [...stats.values()];
  const cols = {
    height: all.some((s) => s.variableLayers),
    support: all.some((s) => s.paint.supports),
    color: all.some((s) => s.paint.color),
    sinking: all.some((s) => s.sinking),
  };
  const meshTip = (open: number) =>
    `${tr('Remaining errors')}:\n\t${orcaPlural(layout, '%1$d non-manifold edge', '%1$d non-manifold edges', open, i18n.language)}\n\n${tr(
      'Click the icon to repair model object',
    )}`;

  // plates -> their objects (once per plate, however many instances); a project without plates: one list
  const groups: { plate: WorkspacePlate | null; objects: WorkspaceObject[] }[] = doc.plates.length
    ? doc.plates.map((p) => ({
        plate: p,
        objects: [...new Set(p.object_ids)].map((id) => byId.get(id)).filter((o): o is WorkspaceObject => !!o),
      }))
    : [{ plate: null, objects: doc.objects }];

  const toggle = (key: string) =>
    setClosed((c) => {
      const next = new Set(c);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const open = (key: string) => !!q || !closed.has(key);
  const menu = (e: React.MouseEvent, sel: Exclude<Selection, null>) => {
    e.preventDefault();
    onSelect(sel);
    onMenu({ x: e.clientX, y: e.clientY }, sel);
  };
  // rows 2em high, as OrcaSlicer's (its badges always readable)
  const row = (selected: boolean) =>
    `group flex items-center gap-1.5 h-6 pr-2 text-[12px] cursor-pointer select-none ${
      selected ? 'bg-bambu-green/25 text-white' : 'text-bambu-gray-light hover:bg-bambu-dark-tertiary/60'
    }`;
  const caret = (key: string, has: boolean) =>
    has ? (
      <button
        onClick={(e) => {
          e.stopPropagation();
          toggle(key);
        }}
        aria-label={open(key) ? t('farm.workspace.collapse') : t('farm.workspace.expand')}
        className="w-3.5 h-3.5 inline-flex items-center justify-center text-bambu-gray"
      >
        {open(key) ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
      </button>
    ) : (
      <span className="w-3.5" />
    );

  const selected = selection;
  const selectedObject = selected?.kind === 'object' ? byId.get(selected.id) : undefined;
  const selectedPlate = selected?.kind === 'plate' ? doc.plates.find((p) => p.index === selected.plate) : undefined;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-3 pt-1.5 pb-1">
        <label className="relative block">
          <img src={orcaIcon('search')} alt="" className="w-3.5 h-3.5 absolute left-2 top-1.5 opacity-70" aria-hidden />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={tr('Search plate, object and part.')}
            aria-label={tr('Search plate, object and part.')}
            className="w-full h-7 bg-bambu-dark border border-bambu-dark-tertiary rounded pl-7 pr-2 text-[12px] text-white"
          />
        </label>
      </div>
      <ul
        role="tree"
        aria-label={tr('Objects')}
        tabIndex={0}
        onKeyDown={filamentKeys}
        className="max-h-[40%] min-h-[6rem] overflow-y-auto border-b border-bambu-dark-tertiary pb-1 focus:outline-none"
        onContextMenu={(e) => {
          if (e.target !== e.currentTarget || !onEmptyMenu) return; // a row has its own menu
          e.preventDefault();
          onEmptyMenu({ x: e.clientX, y: e.clientY });
        }}
      >
        {groups.map(({ plate, objects }) => {
          const plateKey = `p${plate?.index ?? 0}`;
          const plateName = plate ? `${tr('Plate')} ${plate.index}${plate.name ? ` · ${plate.name}` : ''}` : tr('Objects');
          const shownObjects = objects.filter((o) => hit(plateName) || hit(o.name) || o.parts.some((p) => hit(p.name)));
          if (q && !shownObjects.length && !hit(plateName)) return null;
          const plateSelected = !!plate && selected?.kind === 'plate' && selected.plate === plate.index;
          const plateHasOwn = !!plate && Object.keys(plateOwn(layout, plate, edits)).length > 0;
          return (
            <li key={plateKey} role="treeitem" aria-expanded={open(plateKey)} aria-selected={plateSelected} aria-level={1}>
              <div
                className={`${row(plateSelected)} pl-1`}
                onClick={() => plate && onSelect({ kind: 'plate', plate: plate.index })}
                onContextMenu={(e) => plate && menu(e, { kind: 'plate', plate: plate.index })}
              >
                {caret(plateKey, objects.length > 0)}
                <span className="flex-1 truncate">{plateName}</span>
                {plateHasOwn && <Cog title={ownCount} />}
              </div>
              {!!plate?.copies?.length && open(plateKey) && (
                <p className="pl-6 pr-2 py-1 text-xs text-bambu-gray">{t('farm.workspace.plateCopies')}</p>
              )}
              {open(plateKey) && (
                <ul role="group">
                  {shownObjects.map((obj) => {
                    const id = obj.id ?? '';
                    const objKey = `${plateKey}-o${id}`;
                    const own = Object.keys(ownSettings(obj, null, edits)).length > 0;
                    const showParts = obj.parts.length > 1 || obj.parts.some((p) => p.type && p.type !== 'normal_part') || obj.overrides.some((o) => o.part && o.part !== obj.name);
                    const placed = instances.get(id) ?? [];
                    const showInstances = placed.length > 1;
                    const ranges = rangesOf(obj, rangeEdits);
                    const layersKey = `${objKey}-l`;
                    const layersSelected = selected?.kind === 'object' && selected.id === id && !!selected.layers && selected.range == null;
                    const s = stats.get(id);
                    const n = filamentOf(obj, null, edits);
                    // an instance picked in the list lights its own row (one picked in 3D, the object's when it has one instance)
                    const objSelected =
                      (selected?.kind === 'object' && selected.id === id && !selected.part && (selected.instance == null || !showInstances)) ||
                      selectedIds.includes(id);
                    const sel = { kind: 'object' as const, id, plate: plate?.index ?? null };
                    return (
                      <li
                        key={objKey}
                        role="treeitem"
                        aria-expanded={showParts || showInstances || ranges.length > 0 ? open(objKey) : undefined}
                        aria-selected={objSelected}
                        aria-level={2}
                      >
                        <div
                          className={`${row(objSelected)} pl-4`}
                          onClick={(e) => {
                            if ((e.ctrlKey || e.metaKey || e.shiftKey) && onToggle) onToggle(id);
                            onSelect(sel);
                          }}
                          onContextMenu={(e) => menu(e, sel)}
                        >
                          {caret(objKey, showParts || showInstances || ranges.length > 0)}
                          {s && s.openEdges > 0 && (
                            <img src={orcaIcon('obj_warning')} alt={tr('Errors')} title={meshTip(s.openEdges)} className="w-4 h-4 flex-shrink-0" />
                          )}
                          <span className="flex-1 truncate">{obj.name ?? `#${id}`}</span>
                          {cols.height && <Cell icon={s?.variableLayers && 'obj_variable_layer_height'} title={tr('Variable layer height')} />}
                          <input
                            type="checkbox"
                            readOnly
                            disabled
                            checked={s?.printable ?? true}
                            aria-label={tr('Printable')}
                            title={tr('Printable')}
                            className="w-3.5 h-3.5 accent-bambu-green"
                          />
                          <FilamentCell
                            value={n}
                            shown={n}
                            colours={colours}
                            label={`${tr('Filament')} ${n}`}
                            onOpen={canPick ? (at) => setPicker({ obj, partId: null, at }) : undefined}
                          />
                          {cols.support && (
                            <Cell icon={s?.paint.supports && 'objlist_support_painting'} title={tr('Click the icon to edit support painting of the object')} />
                          )}
                          {cols.color && (
                            <Cell icon={s?.paint.color && 'objlist_color_painting'} title={tr('Click the icon to edit color painting for the object')} />
                          )}
                          {cols.sinking && <Cell icon={s?.sinking && 'objlist_sinking'} title={tr('Click the icon to shift this object to the bed')} />}
                          {own ? (
                            <Cog
                              title={editing ? tr('Click the icon to reset all settings of the object') : ownCount}
                              onReset={editing ? () => resetAll(obj, null) : undefined}
                            />
                          ) : (
                            <span className="w-3.5" aria-hidden />
                          )}
                        </div>
                        {showParts && open(objKey) && (
                          <ul role="group">
                            {obj.parts
                              .filter((p) => hit(p.name) || hit(obj.name) || hit(plateName))
                              .map((p) => {
                                const partSelected = selected?.kind === 'object' && selected.id === id && selected.part === p.name;
                                const partOwn = !!p.id && Object.keys(targetOwn(obj, p.id, edits)).length > 0;
                                const partSel = { ...sel, part: p.name, partId: p.id };
                                return (
                                  <li key={`${objKey}-${p.id ?? p.name}`} role="treeitem" aria-selected={partSelected} aria-level={3}>
                                    <div className={`${row(partSelected)} pl-9`} onClick={() => onSelect(partSel)} onContextMenu={(e) => menu(e, partSel)}>
                                      <img src={orcaIcon(PART_ICONS[p.type ?? 'normal_part'] ?? 'menu_add_part')} alt="" className="w-3.5 h-3.5" aria-hidden />
                                      <span className="flex-1 truncate">{p.name ?? `#${p.id}`}</span>
                                      {p.id && FILAMENT_PARTS.has(p.type ?? 'normal_part') && (
                                        <FilamentCell
                                          value={filamentOf(obj, p.id, edits)}
                                          // a model part with none prints with its object's; a modifier's none changes nothing
                                          shown={filamentOf(obj, p.id, edits) || ((p.type ?? 'normal_part') === 'normal_part' ? n : 0)}
                                          colours={colours}
                                          label={`${tr('Filament')} ${filamentOf(obj, p.id, edits) || tr('default')}`}
                                          onOpen={canPick ? (at) => setPicker({ obj, partId: p.id, at }) : undefined}
                                        />
                                      )}
                                      {partOwn && (
                                        <Cog
                                          title={editing ? tr('Click the icon to reset all settings of the object') : ownCount}
                                          onReset={editing && p.id ? () => resetAll(obj, p.id) : undefined}
                                        />
                                      )}
                                    </div>
                                  </li>
                                );
                              })}
                          </ul>
                        )}
                        {ranges.length > 0 && open(objKey) && (
                          <ul role="group">
                            <li role="treeitem" aria-expanded={open(layersKey)} aria-selected={layersSelected} aria-level={3}>
                              <div
                                className={`${row(layersSelected)} pl-9`}
                                onClick={() => onSelect({ ...sel, layers: true, range: null })}
                              >
                                {caret(layersKey, true)}
                                <img src={orcaIcon('height_range_layer')} alt="" className="w-3.5 h-3.5" aria-hidden />
                                <span className="flex-1 truncate">{tr('Layers')}</span>
                              </div>
                              {open(layersKey) && (
                                <ul role="group">
                                  {ranges.map((r, k) => {
                                    const rangeSelected = selected?.kind === 'object' && selected.id === id && !!selected.layers && selected.range === k;
                                    const own = Number(r.options.extruder ?? 0);
                                    return (
                                      <li key={`${r.min_z}-${r.max_z}`} role="treeitem" aria-selected={rangeSelected} aria-level={4}>
                                        <div
                                          className={`${row(rangeSelected)} pl-14`}
                                          onClick={() => onSelect({ ...sel, layers: true, range: k })}
                                        >
                                          <img src={orcaIcon('height_range_layer')} alt="" className="w-3.5 h-3.5" aria-hidden />
                                          <span className="flex-1 truncate">{rangeLabel(r, tr('Range'), tr('mm'))}</span>
                                          <FilamentCell
                                            value={own}
                                            // 0: the object's filament
                                            shown={own || n}
                                            colours={colours}
                                            label={`${tr('Filament')} ${own || tr('default')}`}
                                            onOpen={editing?.ranges && colours.length ? (at) => setPicker({ obj, partId: null, range: k, at }) : undefined}
                                          />
                                        </div>
                                      </li>
                                    );
                                  })}
                                </ul>
                              )}
                            </li>
                          </ul>
                        )}
                        {showInstances && open(objKey) && (
                          <ul role="group">
                            <li role="treeitem" aria-expanded={open(`${objKey}-i`)} aria-level={3}>
                              <div className={`${row(false)} pl-9`} onClick={() => toggle(`${objKey}-i`)}>
                                {caret(`${objKey}-i`, true)}
                                <span className="flex-1 truncate">{tr('Instances')}</span>
                              </div>
                              {open(`${objKey}-i`) && (
                                <ul role="group">
                                  {placed.map((inst) => {
                                    const instSel = { kind: 'object' as const, id, plate: inst.plate, instance: inst.index };
                                    const instSelected = selected?.kind === 'object' && selected.id === id && selected.instance === inst.index && !selected.part;
                                    // OrcaSlicer writes the plate's index from 0 (find_instance_belongs): no mark on the first
                                    const plate0 = inst.plate - 1;
                                    const label =
                                      plate0 > 0
                                        ? orcaFormat(layout, '[P%d]Instance %d', i18n.language, plate0, inst.index + 1)
                                        : orcaFormat(layout, 'Instance %d', i18n.language, inst.index + 1);
                                    return (
                                      <li key={`${inst.plate}-${inst.index}`} role="treeitem" aria-selected={instSelected} aria-level={4}>
                                        <div
                                          className={`${row(instSelected)} pl-14`}
                                          onClick={() => onSelect(instSel)}
                                          onContextMenu={(e) => menu(e, instSel)}
                                        >
                                          <span className="flex-1 truncate">{label}</span>
                                        </div>
                                      </li>
                                    );
                                  })}
                                </ul>
                              )}
                            </li>
                          </ul>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {picker && (
        <FilamentList
          at={picker.at}
          colours={colours}
          // ExtraRenderers.cpp: "default" for a modifier; an object's and a part's are the filaments
          withDefault={
            picker.range != null || (!!picker.partId && picker.obj.parts.find((p) => p.id === picker.partId)?.type === 'modifier_part')
          }
          current={
            picker.range != null
              ? Number(rangesOf(picker.obj, rangeEdits)[picker.range]?.options.extruder ?? 0)
              : filamentOf(picker.obj, picker.partId, edits)
          }
          defaultLabel={tr('default')}
          label={tr('Fila.')}
          onPick={(n) => {
            const re = editing?.ranges;
            if (picker.range != null && re) {
              re.set(picker.obj, withOption(rangesOf(picker.obj, re.edits), picker.range, 'extruder', String(n), re.defaults(picker.obj)));
            } else pick(picker.obj, picker.partId, n, 'column');
            setPicker(null);
          }}
          onClose={() => setPicker(null)}
        />
      )}
      <div className="flex-1 min-h-[16rem]">
        {selectedPlate ? (
          <PlateSettings key={selectedPlate.index} layout={layout} plate={selectedPlate} byKey={byKey} draft={draft} editing={editing} />
        ) : selectedObject && selected?.kind === 'object' && selected.layers ? (
          <RangeSettings
            key={`${selected.id}-l${selected.range ?? ''}`}
            layout={layout}
            doc={doc}
            draft={draft}
            obj={selectedObject}
            range={selected.range ?? null}
            editing={editing?.ranges}
            onSelectRange={(range) =>
              onSelect(range === undefined ? { kind: 'object', id: selected.id, plate: selected.plate } : { ...selected, range })
            }
          />
        ) : selectedObject && selected?.kind === 'object' ? (
          <ObjectSettings
            key={`${selected.id}-${selected.partId ?? selected.part ?? ''}`}
            layout={layout}
            doc={doc}
            draft={draft}
            obj={selectedObject}
            partId={selected.partId ?? (selected.part ? selectedObject.parts.find((p) => p.name === selected.part)?.id ?? null : null)}
            editing={editing}
          />
        ) : (
          <p className="px-3 py-3 text-[12px] text-bambu-gray">{t('farm.workspace.selectHint')}</p>
        )}
      </div>
    </div>
  );
}

/** The Fila. column's list (BitmapChoiceRenderer): each filament with its colour, "default" first for a modifier. */
function FilamentList({
  at,
  colours,
  withDefault,
  current,
  defaultLabel,
  label,
  onPick,
  onClose,
}: {
  at: { x: number; y: number };
  colours: string[];
  withDefault: boolean;
  current: number;
  defaultLabel: string;
  label: string;
  onPick: (n: number) => void;
  onClose: () => void;
}) {
  const choices = [...(withDefault ? [0] : []), ...colours.map((_, i) => i + 1)];
  return (
    <>
      <div
        className="fixed inset-0 z-40"
        onClick={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
        aria-hidden
      />
      <ul
        role="listbox"
        aria-label={label}
        className="fixed z-50 min-w-[6rem] max-h-72 overflow-y-auto rounded border border-bambu-dark-tertiary bg-bambu-dark-secondary py-1 shadow-lg"
        style={{ left: at.x, top: at.y + 2 }}
        onKeyDown={(e) => e.key === 'Escape' && onClose()}
      >
        {choices.map((n) => (
          <li key={n} role="option" aria-selected={n === current}>
            <button
              type="button"
              onClick={() => onPick(n)}
              className={`flex w-full items-center gap-2 px-2 py-1 text-[12px] text-left hover:bg-bambu-dark-tertiary ${
                n === current ? 'text-white font-semibold' : 'text-bambu-gray-light'
              }`}
            >
              {n ? <Chip colour={colours[n - 1]} n={n} /> : <Chip colour="#9e9e9e" n="–" />}
              {n ? n : defaultLabel}
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

/** One object's (part's) tabs: OrcaSlicer's TabPrintObject (TabPrintPart). */
function ObjectSettings({
  layout,
  doc,
  draft,
  obj,
  partId,
  editing,
}: {
  layout: OrcaLayout;
  doc: WorkspaceDocument;
  draft: Draft;
  obj: WorkspaceObject;
  partId: string | null;
  editing?: OwnEditing;
}) {
  const { t } = useTranslation();
  const part = partId ? obj.parts.find((p) => p.id === partId) : undefined;
  const editable = !!editing && (!part || PART_TABS.has(part.type ?? 'normal_part'));
  const edits = editing?.edits;
  /** ``value`` of ``key`` for the target that holds it: undefined when it's the file's own value again. */
  const put = (key: string, value: string | null) => {
    if (!editing) return;
    const target = editTarget(obj, partId, key, edits);
    const [, targetPart = null] = target.split('/');
    const saved = targetOwn(obj, targetPart)[key];
    editing.set(target, key, value === (saved ?? null) ? undefined : value);
  };
  return (
    <SettingsPanel
      layout={layout}
      section="process"
      settings={doc.settings}
      draft={draft}
      editable={editable}
      onChange={(setting, value) => put(setting.key, Array.isArray(value) ? value.join(',') : value)}
      onReset={(key) => put(key, null)}
      object={{ own: ownSettings(obj, partId, edits), part: !!partId }}
      header={
        <p className="text-[12px] text-bambu-gray truncate">
          {obj.name}
          {part && part.name !== obj.name ? ` › ${part.name}` : ''}
          {!editable && ` · ${t('farm.workspace.readOnly')}`}
        </p>
      }
    />
  );
}

/** A Z of a range (LayerRangeEditor): typed, taken on Enter or when it loses focus; a value it can't take shows
 * Orca's "Invalid numeric." and goes back. */
function ZInput({ value, label, onCommit, onInvalid, disabled }: {
  value: number;
  label: string;
  /** false: the range didn't change (the same value, or an end below the start): the field shows its value again */
  onCommit: (z: number) => boolean;
  onInvalid: () => void;
  disabled: boolean;
}) {
  const { i18n } = useTranslation();
  const shown = toLocaleDecimal(String(Number(value.toFixed(6))), i18n.language);
  const [text, setText] = useState(shown);
  const commit = () => {
    const z = parseZ(text);
    if (z === null) onInvalid();
    if (z === null || !onCommit(z)) setText(shown);
  };
  return (
    <span className="relative flex-1 min-w-0">
      <input
        type="text"
        inputMode="decimal"
        aria-label={label}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && commit()}
        className="w-full h-6 bg-bambu-dark border border-bambu-dark-tertiary rounded pl-1.5 pr-7 text-[12px] text-white disabled:opacity-60"
      />
      <span className="absolute right-1.5 top-1 text-[11px] text-bambu-gray pointer-events-none">mm</span>
    </span>
  );
}

/** The "Layers" row's editor (ObjectLayers: every range, or the selected one) and a range's settings (TabPrintLayer). */
function RangeSettings({
  layout,
  doc,
  draft,
  obj,
  range,
  editing,
  onSelectRange,
}: {
  layout: OrcaLayout;
  doc: WorkspaceDocument;
  draft: Draft;
  obj: WorkspaceObject;
  /** null: the Layers row, all of them */
  range: number | null;
  editing?: RangeEditing;
  /** the range now selected after an edit moved it (null: the Layers row; undefined: the object, none left) */
  onSelectRange: (range: number | null | undefined) => void;
}) {
  const { t, i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const [invalid, setInvalid] = useState(false);
  const ranges = rangesOf(obj, editing?.edits);
  const defaults = editing?.defaults(obj) ?? {};
  const current = range != null ? ranges[range] : undefined;
  const shown = range == null ? ranges.map((r, k) => [r, k] as const) : current ? [[current, range] as const] : [];
  /** the ranges as edited; the selected range follows its edit to where it is now (Orca re-selects it) */
  const apply = (next: LayerRange[] | null, moved?: LayerRange, key?: string) => {
    if (!next || !editing) return false;
    editing.set(obj, next, key);
    if (range != null && moved) onSelectRange(next.indexOf(moved));
    return true;
  };
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-3 pt-1.5 pb-2 border-b border-bambu-dark-tertiary space-y-1" data-testid="height-ranges">
        {shown.map(([r, k]) => (
          <div key={`${k}-${r.min_z}-${r.max_z}`} className="flex items-center gap-1.5 text-[12px] text-bambu-gray-light">
            <img src={orcaIcon('height_range_layer')} alt="" className="w-3.5 h-3.5" aria-hidden />
            <span className="w-14 flex-shrink-0">{tr('Range')}</span>
            <ZInput
              value={r.min_z}
              label={`${rangeLabel(r, tr('Range'), tr('mm'))}: Z min`}
              disabled={!editing}
              onInvalid={() => setInvalid(true)}
              onCommit={(z) => {
                setInvalid(false);
                const next = editMin(ranges, k, z);
                return apply(next, next?.find((x) => x.options === r.options && x.min_z === z));
              }}
            />
            <span aria-hidden>-</span>
            <ZInput
              value={r.max_z}
              label={`${rangeLabel(r, tr('Range'), tr('mm'))}: Z max`}
              disabled={!editing}
              onInvalid={() => setInvalid(true)}
              onCommit={(z) => {
                setInvalid(false);
                const next = editMax(ranges, k, z);
                return apply(next, next?.find((x) => x.options === r.options && x.max_z === z));
              }}
            />
            {editing && (
              <>
                <button
                  type="button"
                  title={tr('Remove height range')}
                  aria-label={`${tr('Remove height range')}: ${rangeLabel(r, tr('Range'), tr('mm'))}`}
                  onClick={() => {
                    const next = removeRange(ranges, k);
                    editing.set(obj, next);
                    // del_layer_range: the last one gone, the object is selected
                    if (!next.length) onSelectRange(undefined);
                    else if (range != null) onSelectRange(null);
                  }}
                  className="w-5 h-5 flex-shrink-0 flex items-center justify-center rounded hover:bg-bambu-dark-tertiary"
                >
                  <img src={orcaIcon('delete')} alt="" className="w-3.5 h-3.5" aria-hidden />
                </button>
                <button
                  type="button"
                  title={tr('Add height range')}
                  aria-label={`${tr('Add height range')}: ${rangeLabel(r, tr('Range'), tr('mm'))}`}
                  onClick={() => {
                    const next = addAfter(ranges, k, defaults, editing.minLayerHeight);
                    if (next) editing.set(obj, next);
                    if (range != null) onSelectRange(null); // Orca selects the Layers row
                  }}
                  className="w-5 h-5 flex-shrink-0 flex items-center justify-center rounded hover:bg-bambu-dark-tertiary"
                >
                  <img src={orcaIcon('add')} alt="" className="w-3.5 h-3.5" aria-hidden />
                </button>
              </>
            )}
          </div>
        ))}
        {invalid && (
          <p role="alert" className="text-[12px] text-red-400">
            {tr('Invalid numeric.')}
          </p>
        )}
        {!editing && <p className="text-[11px] text-bambu-gray">{t('farm.workspace.readOnly')}</p>}
      </div>
      {current && range != null && (
        <div className="flex-1 min-h-0">
          <SettingsPanel
            layout={layout}
            section="process"
            settings={doc.settings}
            draft={draft}
            editable={!!editing}
            onChange={(setting, value) =>
              apply(withOption(ranges, range, setting.key, Array.isArray(value) ? value.join(',') : value, defaults), undefined, `${obj.id}|${range}|${setting.key}`)
            }
            onReset={(key) => apply(withOption(ranges, range, key, null, defaults))}
            object={{ own: current.options, range: true }}
            header={
              <p className="text-[12px] text-bambu-gray truncate">
                {obj.name} › {rangeLabel(current, tr('Range'), tr('mm'))}
              </p>
            }
          />
        </div>
      )}
    </div>
  );
}

/** OrcaSlicer's "Plate Settings" tab (TabPrintPlate): the plate's own value of each, else the project's (greyed).
 * Bed type, print sequence and spiral vase change here, their reset arrow back to the project's; the filament
 * sequences are Auto or Customize, which opens the dialog's sequences. The skirt's start point stays read only:
 * Orca keeps it per plate only while the project is open (bbs_3mf writes no key for it). */
function PlateSettings({
  layout,
  plate,
  byKey,
  draft,
  editing,
}: {
  layout: OrcaLayout;
  plate: WorkspacePlate;
  byKey: Map<string, WorkspaceSetting>;
  draft: Draft;
  editing?: OwnEditing;
}) {
  const { t, i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const own = plateOwn(layout, plate, editing?.edits);
  const metaKey = Object.fromEntries(Object.entries(layout.plate_keys ?? {}).map(([m, opt]) => [opt, m]));
  const apply = editing?.plate && ((values: PlateValues) => editing.plate?.apply(plate, values));
  const sequences: Record<string, string> = {
    first_layer_print_sequence: 'First layer filament sequence',
    other_layers_print_sequence: 'Other layer filament sequence',
  };
  return (
    <div className="px-3 pt-1">
      <div className="flex border-b border-bambu-dark-tertiary" role="tablist">
        <span role="tab" aria-selected className="-mb-px py-1.5 text-[13px] border-b-2 border-bambu-green text-white font-semibold">
          {tr('Plate Settings')}
        </span>
      </div>
      {!apply && <p className="text-[11px] text-bambu-gray mt-1">{t('farm.workspace.readOnly')}</p>}
      {Object.values(layout.plate_keys ?? {}).map((key) => {
        const reset = apply && own[key] != null && (
          <button
            type="button"
            title={t('farm.workspace.reset')}
            aria-label={`${t('farm.workspace.reset')}: ${layout.options[key] ? tr(layout.options[key].label) : key}`}
            onClick={() => apply({ [metaKey[key]]: null })}
            className="w-5 h-5 flex items-center justify-center rounded text-bambu-gray hover:text-white"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
        );
        if (key in sequences) {
          const label = tr(sequences[key]);
          return (
            <div key={key} className={`flex items-center gap-2 min-h-[30px] py-0.5 pl-4 ${own[key] != null ? '' : 'opacity-60'}`} data-testid={`plate-${key}`}>
              <span className={`flex-1 min-w-0 text-[13px] leading-tight ${own[key] != null ? 'text-[#F1754E]' : 'text-bambu-gray-light'}`}>{label}</span>
              <EnumSelect
                label={label}
                value={own[key] != null ? 'custom' : 'auto'}
                options={[
                  { value: 'auto', label: tr('Auto') },
                  { value: 'custom', label: tr('Customize') },
                ]}
                onChange={(v) => (v === 'auto' ? apply?.({ [metaKey[key]]: null }) : editing?.plate?.customize(plate))}
              />
              <span className="w-5" aria-hidden />
            </div>
          );
        }
        const option = layout.options[key];
        const global = byKey.get(key);
        const label = option ? orcaText(layout, option.label, i18n.language) : key;
        const value = own[key] ?? (global ? (key in draft ? draft[key] : global.value) : undefined);
        const enumLabel = (value: string, fallback: string) =>
          orcaText(layout, option?.enum?.find((e) => e.value === value)?.label ?? fallback, i18n.language);
        // the project's setting, else (a key the project doesn't hold) the slicer's own definition of it
        const base: WorkspaceSetting | undefined =
          global ??
          (option && {
            key,
            label,
            section: 'process',
            type: ({ coBool: 'bool', coEnum: 'enum', coInt: 'int', coFloat: 'float' } as const)[option.type] ?? 'string',
            unit: option.unit ?? null,
            options: option.enum?.map((e) => ({ value: e.value, label: e.label })) ?? null,
            value: '',
            editable: false,
            readonly_reason: null,
            differs_from_system: null,
            overrides: [],
          });
        const setting: WorkspaceSetting | undefined = base && {
          ...base,
          label,
          editable: !!apply && key !== 'skirt_start_angle',
          options: base.options?.map((o) => ({ ...o, label: enumLabel(o.value, o.label) })) ?? null,
        };
        return (
          <div key={key} className={`flex items-center gap-2 min-h-[30px] py-0.5 pl-4 ${own[key] != null ? '' : 'opacity-60'}`} data-testid={`plate-${key}`}>
            <span className={`flex-1 min-w-0 text-[13px] leading-tight ${own[key] != null ? 'text-[#F1754E]' : 'text-bambu-gray-light'}`}>
              {label}
              {own[key] == null && <span className="block text-[11px] text-bambu-gray">{t('farm.workspace.fromProject')}</span>}
            </span>
            {setting && value != null ? (
              <SettingControl
                setting={setting}
                value={value}
                errors={[]}
                // a plate's spiral vase is written as Orca writes it, true or false
                onChange={(v) => apply?.({ [metaKey[key]]: setting.type === 'bool' ? (ON.has(String(v)) ? 'true' : 'false') : String(v) })}
              />
            ) : (
              <span className="w-[120px] text-[13px] text-bambu-gray truncate">{own[key] ?? '—'}</span>
            )}
            {reset || <span className="w-5" aria-hidden />}
          </div>
        );
      })}
    </div>
  );
}
