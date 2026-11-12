// Fork (printfarm): OrcaSlicer's object table, "Object/Part Settings" (GUI_ObjectTable.cpp, v2.5.0): every object,
// and its parts under it, with its Printable, Plate, Name and Filament and six process settings, each setting with
// the reset icon when it differs from what the row inherits; Plate, Name and Filament sort the rows. The side panel
// (GUI_ObjectTableSettings.cpp) shows the settings of the selected cell's category, its own in orange with their
// reset. Ctrl+C on a cell and Ctrl+V on cells of the same column copy its value (ObjectGrid::paste_data). Every
// change is one step of the workspace's undo history; the rules are in objectGrid.ts.
// Name and Printable are shown, not edited: renaming and the printable switch belong to the object list (later).
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Selection } from './objects';
import { ownSettings, targetOwn } from './objects';
import type { WorkspaceDocument, WorkspaceSetting } from './api';
import { type Draft, valueErrors } from './draft';
import { Dialog } from './Dialogs';
import { Chip, type OwnEditing } from './ObjectsPanel';
import { type OrcaLayout, orcaIcon, orcaText } from './orcaLayout';
import { EnumSelect, SettingControl } from './SettingControl';
import {
  TABLE_BRIMS,
  TABLE_COLUMNS,
  type TableColumn,
  type TableRow,
  type TargetEdits,
  cellEdit,
  cellEditable,
  filamentCellEdit,
  inherited,
  ownEdit,
  platesOf,
  rowFilament,
  rowValue,
  sameSetting,
  sideSettings,
  sortRows,
  tableRows,
} from './objectGrid';
import type { ObjectStats } from './viewTools';
import '../i18n';

const rowKey = (r: TableRow) => `${r.obj.id ?? ''}/${r.partId ?? ''}`;

interface Props {
  layout: OrcaLayout;
  doc: WorkspaceDocument;
  draft: Draft;
  colours: string[];
  stats: Map<string, ObjectStats>;
  /** absent: the file can't be edited, the table only shows */
  editing?: OwnEditing;
  /** the row selected when it opens (``Popup(obj_idx, vol_idx)``); null: none */
  start: { objectId: string; partId: string | null } | null;
  /** a cell chosen: its object (part) is selected in the list and the 3D view too */
  onSelect: (selection: Exclude<Selection, null>) => void;
  onClose: () => void;
}

export function ObjectTableDialog({ layout, doc, draft, colours, stats, editing, start, onSelect, onClose }: Props) {
  const { t, i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const edits = editing?.edits;
  const byKey = useMemo(() => new Map(doc.settings.map((s) => [s.key, s])), [doc.settings]);
  const plates = useMemo(() => platesOf(doc), [doc]);
  const all = useMemo(() => new Map(tableRows(doc).map((r) => [rowKey(r), r])), [doc]);
  // the order changes when a header is clicked, not as values change (as Orca's)
  const [order, setOrder] = useState(() => sortRows([...all.values()], plates, null, false).map(rowKey));
  const [sorted, setSorted] = useState<{ col: string; desc: boolean } | null>(null);
  const rows = order.map((k) => all.get(k)).filter((r): r is TableRow => !!r);
  const first = start ? rows.findIndex((r) => r.obj.id === start.objectId && r.partId === (start.partId ?? null)) : -1;
  const [cursor, setCursor] = useState<{ row: number; col: number } | null>(first >= 0 ? { row: first, col: 2 } : null);
  const [anchor, setAnchor] = useState<{ row: number; col: number } | null>(null);
  const copied = useRef<{ col: number; value: string; many: boolean } | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const cells = useRef<HTMLTableSectionElement>(null);

  useEffect(() => {
    if (first >= 0) cells.current?.querySelector(`[data-row="${first}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [first]);

  const block = cursor && {
    top: Math.min(cursor.row, anchor?.row ?? cursor.row),
    bottom: Math.max(cursor.row, anchor?.row ?? cursor.row),
    left: Math.min(cursor.col, anchor?.col ?? cursor.col),
    right: Math.max(cursor.col, anchor?.col ?? cursor.col),
  };
  const inBlock = (r: number, c: number) => !!block && r >= block.top && r <= block.bottom && c >= block.left && c <= block.right;

  const nameOf = (r: TableRow) => (r.partId ? (r.obj.parts.find((p) => p.id === r.partId)?.name ?? '') : (r.obj.name ?? `#${r.obj.id}`));
  const choose = (row: number, col: number, extend = false) => {
    setWarning(null);
    if (extend && cursor) {
      setAnchor((a) => a ?? cursor);
    } else setAnchor(null);
    setCursor({ row, col });
    const r = rows[row];
    if (r && !extend)
      onSelect({ kind: 'object', id: r.obj.id ?? '', plate: plates.get(r.obj.id ?? '') ?? null, partId: r.partId, part: r.partId ? nameOf(r) : null });
  };

  const sortBy = (col: string) => {
    const desc = sorted?.col === col && !sorted.desc;
    setOrder(sortRows([...all.values()], plates, col, desc, edits).map(rowKey));
    setSorted(desc ? null : { col, desc: false }); // a second click sorts down, the third up again (m_sort_col)
    setCursor(null);
    setAnchor(null);
  };

  /** The text of a cell, as Ctrl+C copies it. */
  const cellText = (r: TableRow, col: TableColumn): string => {
    if (col.id === 'extruder') return String(rowFilament(r, edits));
    if (col.id === 'name') return nameOf(r);
    if (col.id === 'plate') return String(plates.get(r.obj.id ?? '') ?? '');
    if (col.id === 'printable') return (stats.get(r.obj.id ?? '')?.printable ?? true) ? '1' : '0';
    const setting = byKey.get(col.id);
    return setting ? rowValue(r, setting, draft, edits) : '';
  };
  /** The edits a value makes in a cell (nothing when the cell can't change). */
  const editOf = (r: TableRow, col: TableColumn, value: string, current = edits): TargetEdits => {
    const setting = byKey.get(col.id);
    if (!editing || !cellEditable(r, col, setting)) return {};
    if (col.id === 'extruder') {
      const n = Number(value);
      return Number.isInteger(n) && n >= 1 && n <= Math.max(colours.length, 1) ? filamentCellEdit(r, n, current) : {};
    }
    return setting ? cellEdit(r, setting, value, draft, current) : {};
  };
  const setCell = (r: TableRow, col: TableColumn, value: string) => {
    const out = Object.entries(editOf(r, col, value));
    // a filament change and its parts' together: one undo step
    if (out.length > 1) return editing?.setTargets?.(Object.fromEntries(out));
    // typing in one cell is one undo step too (draft.ts coalesces a key's edits)
    for (const [target, keys] of out) for (const [key, v] of Object.entries(keys)) editing?.set(target, key, v);
  };

  const onKey = (e: React.KeyboardEvent) => {
    const inField = (e.target as HTMLElement).closest('input, textarea, [role="combobox"], [role="listbox"]');
    if (!cursor || inField) return;
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && (e.key === 'c' || e.key === 'C') && block) {
      e.preventDefault();
      copied.current = {
        col: cursor.col,
        value: cellText(rows[cursor.row], TABLE_COLUMNS[cursor.col]),
        many: block.top !== block.bottom || block.left !== block.right,
      };
    } else if (ctrl && (e.key === 'v' || e.key === 'V') && block && copied.current) {
      e.preventDefault();
      const src = copied.current;
      if (src.many) return setWarning(tr('Copying multiple cells is not supported.'));
      if (block.left !== block.right || block.left !== src.col) return setWarning(tr('One cell can only be copied to one or more cells in the same column.'));
      const col = TABLE_COLUMNS[src.col];
      let next = edits;
      const out: TargetEdits = {};
      for (let i = block.top; i <= block.bottom; i++) {
        for (const [target, keys] of Object.entries(editOf(rows[i], col, src.value, next))) {
          out[target] = { ...out[target], ...keys };
          next = { ...next, [target]: { ...(next?.[target] ?? {}), ...(keys as Record<string, string | null>) } };
        }
      }
      if (Object.keys(out).length) editing?.setTargets?.(out); // the cells pasted: one undo step
    } else {
      const move: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
      const d = move[e.key];
      if (!d) return;
      e.preventDefault();
      const row = Math.min(Math.max(cursor.row + d[0], 0), rows.length - 1);
      const col = Math.min(Math.max(cursor.col + d[1], 0), TABLE_COLUMNS.length - 1);
      choose(row, col, e.shiftKey);
    }
  };

  const header = (col: TableColumn) => (col.sort ? `${tr(col.label)} ↑↓` : tr(col.label));
  const chosen = cursor ? rows[cursor.row] : undefined;
  const chosenCol = cursor ? TABLE_COLUMNS[cursor.col] : undefined;

  return (
    <Dialog title={tr('Object/Part Settings')} wide="full" onClose={onClose}>
      {warning && (
        <p role="alert" className="mx-4 mt-2 rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1 text-[12px] text-amber-200">
          {warning}
        </p>
      )}
      <div className="flex min-h-0 flex-1 h-[70vh]">
        <div className="min-w-0 flex-1 overflow-auto" onKeyDown={onKey}>
          <table role="grid" aria-label={tr('Object/Part Settings')} className="border-collapse text-[12px]">
            <thead className="sticky top-0 z-10 bg-[#3a3a3a]">
              <tr>
                {TABLE_COLUMNS.map((col) => (
                  <th key={col.id} scope="col" className="px-2 py-1.5 text-left font-semibold text-white whitespace-nowrap">
                    {col.sort ? (
                      <button type="button" onClick={() => sortBy(col.id)} className="hover:text-bambu-green">
                        {header(col)}
                      </button>
                    ) : (
                      header(col)
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody ref={cells}>
              {rows.map((r, ri) => (
                <tr key={rowKey(r)} data-row={ri} className={r.partId ? '' : 'border-t border-bambu-dark-tertiary'}>
                  {TABLE_COLUMNS.map((col, ci) => (
                    <td
                      key={col.id}
                      tabIndex={-1}
                      aria-selected={inBlock(ri, ci)}
                      data-testid={`cell-${rowKey(r)}-${col.id}`}
                      onMouseDown={(e) => {
                        if (!(e.target as HTMLElement).closest('input, button, [role="combobox"]')) e.currentTarget.focus();
                        choose(ri, ci, e.shiftKey);
                      }}
                      className={`px-2 py-0.5 h-8 whitespace-nowrap outline-none ${
                        cursor?.row === ri && cursor.col === ci ? 'ring-1 ring-inset ring-bambu-green' : ''
                      } ${inBlock(ri, ci) ? 'bg-[#BFE1DE]/15' : ''}`}
                    >
                      <Cell
                        layout={layout}
                        row={r}
                        col={col}
                        setting={byKey.get(col.id)}
                        draft={draft}
                        editing={editing}
                        colours={colours}
                        plate={plates.get(r.obj.id ?? '') ?? null}
                        printable={stats.get(r.obj.id ?? '')?.printable ?? true}
                        name={nameOf(r)}
                        onChange={(value) => setCell(r, col, value)}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {chosen && chosenCol && chosenCol.id !== 'extruder' && (
          <aside className="w-[360px] flex-shrink-0 overflow-y-auto border-l-2 border-[#A6A9AA] px-2 py-1" aria-label={nameOf(chosen)}>
            <SidePanel layout={layout} row={chosen} category={chosenCol.category} byKey={byKey} draft={draft} editing={editing} />
          </aside>
        )}
      </div>
      {!editing && <p className="px-4 py-1.5 text-[11px] text-bambu-gray">{t('farm.workspace.readOnly')}</p>}
    </Dialog>
  );
}

function ResetIcon({ title, onClick, disabled }: { title: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className="w-5 h-5 flex-shrink-0 flex items-center justify-center rounded hover:bg-white/10 disabled:opacity-30 disabled:hover:bg-transparent"
    >
      <img src={orcaIcon('lock_normal')} alt="" className="w-4 h-4" aria-hidden />
    </button>
  );
}

/** A setting as the table edits it: the column's (or side panel's) label, the Brim column's seven choices, the
 * enum labels in the UI language. */
function asShown(layout: OrcaLayout, setting: WorkspaceSetting, label: string, language: string, brims = false): WorkspaceSetting {
  const enumLabel = (v: string, fallback: string) => orcaText(layout, layout.options[setting.key]?.enum?.find((e) => e.value === v)?.label ?? fallback, language);
  const options = setting.options?.filter((o) => !brims || TABLE_BRIMS.includes(o.value)).map((o) => ({ ...o, label: enumLabel(o.value, o.label) }));
  return { ...setting, label, options: options ?? null };
}

function Cell({
  layout,
  row,
  col,
  setting,
  draft,
  editing,
  colours,
  plate,
  printable,
  name,
  onChange,
}: {
  layout: OrcaLayout;
  row: TableRow;
  col: TableColumn;
  setting: WorkspaceSetting | undefined;
  draft: Draft;
  editing?: OwnEditing;
  colours: string[];
  plate: number | null;
  printable: boolean;
  name: string;
  onChange: (value: string) => void;
}) {
  const { i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const edits = editing?.edits;
  const editable = !!editing && cellEditable(row, col, setting);
  const label = `${tr(col.label)} — ${name}`;
  if (col.id === 'printable') return <input type="checkbox" disabled checked={printable} aria-label={label} className="w-4 h-4 accent-bambu-green opacity-70" />;
  if (col.id === 'plate') return <span className="text-bambu-gray-light">{plate ?? tr('Outside')}</span>;
  if (col.id === 'name') return <span className={`block max-w-[16rem] truncate ${row.partId ? 'pl-4 text-bambu-gray-light' : 'text-white'}`} title={name}>{name}</span>;
  if (col.id === 'extruder') {
    const n = rowFilament(row, edits);
    const chip = <Chip colour={colours[n - 1]} n={n} />;
    if (!editable) return <span className="inline-flex items-center gap-1.5">{chip}</span>;
    const count = Math.max(colours.length, 1);
    return (
      <span className="inline-flex items-center gap-1.5">
        {chip}
        <EnumSelect
          label={label}
          value={String(n)}
          options={Array.from({ length: count }, (_, i) => ({ value: String(i + 1), label: String(i + 1) }))}
          onChange={onChange}
        />
      </span>
    );
  }
  if (!setting) return <span className="text-bambu-gray">—</span>;
  const value = rowValue(row, setting, draft, edits);
  const many = Array.isArray(setting.value);
  const shown: string | string[] = many ? value.split(',') : value;
  const differs = editable && !sameSetting(value, inherited(row, setting, draft, edits));
  return (
    <span className="inline-flex items-center gap-1">
      {/* read only (a part's object-only column): greyed, as the settings panel shows it */}
      <SettingControl
        setting={{ ...asShown(layout, setting, label, i18n.language, col.id === 'brim_type'), editable }}
        value={shown}
        index={many ? 0 : undefined}
        errors={editable ? valueErrors(setting, shown) : []}
        onChange={(v) => onChange(Array.isArray(v) ? v.join(',') : v)}
      />
      {/* the reset column: back to what the row inherits */}
      {differs ? <ResetIcon title={tr('Reset parameter')} onClick={() => onChange(inherited(row, setting, draft, edits))} /> : <span className="w-5" aria-hidden />}
    </span>
  );
}

/** ObjectTableSettings: the selected cell's category for its row, the row's own values in orange with a reset. */
function SidePanel({
  layout,
  row,
  category,
  byKey,
  draft,
  editing,
}: {
  layout: OrcaLayout;
  row: TableRow;
  category: string;
  byKey: Map<string, WorkspaceSetting>;
  draft: Draft;
  editing?: OwnEditing;
}) {
  const { i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const edits = editing?.edits;
  const own = row.partId ? targetOwn(row.obj, row.partId, edits) : ownSettings(row.obj, null, edits);
  const editable = !!editing && cellEditable(row, { id: '', label: '', category, setting: true }, { editable: true } as WorkspaceSetting);
  const put = (target: TargetEdits) => {
    for (const [to, keys] of Object.entries(target)) for (const [key, v] of Object.entries(keys)) editing?.set(to, key, v);
  };
  return (
    <>
      {sideSettings(layout, category, !!row.partId, own).map((group) => (
        <section key={group.category} className="mb-2">
          <h3 className="py-1 text-[13px] font-semibold text-white border-b border-bambu-dark-tertiary">{tr(group.category)}</h3>
          {group.keys.map(({ key, label }) => {
            const setting = byKey.get(key);
            const text = tr(label ?? layout.options[key]?.label ?? key);
            if (!setting) return null;
            const value = rowValue(row, setting, draft, edits);
            const many = Array.isArray(setting.value);
            const shown = many ? value.split(',') : value;
            const mine = key in own;
            return (
              <div key={key} className="flex items-center gap-2 min-h-[30px] py-0.5" data-testid={`side-${key}`}>
                <span className={`flex-1 min-w-0 text-[12px] leading-tight ${mine ? 'text-[#F1754E]' : 'text-bambu-gray-light'}`}>{text}</span>
                <SettingControl
                  setting={asShown(layout, { ...setting, editable: editable && setting.editable }, text, i18n.language)}
                  value={shown}
                  errors={valueErrors(setting, shown)}
                  onChange={(v) => put(cellEdit(row, setting, Array.isArray(v) ? v.join(',') : v, draft, edits))}
                />
                <ResetIcon title={tr('Reset parameter')} disabled={!editable || !mine} onClick={() => put(ownEdit(row, key, null, edits))} />
              </div>
            );
          })}
        </section>
      ))}
    </>
  );
}
