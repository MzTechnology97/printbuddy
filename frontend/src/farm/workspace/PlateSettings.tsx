// Fork (printfarm): OrcaSlicer's "Plate Settings" dialog (PlateSettingsDialog.cpp, v2.5.0): the plate's name, bed
// type, print sequence and spiral vase ("Same as Global" for each), the first layer's filament order and the other
// layers' (ranges "Layer N to M", each with its order). OK makes it one step of the workspace's undo history; the
// rules are in plates.ts. Turning spiral vase on asks first, as Orca, and sets the plate's objects to suit it.
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { WorkspaceDocument, WorkspacePlate } from './api';
import { Button } from '../../components/Button';
import { Dialog } from './Dialogs';
import type { OwnEdits } from './draft';
import { Chip } from './ObjectsPanel';
import { type OrcaLayout, orcaText } from './orcaLayout';
import { LAYER_END, LAYER_MIN, type LayerRange, type PlateKey, formatRanges, orderOf, parseRanges, plateMeta } from './plates';
import { EnumSelect } from './SettingControl';
import '../i18n';

export type PlateValues = Partial<Record<PlateKey, string | null>>;

/** DragCanvas: the filaments in their order; drag one, or move it with the arrow keys. */
function OrderList({ order, colours, label, onChange }: { order: number[]; colours: string[]; label: string; onChange: (o: number[]) => void }) {
  const [dragged, setDragged] = useState<number | null>(null);
  const move = (from: number, to: number) => {
    if (to < 0 || to >= order.length || from === to) return;
    const next = [...order];
    next.splice(to, 0, ...next.splice(from, 1));
    onChange(next);
  };
  return (
    <ol className="flex gap-1" aria-label={label}>
      {order.map((n, i) => (
        <li
          key={n}
          draggable
          tabIndex={0}
          aria-label={`${n}`}
          onDragStart={() => setDragged(i)}
          onDragOver={(e) => e.preventDefault()}
          onDrop={() => dragged != null && move(dragged, i)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
              e.preventDefault();
              move(i, i + (e.key === 'ArrowLeft' ? -1 : 1));
            }
          }}
          className="cursor-grab rounded outline-none focus-visible:ring-1 focus-visible:ring-bambu-green"
        >
          <Chip colour={colours[n - 1]} n={n} />
        </li>
      ))}
    </ol>
  );
}

interface RangeDraft {
  from: string;
  /** null: "End" */
  to: string | null;
  order: number[];
}

export function PlateSettingsDialog({
  layout,
  doc,
  plate,
  edits,
  colours,
  onlyLayers = false,
  onApply,
  onClose,
}: {
  layout: OrcaLayout;
  doc: WorkspaceDocument;
  plate: WorkspacePlate;
  edits?: OwnEdits;
  colours: string[];
  /** TabPrintPlate's "Customize": only the filament sequences */
  onlyLayers?: boolean;
  onApply: (values: PlateValues) => void;
  onClose: () => void;
}) {
  const { i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  const filaments = Math.max(colours.length, 1);
  const meta = plateMeta(plate, edits);
  const [name, setName] = useState(meta.plater_name ?? '');
  const [bed, setBed] = useState(meta.bed_type ?? '');
  const [sequence, setSequence] = useState(meta.print_sequence ?? '');
  const [spiral, setSpiral] = useState(meta.spiral_mode == null ? '' : meta.spiral_mode === 'true' ? 'on' : 'off');
  const [firstCustom, setFirstCustom] = useState(!!meta.first_layer_print_sequence);
  const [firstOrder, setFirstOrder] = useState(() => orderOf(meta.first_layer_print_sequence, filaments));
  const [otherCustom, setOtherCustom] = useState(!!meta.other_layers_print_sequence);
  const [ranges, setRanges] = useState<RangeDraft[]>(() => {
    const parsed = parseRanges(meta.other_layers_print_sequence, Number(meta.other_layers_print_sequence_nums) || undefined, filaments);
    const start = parsed.length ? parsed : [{ from: LAYER_MIN, to: LAYER_END, order: orderOf(undefined, filaments) }];
    return start.map((r) => ({ from: String(r.from), to: r.to >= LAYER_END ? null : String(r.to), order: r.order }));
  });
  const [error, setError] = useState<string | null>(null);
  const mixed = [doc.settings.find((s) => s.key === 'filament_is_mixed')?.value ?? []].flat().some((v) => v === '1' || v === 'true');
  const enumOptions = (key: string) => (layout.options[key]?.enum ?? []).map((e) => ({ value: e.value, label: tr(e.label) }));
  const row = (label: string, control: React.ReactNode) => (
    <div className="flex items-center gap-3 py-1">
      <span className="flex-1 text-[13px] text-bambu-gray-light">{label}</span>
      {control}
    </div>
  );

  const ok = () => {
    let out: LayerRange[] = [];
    if (otherCustom) {
      const parsed = ranges.map((r) => ({ from: Number(r.from), to: r.to == null ? LAYER_END : Number(r.to), order: r.order }));
      if (parsed.some((r) => !Number.isInteger(r.from) || r.from < LAYER_MIN || !Number.isInteger(r.to) || r.to < LAYER_MIN)) {
        setError(tr('Please input layer value (>= 2).'));
        return;
      }
      // as the layer inputs do: a range never ends before it begins
      out = parsed.map((r) => ({ ...r, to: Math.max(r.to, r.from) }));
    }
    onApply({
      plater_name: name,
      bed_type: bed || null,
      print_sequence: sequence || null,
      spiral_mode: spiral === '' ? null : spiral === 'on' ? 'true' : 'false',
      first_layer_print_sequence: firstCustom ? firstOrder.join(' ') : null,
      other_layers_print_sequence: otherCustom ? formatRanges(out) : null,
    });
    onClose();
  };

  return (
    <Dialog title={tr('Plate Settings')} onClose={onClose}>
      <div className="px-6 py-3 overflow-y-auto">
        {!onlyLayers && (
          <>
            {row(
              tr('Plate name'),
              <input
                type="text"
                aria-label={tr('Plate name')}
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="h-7 w-[240px] rounded border border-bambu-dark-tertiary bg-bambu-dark px-2 text-[13px] text-white outline-none focus:border-bambu-green"
              />,
            )}
            {row(
              tr('Bed type'),
              <EnumSelect label={tr('Bed type')} value={bed} options={[{ value: '', label: tr('Same as Global Plate Type') }, ...enumOptions('curr_bed_type')]} onChange={setBed} />,
            )}
            {row(
              tr('Print sequence'),
              <EnumSelect
                label={tr('Print sequence')}
                value={sequence}
                options={[{ value: '', label: tr('Same as Global Print Sequence') }, ...enumOptions('print_sequence')]}
                onChange={setSequence}
              />,
            )}
            {row(
              tr('Spiral vase'),
              <EnumSelect
                label={tr('Spiral vase')}
                value={spiral}
                options={[
                  { value: '', label: tr('Same as Global') },
                  { value: 'on', label: tr('Enable') },
                  { value: 'off', label: tr('Disable') },
                ]}
                onChange={setSpiral}
              />,
            )}
          </>
        )}
        {row(
          tr('First layer filament sequence'),
          <EnumSelect
            label={tr('First layer filament sequence')}
            value={firstCustom ? 'custom' : 'auto'}
            options={[
              { value: 'auto', label: tr('Auto') },
              { value: 'custom', label: tr('Customize') },
            ]}
            onChange={(v) => !mixed && setFirstCustom(v === 'custom')}
          />,
        )}
        {firstCustom && (
          <div className="flex justify-end pb-2">
            <OrderList order={firstOrder} colours={colours} label={tr('First layer filament sequence')} onChange={setFirstOrder} />
          </div>
        )}
        {row(
          tr('Other layer filament sequence'),
          <EnumSelect
            label={tr('Other layer filament sequence')}
            value={otherCustom ? 'custom' : 'auto'}
            options={[
              { value: 'auto', label: tr('Auto') },
              { value: 'custom', label: tr('Customize') },
            ]}
            onChange={(v) => !mixed && setOtherCustom(v === 'custom')}
          />,
        )}
        {otherCustom && (
          <div className="space-y-2 pb-2">
            {ranges.map((r, i) => {
              const set = (next: Partial<RangeDraft>) => setRanges((all) => all.map((x, j) => (j === i ? { ...x, ...next } : x)));
              const inputClass = 'h-7 w-20 rounded border border-bambu-dark-tertiary bg-bambu-dark px-2 text-[13px] text-white outline-none focus:border-bambu-green';
              return (
                <div key={i} className="flex items-center gap-2 text-[13px] text-bambu-gray-light" data-testid={`range-${i}`}>
                  <span>{tr('Layer')}</span>
                  <input type="text" inputMode="numeric" aria-label={`${tr('Layer')} ${i + 1}`} value={r.from} onChange={(e) => set({ from: e.target.value.replace(/\D/g, '') })} className={inputClass} />
                  <span>{tr('to')}</span>
                  {r.to == null ? (
                    <EnumSelect
                      label={`${tr('to')} ${i + 1}`}
                      value="end"
                      options={[
                        { value: 'end', label: tr('Layer range\u0004End') },
                        { value: 'custom', label: tr('Customize') },
                      ]}
                      onChange={(v) => v === 'custom' && set({ to: r.from || String(LAYER_MIN) })}
                    />
                  ) : (
                    <input type="text" inputMode="numeric" aria-label={`${tr('to')} ${i + 1}`} value={r.to} onChange={(e) => set({ to: e.target.value.replace(/\D/g, '') })} className={inputClass} />
                  )}
                  <span className="ml-auto">
                    <OrderList order={r.order} colours={colours} label={`${tr('Other layer filament sequence')} ${i + 1}`} onChange={(order) => set({ order })} />
                  </span>
                </div>
              );
            })}
            <div className="flex justify-end gap-1">
              <button
                type="button"
                aria-label="+"
                onClick={() => setRanges((all) => [...all, { from: '', to: null, order: orderOf(undefined, filaments) }])}
                className="h-6 w-6 rounded border border-bambu-dark-tertiary text-white hover:border-bambu-green"
              >
                +
              </button>
              <button
                type="button"
                aria-label="−"
                disabled={ranges.length < 2}
                onClick={() => setRanges((all) => all.slice(0, -1))}
                className="h-6 w-6 rounded border border-bambu-dark-tertiary text-white hover:border-bambu-green disabled:opacity-40"
              >
                −
              </button>
            </div>
          </div>
        )}
        {mixed && <p className="py-1 text-[12px] text-[#FF6F00]">{tr('The filament list contains mixed filaments. Custom filament sequence will not take effect.')}</p>}
        {error && (
          <p role="alert" className="py-1 text-[12px] text-red-400">
            {error}
          </p>
        )}
      </div>
      <div className="flex justify-end gap-2 border-t border-bambu-dark-tertiary p-3">
        <Button variant="secondary" onClick={onClose}>
          {tr('Cancel')}
        </Button>
        <Button onClick={ok}>{tr('OK')}</Button>
      </div>
    </Dialog>
  );
}

/** ConfigManipulation::show_spiral_mode_settings_dialog for a plate: Yes changes the objects and turns it on. */
export function SpiralConfirm({ layout, onYes, onNo }: { layout: OrcaLayout; onYes: () => void; onNo: () => void }) {
  const { i18n } = useTranslation();
  const tr = (text: string) => orcaText(layout, text, i18n.language);
  return (
    <Dialog title={tr('Spiral vase')} onClose={onNo}>
      <div className="whitespace-pre-line px-6 py-3 text-[13px] text-bambu-gray-light">
        {tr(
          'Spiral mode only works when wall loops is 1, support is disabled, clumping detection by probing is disabled, top shell layers is 0, sparse infill density is 0 and timelapse type is traditional.',
        )}
        {'\n\n'}
        {tr('Change these settings automatically?\nYes - Change these settings and enable spiral/vase mode automatically\nNo  - Cancel enabling spiral mode')}
      </div>
      <div className="flex justify-end gap-2 border-t border-bambu-dark-tertiary p-3">
        <Button variant="secondary" onClick={onNo}>
          {tr('No')}
        </Button>
        <Button onClick={onYes}>{tr('Yes')}</Button>
      </div>
    </Dialog>
  );
}
