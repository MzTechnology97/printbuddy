// Printfarm (fork): what to print, per component, in Seller Hub's order of urgency (phase 3).
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { AlertTriangle, ClipboardList, ImageOff, ListPlus, Loader2, RefreshCw, Settings2, X } from 'lucide-react';
import { api } from '../api/client';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { ScheduleOptionsPanel } from '../components/PrintModal/ScheduleOptions';
import { ConfirmModal } from '../components/ConfirmModal';
import { ModelViewerModal } from '../components/ModelViewerModal';
import { DEFAULT_SCHEDULE_OPTIONS, type ScheduleOptions } from '../components/PrintModal/types';
import {
  farmApi,
  type FarmPlanComponent,
  type FarmPlanPlate,
  type FarmPlanSettings,
  type FarmPrinterSpool,
  type FarmSdFiles,
} from './api';
import { BoardSummary, SlicerBadge } from './JobsBoard';
import { PlateThumb } from './PlatesPanel';
import { PrinterSlots } from './PrinterSlots';
import { kitUnits, suggestPrints } from './suggest';
import './i18n';

const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';
const STATUS_STYLE: Record<string, string> = {
  esaurito: 'bg-red-600/20 text-red-300 border-red-600/40',
  urgente: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
  da_stampare: 'bg-bambu-green/15 text-bambu-green border-bambu-green/40',
};

const duration = (s: number | null) => (s ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : null);
const fileName = (path: string) => path.split(/[\\/]/).pop() || path;

// What the plates of one component can be narrowed by, when they differ: their 3MF, its material, its slicer.
type PlateFilter = 'file' | 'material' | 'slicer';
type PlateFilters = Partial<Record<PlateFilter, string>>;
const PLATE_FILTERS: PlateFilter[] = ['file', 'material', 'slicer'];
const SLICER_NAMES: Record<string, string> = { orcaslicer: 'OrcaSlicer', bambu_studio: 'Bambu Studio' };
const filterValue = (p: FarmPlanPlate, k: PlateFilter) => (k === 'file' ? p.file : k === 'material' ? p.material : p.slicer || '');
const filterLabel = (k: PlateFilter, v: string) => (k === 'file' ? fileName(v) : k === 'slicer' ? SLICER_NAMES[v] ?? v : v);
const matches = (p: FarmPlanPlate, f: PlateFilters, except?: PlateFilter) =>
  PLATE_FILTERS.every((k) => k === except || !f[k] || filterValue(p, k) === f[k]);

/** One select per way the plates differ (each lists only what the other filters leave: never an empty list). */
function PlateFilterBar({ plates, value, onChange }: {
  plates: FarmPlanPlate[];
  value: PlateFilters;
  onChange: (f: PlateFilters) => void;
}) {
  const { t } = useTranslation();
  const bars = PLATE_FILTERS.map((k) => ({
    k,
    options: [...new Set(plates.filter((p) => matches(p, value, k)).map((p) => filterValue(p, k)).filter(Boolean))],
  })).filter(({ k, options }) => options.length > 1 || value[k]);
  if (!bars.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-white">
      <span className="text-xs text-bambu-gray">{t('farm.jobs.filterPlates')}</span>
      {bars.map(({ k, options }) => (
        <select key={k} aria-label={t(`farm.jobs.filter.${k}`)} className={`${INPUT} py-1 max-w-full`}
          value={value[k] || ''} onChange={(e) => onChange({ ...value, [k]: e.target.value || undefined })}>
          <option value="">{t(`farm.jobs.filterAll.${k}`)}</option>
          {options.map((v) => <option key={v} value={v}>{filterLabel(k, v)}</option>)}
        </select>
      ))}
    </div>
  );
}

export function PrinterChip({ p }: { p: FarmPlanPlate['printers'][number] }) {
  const { t } = useTranslation();
  const ready = p.idle && p.material_ok && p.color_ok;
  const dot = ready ? 'bg-bambu-green' : p.idle ? 'bg-amber-400' : 'bg-bambu-gray';
  const notes = [
    p.idle ? t('farm.plan.free') : t('farm.plan.busy'),
    p.material_ok === false ? t('farm.plan.noMaterial') : null,
    p.color_ok === false ? t('farm.plan.noColor') : null,
    p.color_ok === null ? t('farm.plan.colorUnknown') : null,
    p.by_hand ? t('farm.spools.byHand') : null,
  ].filter(Boolean);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs bg-bambu-dark rounded px-2 py-1" title={notes.join(' · ')}>
      <span className={`w-2 h-2 rounded-full ${dot}`} />
      {p.name}
      <span className="text-bambu-gray">· {notes.join(' · ')}</span>
    </span>
  );
}

/** The plate's picture (its slice's, else the 3MF's); a click shows it large, a missing one a placeholder. */
/** The plate's picture; a click opens it in 3D: its G-code when sliced, else the 3MF on that plate. */
function PlatePreview({ plate }: { plate: FarmPlanPlate }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const plateName = t('farm.plates.plate', { index: plate.plate });
  const label = `${t(plate.sliced_file_id ? 'farm.plan.viewGcode' : 'farm.plan.view3d')} · ${plateName}`;
  const src = farmApi.plateImageUrl(plate.plate_id);
  // Keyed by URL: another plate in the same row gets its own try.
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  return (
    <>
      <button type="button" aria-label={label} title={label} onClick={() => setOpen(true)}
        className="w-20 h-20 shrink-0 rounded bg-bambu-dark-secondary hover:ring-2 hover:ring-bambu-green flex items-center justify-center">
        {failedSrc === src ? (
          <ImageOff className="w-6 h-6 text-bambu-gray" aria-label={t('farm.plan.noPreview')} />
        ) : (
          <img src={src} alt="" loading="lazy" className="w-full h-full object-contain rounded" onError={() => setFailedSrc(src)} />
        )}
      </button>
      {open && (
        <ModelViewerModal
          libraryFileId={plate.sliced_file_id ?? plate.library_file_id}
          fileType="3mf"
          title={`${fileName(plate.file)} · ${plateName}`}
          initialTab={plate.sliced_file_id ? 'gcode' : '3d'}
          initialPlate={plate.sliced_file_id ? undefined : plate.plate}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

/** Slicer weight when known, else the solid-piece upper bound. */
function Grams({ plate }: { plate: FarmPlanPlate }) {
  const { t } = useTranslation();
  const text = gramsText(plate, t);
  if (!text) return null;
  return (
    <span className="text-xs text-white" title={plate.filament_grams ? undefined : t('farm.plan.gramsMaxHint')}>
      {' · '}{text}
    </span>
  );
}

const gramsText = (plate: FarmPlanPlate, t: TFunction) =>
  plate.filament_grams
    ? `${Math.round(plate.filament_grams)} g`
    : plate.filament_grams_max
      ? t('farm.plan.gramsMax', { grams: Math.round(plate.filament_grams_max) })
      : null;

/** A plate with its picture (3D on click) and its printers. With ``onPick`` the row's text picks it. */
function PlateRow({ plate, proposed, picked = false, onPick }: {
  plate: FarmPlanPlate;
  proposed: boolean;
  picked?: boolean;
  onPick?: () => void;
}) {
  const { t } = useTranslation();
  const facts = [
    plate.printer_model,
    plate.material,
    t('farm.plan.yield', { count: plate.yield }),
    duration(plate.print_time_seconds),
  ].filter(Boolean);
  return (
    <li className={`rounded-lg p-2 flex gap-3 ${
      picked ? 'bg-bambu-green/20 border-2 border-bambu-green'
        : proposed ? 'bg-bambu-green/10 border border-bambu-green/40' : 'bg-bambu-dark border border-transparent'
    }`}>
      <PlatePreview plate={plate} />
      <div
        className={`min-w-0 flex-1 space-y-1 ${onPick ? 'cursor-pointer rounded hover:bg-white/5' : ''}`}
        {...(onPick ? {
          role: 'button',
          tabIndex: 0,
          'aria-pressed': picked,
          'aria-label': `${t(picked ? 'farm.jobs.platePicked' : 'farm.jobs.pickPlate')}: ${fileName(plate.file)} · ${t('farm.plates.plate', { index: plate.plate })}`,
          onClick: onPick,
          onKeyDown: (e: React.KeyboardEvent) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onPick();
            }
          },
        } : {})}
      >
        <p className="text-sm text-white break-all">
          {picked && <span className="text-xs text-bambu-green font-semibold mr-2">✓ {t('farm.jobs.chosen')}</span>}
          {!picked && proposed && <span className="text-xs text-bambu-green mr-2">{t('farm.plan.proposed')}</span>}
          {fileName(plate.file)} · {t('farm.plates.plate', { index: plate.plate })}
          {plate.slicer && <span className="ml-2 align-middle"><SlicerBadge slicer={plate.slicer} /></span>}
          <span className="text-xs text-bambu-gray"> · {facts.join(' · ')}</span>
          <Grams plate={plate} />
          {!plate.sliced && <span className="text-xs text-amber-400"> · {t('farm.plan.notSliced')}</span>}
        </p>
        <div className="flex flex-wrap gap-1">
          {plate.printers.length ? (
            plate.printers.map((p) => <PrinterChip key={p.id} p={p} />)
          ) : (
            <span className="text-xs text-bambu-gray">{t('farm.plan.noPrinter', { model: plate.printer_model })}</span>
          )}
        </div>
      </div>
    </li>
  );
}

/** A chosen printer waiting for its plate to be cleared: say so, and let it be marked clear from here. */
function PlateClear({ printerId, name }: { printerId: number; name: string }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const status = useQuery({
    queryKey: ['printerStatus', printerId],
    queryFn: () => api.getPrinterStatus(printerId),
    refetchInterval: 10_000,
  });
  const clear = useMutation({
    mutationFn: () => api.clearPlate(printerId),
    onSuccess: () => {
      setConfirm(false);
      showToast(t('queue.clearPlateSuccess'), 'success');
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
      queryClient.invalidateQueries({ queryKey: ['farm'] });
    },
    onError: (e: Error) => showToast(e.message || t('queue.clearPlateFailed'), 'error'),
  });
  if (!status.data?.awaiting_plate_clear) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-amber-400">
      <span>{t('farm.jobs.plateToClear', { printer: name })}</span>
      {hasPermission('printers:clear_plate') && (
        <Button size="sm" variant="secondary" onClick={() => setConfirm(true)}>{t('farm.jobs.plateCleared')}</Button>
      )}
      {confirm && (
        <ConfirmModal title={t('farm.jobs.plateClearTitle')} message={t('farm.jobs.plateClearMessage', { printer: name })}
          confirmText={t('farm.jobs.plateCleared')} isLoading={clear.isPending}
          onConfirm={() => clear.mutate()} onCancel={() => setConfirm(false)} />
      )}
    </div>
  );
}

/** Requested components no active printer can print: no ready plate, or plates only for models nobody has. */
export function NotPrintable({ components }: { components: FarmPlanComponent[] }) {
  const { t } = useTranslation();
  const missing = components.filter((c) => (c.to_print ?? 0) > 0 && !c.plates.some((p) => p.printers.length > 0));
  if (!missing.length) return null;
  return (
    <div role="alert" className="text-sm text-amber-400 bg-amber-500/10 border border-amber-500/40 rounded-lg p-3 space-y-1">
      <p className="flex items-center gap-2 font-semibold">
        <AlertTriangle className="w-4 h-4 shrink-0" /> {t('farm.plan.notPrintable', { count: missing.length })}
      </p>
      <p className="break-words">{missing.map((c) => `${c.code} ${c.name}`).join(' · ')}</p>
      <Link to="/farm/products?tab=plates" className="text-bambu-green hover:underline">{t('farm.plan.toPlates')}</Link>
    </div>
  );
}

/** The cover of a product using the component (its variant, so in its colour) and the proposed plate's preview. */
function ComponentPictures({ c }: { c: FarmPlanComponent }) {
  const { t } = useTranslation();
  const product = c.products.find((p) => p.has_cover);
  const plate = c.plates[0];
  if (!product && !plate) return null;
  return (
    <div className="flex gap-2 shrink-0">
      {product && (
        <Link to={`/projects/${product.project_id}`} title={c.products.map((p) => p.name).join(', ')}
          className="w-24 h-24 rounded bg-bambu-dark overflow-hidden hover:ring-2 hover:ring-bambu-green">
          <img alt={`${t('farm.plan.productPhoto')} ${product.name}`} loading="lazy" decoding="async"
            className="w-full h-full object-cover" src={api.getProjectCoverImageUrl(product.project_id)} />
        </Link>
      )}
      {plate && <PlateThumb fileId={plate.library_file_id} plateIndex={plate.plate} fileName={fileName(plate.file)}
        className="w-24 h-24" />}
    </div>
  );
}

function ComponentCard({ c, selected, onSelect, onAccoda }: {
  c: FarmPlanComponent;
  selected: boolean;
  onSelect: (on: boolean) => void;
  onAccoda: () => void;
}) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  const plates = all ? c.plates : c.plates.slice(0, 2);
  const canQueue = c.plates.length > 0 && c.missing > 0;
  return (
    <li className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-4 space-y-3">
      <div className="flex flex-wrap items-start gap-3">
        <input type="checkbox" aria-label={`${t('farm.jobs.select')} ${c.code}`} className="mt-1"
          disabled={!canQueue} checked={selected} onChange={(e) => onSelect(e.target.checked)} />
        <span className={`text-xs px-2 py-0.5 rounded border ${STATUS_STYLE[c.status] || 'border-bambu-dark-tertiary text-bambu-gray'}`}>
          {t(`farm.plan.status.${c.status}`, { defaultValue: c.status })}
        </span>
        {c.color_hex && (
          <span aria-hidden className="mt-0.5 w-4 h-4 rounded-full border border-bambu-gray/60"
            style={{ background: `#${c.color_hex}` }} />
        )}
        <ComponentPictures c={c} />
        <div className="min-w-0 flex-1">
          <p className="text-white">
            <span className="font-mono text-bambu-green mr-2">{c.code}</span>
            {c.name}
          </p>
          {c.products.length > 0 && (
            <p className="text-xs text-bambu-gray">{t('farm.plan.usedIn', { names: c.products.map((p) => p.name).join(', ') })}</p>
          )}
          <p className="text-xs text-bambu-gray">
            {t('farm.plan.missing', { count: c.missing })}
            {c.in_queue > 0 && ` · ${t('farm.plan.inQueue', { count: c.in_queue })}`}
            {` · ${t('farm.plan.toPrint', { count: c.to_print ?? 0 })}`}
            {c.days_left != null && ` · ${t('farm.plan.daysLeft', { count: Math.round(c.days_left) })}`}
          </p>
        </div>
        <Button size="sm" disabled={!canQueue} onClick={onAccoda}
          title={c.missing <= 0 ? t('farm.jobs.covered') : undefined}>
          <ListPlus className="w-4 h-4" /> {t('farm.jobs.accoda')}
        </Button>
      </div>
      {!c.known ? (
        <p className="text-sm text-amber-400">{t('farm.plan.unknown')}</p>
      ) : c.plates.length === 0 ? (
        <p className="text-sm text-amber-400 flex items-center gap-1">
          <AlertTriangle className="w-4 h-4" />
          {t('farm.plan.noPlates', { shape: c.shape || c.name })}{' '}
          <Link to="/farm/products?tab=plates" className="text-bambu-green hover:underline">{t('farm.plan.toPlates')}</Link>
        </p>
      ) : (
        <ul className="space-y-2">
          {plates.map((p, i) => (
            <PlateRow key={p.plate_id} plate={p} proposed={i === 0 && p.score > 0} />
          ))}
          {c.plates.length > 2 && (
            <button type="button" className="text-xs text-bambu-green hover:underline" onClick={() => setAll(!all)}>
              {all ? t('farm.plan.fewer') : t('farm.plan.morePlates', { count: c.plates.length - 2 })}
            </button>
          )}
        </ul>
      )}
    </li>
  );
}

function SettingsCard({ settings, onClose }: { settings: FarmPlanSettings; onClose: () => void }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [url, setUrl] = useState(settings.url);
  const [tolerance, setTolerance] = useState(settings.tolerance);
  const [colors, setColors] = useState(Object.entries(settings.colors));
  const save = useMutation({
    mutationFn: () => farmApi.savePlanSettings({ url, tolerance, colors: Object.fromEntries(colors.filter(([n]) => n.trim())) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['farm', 'plan'] });
      queryClient.invalidateQueries({ queryKey: ['farm', 'plan-settings'] });
      showToast(t('farm.products.saved'), 'success');
      onClose();
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  return (
    <form className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-4 space-y-3 max-w-2xl"
      onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <label className="block text-sm text-white">
        {t('farm.plan.url')}
        <input className={`${INPUT} w-full mt-1`} value={url} placeholder="http://seller-hub:8000"
          onChange={(e) => setUrl(e.target.value)} />
      </label>
      <label className="block text-sm text-white">
        {t('farm.plan.tolerance', { value: tolerance })}
        <input type="range" min={10} max={200} className="w-full" value={tolerance}
          onChange={(e) => setTolerance(Number(e.target.value))} />
      </label>
      <div className="space-y-1">
        <p className="text-sm text-white">{t('farm.plan.colors')}</p>
        {colors.map(([name, hex], i) => (
          <div key={i} className="flex items-center gap-2">
            <input aria-label={t('farm.products.color')} className={`${INPUT} py-1 flex-1`} value={name}
              onChange={(e) => setColors(colors.map((row, j) => (j === i ? [e.target.value, row[1]] : row)))} />
            <input type="color" aria-label={`${t('farm.plan.colorCode')} ${name}`} value={`#${hex}`}
              onChange={(e) => setColors(colors.map((row, j) => (j === i ? [row[0], e.target.value.slice(1)] : row)))} />
            <span className="font-mono text-xs text-bambu-gray w-16">{hex}</span>
          </div>
        ))}
        <Button type="button" size="sm" variant="secondary" onClick={() => setColors([...colors, ['', '808080']])}>
          {t('farm.plan.addColor')}
        </Button>
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>{t('farm.products.cancel')}</Button>
        <Button type="submit" disabled={save.isPending}>{t('farm.products.save')}</Button>
      </div>
    </form>
  );
}

/** Bambu printers that keep their files on the SD after a print, so they can be reprinted from the display. */
function SdFilesCard({ data }: { data: FarmSdFiles }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [days, setDays] = useState(data.keep_days);
  const [kept, setKept] = useState(() => new Set(data.printers.filter((p) => p.keep).map((p) => p.id)));
  const save = useMutation({
    mutationFn: () => farmApi.saveSdFiles({ keep_days: days, keep_printer_ids: [...kept] }),
    onSuccess: (saved) => {
      queryClient.setQueryData(['farm', 'sd-files'], saved);
      showToast(t('farm.products.saved'), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  if (!data.printers.length) return null;
  const toggle = (id: number, on: boolean) => {
    const next = new Set(kept);
    if (on) next.add(id);
    else next.delete(id);
    setKept(next);
  };
  return (
    <form className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-4 space-y-3 max-w-2xl"
      onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <h2 className="text-white font-semibold">{t('farm.sdFiles.title')}</h2>
      <p className="text-xs text-bambu-gray">{t('farm.sdFiles.intro')}</p>
      <p className="text-sm text-white">{t('farm.sdFiles.keep')}</p>
      <ul className="space-y-2">
        {data.printers.map((p) => (
          <li key={p.id} className="text-sm text-white">
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-1" checked={kept.has(p.id)}
                aria-label={`${t('farm.sdFiles.keep')}: ${p.name}`}
                onChange={(e) => toggle(p.id, e.target.checked)} />
              <span>
                {p.name} <span className="text-xs text-bambu-gray">{p.model}</span>
                {p.ghost_print_risk && kept.has(p.id) && (
                  <span role="alert" className="block text-xs text-amber-400">{t('farm.sdFiles.ghostRisk')}</span>
                )}
              </span>
            </label>
          </li>
        ))}
      </ul>
      <label className="flex items-center gap-2 text-sm text-white">
        {t('farm.sdFiles.days')}
        <input type="number" min={1} max={365} className={`${INPUT} py-1 w-20`} value={days}
          onChange={(e) => setDays(Number(e.target.value))} />
      </label>
      <div className="flex justify-end">
        <Button type="submit" disabled={save.isPending || !(days >= 1 && days <= 365)}>{t('farm.products.save')}</Button>
      </div>
    </form>
  );
}

/** The component's picture from its 3MF pieces; its code when there is none. */
function ComponentImage({ c }: { c: FarmPlanComponent }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="w-16 h-16 shrink-0 rounded bg-gray-300 flex items-center justify-center overflow-hidden">
      {failed ? (
        <span className="text-xs font-mono text-bambu-dark">{c.code}</span>
      ) : (
        <img src={farmApi.componentImageUrl(c.code, c.color_hex)} alt={c.name} loading="lazy"
          className="w-full h-full object-contain" onError={() => setFailed(true)} />
      )}
    </div>
  );
}

/** Seller Hub's stock and what it says to print; "already N" when nothing is to print. */
function StockNote({ c, limiting }: { c: FarmPlanComponent; limiting: boolean }) {
  const { t } = useTranslation();
  if (c.stock == null) return <span className="text-xs text-bambu-gray">{t('farm.jobs.stockUnknown')}</span>;
  return (
    <span className="text-xs text-bambu-gray flex flex-wrap gap-x-2">
      <span>{t('farm.jobs.stock', { count: c.stock })}</span>
      {c.to_print ? (
        <span className="text-amber-300">{t('farm.jobs.toPrint', { count: c.to_print })}</span>
      ) : (
        <span className="text-bambu-green">{t('farm.jobs.enough', { count: c.stock })}</span>
      )}
      {limiting && <span className="text-red-300">{t('farm.jobs.limiting', { count: kitUnits(c) ?? 0 })}</span>}
    </span>
  );
}

/** ``byHand``: printing a product whatever Seller Hub asks, so components already covered are kept.
 *
 * Like upstream's print dialog: which components, the printer with what it has loaded, its AMS/CFS
 * slots and when to start. The farm still picks the 3MF by material and slices what is missing. */
export function AccodaSummary({ components, onClose, onDone, byHand = false, notes = [] }: {
  components: FarmPlanComponent[];
  onClose: () => void;
  onDone: () => void;
  byHand?: boolean;
  notes?: string[]; // why some asked-for components are not here (a link's)
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.getSettings });
  const usable = components.filter((c) => c.plates.length > 0 && (byHand || c.missing > 0));
  // Seller Hub's stock orders and ticks them (what it says to print); all can change.
  const [suggestion] = useState(() => suggestPrints(usable, byHand));
  const [picked, setPicked] = useState(() => new Set(suggestion.picked));
  const [chosen, setChosen] = useState<Record<string, number>>({}); // component -> printer; none: first free
  const [chosenPlate, setChosenPlate] = useState<Record<string, number>>({}); // none: the loaded material decides
  const [slots, setSlots] = useState<Record<string, Record<number, number>>>({}); // slots picked by hand
  const [amsMappings, setAmsMappings] = useState<Record<string, number[] | undefined>>({});
  const [schedule, setSchedule] = useState<ScheduleOptions>(DEFAULT_SCHEDULE_OPTIONS);
  const [forceSlice, setForceSlice] = useState(false);
  const [ignoreSpool, setIgnoreSpool] = useState<Record<string, boolean>>({}); // component -> sent without the spool check
  const [confirmIgnore, setConfirmIgnore] = useState<{ code: string; printer: string } | null>(null);
  const [filters, setFilters] = useState<Record<string, PlateFilters>>({}); // component -> what its plates are narrowed to
  const byPrinter = (c: FarmPlanComponent) =>
    chosen[c.code] ? c.plates.filter((p) => p.printers.some((pr) => pr.id === chosen[c.code])) : c.plates;
  // The plates the job may use: of the chosen printer, then the filters (automatic picks among these too).
  const platesFor = (c: FarmPlanComponent) => byPrinter(c).filter((p) => matches(p, filters[c.code] || {}));
  // Automatic on a chosen printer: only the plates whose material and colour it has loaded now (unless sent
  // anyway). None: nothing is queued until a plate is picked, the spool loaded or "send anyway" ticked.
  const loadedOn = (p: FarmPlanPlate, printerId: number) => {
    const fit = p.printers.find((pr) => pr.id === printerId);
    return !!fit && fit.material_ok !== false && fit.color_ok !== false;
  };
  const autoPlates = (c: FarmPlanComponent) =>
    chosen[c.code] && !ignoreSpool[c.code] ? platesFor(c).filter((p) => loadedOn(p, chosen[c.code])) : platesFor(c);
  const blocked = (c: FarmPlanComponent) => !chosenPlate[c.code] && autoPlates(c).length === 0;
  const [showAll, setShowAll] = useState<Record<string, boolean>>({}); // component -> the plates that don't fit too
  // The plate the slots are chosen for: the one picked, else the proposed one (then pinned to it).
  const slotPlate = (c: FarmPlanComponent) =>
    platesFor(c).find((p) => p.plate_id === chosenPlate[c.code]) ?? autoPlates(c)[0];
  const selected = usable.filter((c) => picked.has(c.code));
  const toggle = (code: string) => {
    const next = new Set(picked);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    setPicked(next);
  };
  const resetSlots = (code: string) => {
    setSlots((prev) => ({ ...prev, [code]: {} }));
    setAmsMappings((prev) => ({ ...prev, [code]: undefined }));
  };
  const scheduledTime = schedule.scheduleType === 'scheduled' && schedule.scheduledTime
    ? new Date(schedule.scheduledTime).toISOString()
    : undefined;
  const create = useMutation({
    mutationFn: () =>
      farmApi.createJobs(
        selected.map((c) => {
          const ams = chosen[c.code] ? amsMappings[c.code] : undefined;
          const plateIds = chosenPlate[c.code]
            ? [chosenPlate[c.code]]
            : ams ? [slotPlate(c).plate_id] : autoPlates(c).map((p) => p.plate_id);
          return {
            component_code: c.code,
            plate_ids: plateIds,
            printer_id: chosen[c.code] || undefined,
            ams_mapping: ams,
            scheduled_time: scheduledTime,
            // Only what is set: the backend defaults the rest as upstream's queue does.
            manual_start: schedule.scheduleType === 'manual' || undefined,
            require_previous_success: schedule.requirePreviousSuccess || undefined,
            auto_off_after: schedule.autoOffAfter || undefined,
            gcode_injection: schedule.gcodeInjection || undefined,
            force_slice: forceSlice || undefined,
            ignore_spool: (chosen[c.code] && ignoreSpool[c.code]) || undefined,
          };
        }),
      ),
    onSuccess: (jobs) => {
      queryClient.invalidateQueries({ queryKey: ['farm'] });
      showToast(t('farm.jobs.created', { count: jobs.length }), 'success');
      onDone();
      onClose();
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div role="dialog" aria-label={t('farm.jobs.summaryTitle')}
        className="bg-bambu-dark-secondary rounded-lg w-full max-w-3xl max-h-[90vh] flex flex-col border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">{t('farm.jobs.summaryTitle')}</h2>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded" aria-label={t('farm.products.cancel')}>
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>
        <div className="p-4 space-y-4 overflow-y-auto">
          <p className="text-sm text-bambu-gray">{t('farm.jobs.summaryIntro')}</p>
          {notes.length > 0 && <LinkNotes notes={notes} />}
          {usable.length > 1 && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-bambu-gray">
              {t('farm.jobs.pickHint')}
              <Button size="sm" variant="ghost" onClick={() => setPicked(new Set(usable.map((c) => c.code)))}>
                {t('farm.jobs.pickAll')}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setPicked(new Set())}>{t('farm.jobs.pickNone')}</Button>
            </div>
          )}
          {suggestion.order.map((c) => {
            const on = picked.has(c.code);
            const best = c.plates[0];
            const printers = [...new Map(c.plates.flatMap((p) => p.printers).map((pr) => [pr.id, pr])).values()];
            const plate = slotPlate(c);
            return (
              <section key={c.code} className={`space-y-2 rounded-lg p-2 ${on ? 'bg-bambu-dark/40' : 'opacity-70'}`}>
                <label className="flex items-center gap-3 text-white cursor-pointer">
                  <input type="checkbox" className="w-4 h-4 accent-bambu-green" checked={on} onChange={() => toggle(c.code)}
                    aria-label={t('farm.jobs.pick', { code: c.code })} />
                  <ComponentImage c={c} />
                  <span className="flex items-center gap-2 flex-wrap">
                    {c.color_hex && (
                      <span aria-hidden className="w-4 h-4 rounded-full border border-bambu-gray/60"
                        style={{ background: `#${c.color_hex}` }} />
                    )}
                    <span className="font-mono text-bambu-green">{c.code}</span> {c.name}
                    <StockNote c={c} limiting={suggestion.limiting === c.code} />
                  </span>
                </label>
                {on && (
                  <>
                    <p className="text-xs text-bambu-gray">
                      {byHand
                        ? t('farm.jobs.onePrintByHand', { pieces: best.yield })
                        : t('farm.jobs.onePrint', { pieces: best.yield, missing: c.missing })}
                      {best.print_time_seconds ? ` · ${duration(best.print_time_seconds)}` : ''}
                      {best.filament_grams ? ` · ${Math.round(best.filament_grams)} g` : ''}
                    </p>
                    <label className="flex flex-wrap items-center gap-2 text-sm text-white">
                      {t('farm.jobs.printer')}
                      <select className={`${INPUT} py-1`} value={chosen[c.code] ?? ''}
                        onChange={(e) => {
                          setChosen({ ...chosen, [c.code]: Number(e.target.value) || 0 });
                          setChosenPlate({ ...chosenPlate, [c.code]: 0 });
                          setFilters({ ...filters, [c.code]: {} });
                          setIgnoreSpool({ ...ignoreSpool, [c.code]: false }); // asked again for another printer
                          resetSlots(c.code);
                        }}>
                        <option value="">{t('farm.jobs.firstFree')}</option>
                        {printers.map((pr) => (
                          <option key={pr.id} value={pr.id}>
                            {pr.name} · {pr.idle ? t('farm.plan.free') : t('farm.plan.busy')}
                          </option>
                        ))}
                      </select>
                    </label>
                    {chosen[c.code] ? (
                      <>
                        <p className="text-xs text-bambu-gray">{t('farm.jobs.chosenHint')}</p>
                        <label className="flex items-start gap-2 text-sm text-white">
                          <input type="checkbox" className="mt-1" checked={!!ignoreSpool[c.code]}
                            onChange={(e) => {
                              if (!e.target.checked) setIgnoreSpool({ ...ignoreSpool, [c.code]: false });
                              else setConfirmIgnore({
                                code: c.code,
                                printer: printers.find((pr) => pr.id === chosen[c.code])?.name ?? '',
                              });
                            }} />
                          <span>
                            {t('farm.jobs.ignoreSpool')}
                            <span className="block text-xs text-bambu-gray">{t('farm.jobs.ignoreSpoolHint')}</span>
                          </span>
                        </label>
                        <PlateClear printerId={chosen[c.code]}
                          name={printers.find((pr) => pr.id === chosen[c.code])?.name ?? ''} />
                      </>
                    ) : null}
                    <PlateFilterBar plates={byPrinter(c)} value={filters[c.code] || {}}
                      onChange={(f) => {
                        setFilters({ ...filters, [c.code]: f });
                        if (chosenPlate[c.code] && !byPrinter(c).some((p) => p.plate_id === chosenPlate[c.code] && matches(p, f))) {
                          setChosenPlate({ ...chosenPlate, [c.code]: 0 });
                          resetSlots(c.code);
                        }
                      }} />
                    <label className="flex flex-wrap items-center gap-2 text-sm text-white">
                      {t('farm.jobs.plate')}
                      <select className={`${INPUT} py-1 max-w-full`} value={chosenPlate[c.code] || ''}
                        onChange={(e) => {
                          setChosenPlate({ ...chosenPlate, [c.code]: Number(e.target.value) || 0 });
                          resetSlots(c.code);
                        }}>
                        <option value="">{t('farm.jobs.anyPlate')}</option>
                        {platesFor(c).map((p) => {
                          const grams = gramsText(p, t);
                          return (
                            <option key={p.plate_id} value={p.plate_id}>
                              {fileName(p.file)} · {t('farm.plates.plate', { index: p.plate })} · {p.printer_model} · {p.material}
                              {' · '}{t('farm.plan.yield', { count: p.yield })}
                              {grams ? ` · ${grams}` : ''}
                            </option>
                          );
                        })}
                      </select>
                    </label>
                    {chosen[c.code] && plate ? (
                      <PrinterSlots key={`${chosen[c.code]}-${plate.plate_id}`} printerId={chosen[c.code]}
                        plateId={plate.plate_id} color={c.color} colorHex={c.color_hex}
                        plateLabel={`${fileName(plate.file)} · ${t('farm.plates.plate', { index: plate.plate })}`}
                        mappings={slots[c.code] || {}}
                        onMappings={(m) => setSlots((prev) => ({ ...prev, [c.code]: m }))}
                        onAmsMapping={(m) => setAmsMappings((prev) => ({ ...prev, [c.code]: m }))} />
                    ) : null}
                    {!chosenPlate[c.code] && autoPlates(c).length < platesFor(c).length && (
                      <div className="flex flex-wrap items-center gap-2 text-xs">
                        {blocked(c) && (
                          <span role="alert" className="text-amber-400">
                            {t('farm.jobs.noLoadedMatch', {
                              printer: printers.find((pr) => pr.id === chosen[c.code])?.name ?? '',
                            })}
                          </span>
                        )}
                        <button type="button" className="text-bambu-green hover:underline"
                          onClick={() => setShowAll({ ...showAll, [c.code]: !showAll[c.code] })}>
                          {t(showAll[c.code] ? 'farm.jobs.hideOtherPlates' : 'farm.jobs.showOtherPlates', {
                            count: platesFor(c).length - autoPlates(c).length,
                          })}
                        </button>
                      </div>
                    )}
                    <ul className="space-y-2">
                      {/* a click picks a plate, a click on the picked one goes back to automatic; automatic on a
                          chosen printer lists what fits its loaded spools, the rest on demand */}
                      {(chosenPlate[c.code] || showAll[c.code] ? platesFor(c) : autoPlates(c)).map((p, i) => (
                        <PlateRow key={p.plate_id} plate={p}
                          proposed={!chosenPlate[c.code] && i === 0 && p.score > 0 && autoPlates(c).includes(p)}
                          picked={chosenPlate[c.code] === p.plate_id}
                          onPick={() => {
                            setChosenPlate({ ...chosenPlate, [c.code]: chosenPlate[c.code] === p.plate_id ? 0 : p.plate_id });
                            resetSlots(c.code);
                          }} />
                      ))}
                    </ul>
                  </>
                )}
              </section>
            );
          })}
          {usable.length < components.length && (
            <p className="text-xs text-amber-400">{t(byHand ? 'farm.jobs.skippedByHand' : 'farm.jobs.skipped')}</p>
          )}
          {usable.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold text-white">{t('farm.jobs.when')}</h3>
              <ScheduleOptionsPanel options={schedule} onChange={setSchedule}
                dateFormat={settings.data?.date_format || 'system'} timeFormat={settings.data?.time_format || 'system'}
                canControlPrinter={hasPermission('printers:control')} showStagger={false}
                hasGcodeSnippets={!!settings.data?.gcode_snippets} />
              <label className="flex items-start gap-2 text-sm text-white">
                <input type="checkbox" className="mt-1" checked={forceSlice} onChange={(e) => setForceSlice(e.target.checked)} />
                <span>
                  {t('farm.jobs.forceSlice')}
                  <span className="block text-xs text-bambu-gray">{t('farm.jobs.forceSliceHint')}</span>
                </span>
              </label>
            </section>
          )}
        </div>
        {confirmIgnore && (
          <ConfirmModal title={t('farm.jobs.ignoreSpoolTitle')}
            message={t('farm.jobs.ignoreSpoolMessage', { printer: confirmIgnore.printer })}
            confirmText={t('farm.jobs.ignoreSpoolConfirm')} variant="warning"
            onConfirm={() => {
              setIgnoreSpool({ ...ignoreSpool, [confirmIgnore.code]: true });
              setConfirmIgnore(null);
            }}
            onCancel={() => setConfirmIgnore(null)} />
        )}
        <div className="p-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>{t('farm.products.cancel')}</Button>
          <Button disabled={!selected.length || create.isPending || selected.some(blocked)
            || (schedule.scheduleType === 'scheduled' && !scheduledTime)}
            onClick={() => create.mutate()}>
            {create.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : t('farm.jobs.confirm', { count: selected.length })}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Why components a link asked for can't be queued. */
function LinkNotes({ notes, onClose }: { notes: string[]; onClose?: () => void }) {
  const { t } = useTranslation();
  return (
    <div role="alert" className="text-sm text-amber-400 bg-amber-500/10 border border-amber-500/40 rounded-lg p-3 flex items-start gap-2">
      <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
      <ul className="flex-1 space-y-1">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
      {onClose && (
        <button type="button" onClick={onClose} className="p-0.5 rounded hover:bg-bambu-dark"
          aria-label={t('farm.plan.link.dismiss')}>
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}

/** Seller Hub's link ``?accoda=C-0099,C-0100``: the components to queue, and why the others can't be. */
function linkedComponents(param: string, rows: FarmPlanComponent[], t: TFunction) {
  const codes = [...new Set(param.split(',').map((c) => c.trim().toUpperCase()).filter(Boolean))];
  const usable: FarmPlanComponent[] = [];
  const notes: string[] = codes.length ? [] : [t('farm.plan.link.empty')];
  for (const code of codes) {
    const c = rows.find((r) => r.code.toUpperCase() === code);
    if (!c) notes.push(t('farm.plan.link.notInPlan', { code }));
    else if (!c.plates.length) notes.push(t('farm.plan.link.noPlates', { code }));
    else if (c.missing <= 0) notes.push(t('farm.plan.link.covered', { code }));
    else usable.push(c); // what the card's Queue button allows
  }
  return { usable, notes };
}

const MATERIALS = ['PLA', 'PETG', 'ASA', 'ABS', 'TPU', 'PA', 'PC'];

function SpoolRow({ s, colors }: { s: FarmPrinterSpool; colors: string[] }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [material, setMaterial] = useState(s.material || '');
  const [color, setColor] = useState(s.color_name || '');
  const done = () => queryClient.invalidateQueries({ queryKey: ['farm'] });
  const save = useMutation({
    mutationFn: () => farmApi.setPrinterSpool(s.printer_id, { material, color_name: color }),
    onSuccess: () => { done(); showToast(t('farm.products.saved'), 'success'); },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const clear = useMutation({
    mutationFn: () => farmApi.clearPrinterSpool(s.printer_id),
    onSuccess: () => { setMaterial(''); setColor(''); done(); },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  return (
    <li className="p-3 flex flex-wrap items-center gap-2 text-sm">
      <span className="text-white w-32 truncate" title={s.model}>{s.name}</span>
      {s.color && <span aria-hidden className="w-4 h-4 rounded-full border border-bambu-gray/60" style={{ background: `#${s.color}` }} />}
      <input list={`farm-materials-${s.printer_id}`} aria-label={`${t('farm.spools.material')} ${s.name}`} className={`${INPUT} py-1 w-28`}
        value={material} placeholder={t('farm.spools.material')} onChange={(e) => setMaterial(e.target.value)} />
      <datalist id={`farm-materials-${s.printer_id}`}>
        {(s.materials?.length ? s.materials : MATERIALS).map((m) => <option key={m} value={m} />)}
      </datalist>
      <select aria-label={`${t('farm.spools.color')} ${s.name}`} className={`${INPUT} py-1`} value={color}
        onChange={(e) => setColor(e.target.value)}>
        <option value="">{t('farm.spools.color')}</option>
        {colors.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      <Button size="sm" aria-label={`${t('farm.products.save')} ${s.name}`}
        disabled={!material.trim() || !color || save.isPending} onClick={() => save.mutate()}>
        {t('farm.products.save')}
      </Button>
      {s.material && (
        <Button size="sm" variant="ghost" onClick={() => clear.mutate()}>{t('farm.spools.empty')}</Button>
      )}
    </li>
  );
}

/** Klipper printers without CFS can't say what is loaded: the user does, until they change it. */
function SpoolsCard() {
  const { t } = useTranslation();
  const spools = useQuery({ queryKey: ['farm', 'printer-spools'], queryFn: farmApi.getPrinterSpools });
  const settings = useQuery({ queryKey: ['farm', 'plan-settings'], queryFn: farmApi.getPlanSettings });
  if (!spools.data?.length) return null;
  const colors = Object.keys(settings.data?.colors || {}).map((c) => c.charAt(0).toUpperCase() + c.slice(1));
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-semibold text-white">{t('farm.spools.title')}</h2>
      <p className="text-xs text-bambu-gray max-w-3xl">{t('farm.spools.intro')}</p>
      <ul className="divide-y divide-bambu-dark-tertiary bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
        {spools.data.map((s) => <SpoolRow key={`${s.printer_id}-${s.updated_at}`} s={s} colors={colors} />)}
      </ul>
    </section>
  );
}

/** 3MFs that will not work, by file: told when they are saved, gone once fixed (``farm.library_problems``). */
function LibraryProblems() {
  const { t } = useTranslation();
  const problems = useQuery({ queryKey: ['farm', 'library-problems'], queryFn: farmApi.getLibraryProblems,
    refetchInterval: 60_000 });
  if (!problems.data?.length) return null;
  const files = new Map<number, typeof problems.data>();
  for (const p of problems.data) files.set(p.library_file_id, [...(files.get(p.library_file_id) || []), p]);
  return (
    <section role="alert" className="space-y-2 bg-amber-500/10 border border-amber-500/40 rounded-lg p-4">
      <h2 className="text-lg font-semibold text-amber-300 flex items-center gap-2">
        <AlertTriangle className="w-5 h-5" /> {t('farm.problems.title')}
      </h2>
      <p className="text-xs text-bambu-gray">{t('farm.problems.intro')}</p>
      <ul className="space-y-2">
        {[...files.values()].map((rows) => (
          <li key={rows[0].library_file_id} className="text-sm">
            <p className="text-white break-all">{rows[0].filename}</p>
            <ul className="text-xs text-amber-300 space-y-0.5 pl-3">
              {rows.map((p) => (
                <li key={p.id}>
                  {p.plate ? `${t('farm.problems.plate', { n: p.plate })}: ` : ''}{p.message}
                  <span className="text-bambu-gray"> · {t('farm.problems.since', { at: p.since.slice(0, 16).replace('T', ' ') })}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <Link to="/farm/products?tab=plates" className="text-sm text-bambu-green hover:underline">{t('farm.problems.open')}</Link>
    </section>
  );
}

export function PrintPlanPage() {
  const { t } = useTranslation();
  const [showSettings, setShowSettings] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [summary, setSummary] = useState<FarmPlanComponent[] | null>(null);
  const plan = useQuery({ queryKey: ['farm', 'plan'], queryFn: farmApi.getPlan, refetchInterval: 60_000 });
  const settings = useQuery({ queryKey: ['farm', 'plan-settings'], queryFn: farmApi.getPlanSettings });
  const sdFiles = useQuery({ queryKey: ['farm', 'sd-files'], queryFn: farmApi.getSdFiles, enabled: showSettings });
  const rows = plan.data?.components || [];
  const [params, setParams] = useSearchParams();
  const linked = params.get('accoda'); // the contract with Seller Hub: don't rename
  const [linkNotes, setLinkNotes] = useState<string[]>([]);
  // Opened from a link: once the plan is in, the same summary as the Queue button; then the link is
  // dropped from the URL so a refresh doesn't open it again.
  useEffect(() => {
    if (linked === null || !plan.data) return;
    const { usable, notes } = linkedComponents(linked, plan.data.components, t);
    setLinkNotes(notes);
    if (usable.length) setSummary(usable);
    setParams((p) => {
      p.delete('accoda');
      return p;
    }, { replace: true });
  }, [linked, plan.data, setParams, t]);

  return (
    <div className="p-4 md:p-8 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-white flex items-center gap-3">
          <ClipboardList className="w-7 h-7 text-bambu-green" /> {t('farm.plan.title')}
        </h1>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => plan.refetch()} disabled={plan.isFetching}>
            <RefreshCw className={`w-4 h-4 ${plan.isFetching ? 'animate-spin' : ''}`} /> {t('farm.plan.refresh')}
          </Button>
          <Button variant="secondary" onClick={() => setShowSettings(!showSettings)}>
            <Settings2 className="w-4 h-4" /> {t('farm.plan.settings')}
          </Button>
        </div>
      </div>
      <p className="text-bambu-gray text-sm max-w-3xl">{t('farm.plan.intro')}</p>
      {showSettings && settings.data && <SettingsCard settings={settings.data} onClose={() => setShowSettings(false)} />}
      {showSettings && sdFiles.data && <SdFilesCard data={sdFiles.data} />}
      <LibraryProblems />
      <BoardSummary />
      <SpoolsCard />
      {summary ? (
        <AccodaSummary components={summary} notes={linkNotes} onDone={() => setSelected(new Set())}
          onClose={() => { setSummary(null); setLinkNotes([]); }} />
      ) : linkNotes.length > 0 && <LinkNotes notes={linkNotes} onClose={() => setLinkNotes([])} />}
      {plan.data?.error && (
        <p className="text-sm text-amber-400 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          {plan.data.error}
          {plan.data.fetched_at && ` — ${t('farm.plan.cached', { at: plan.data.fetched_at })}`}
        </p>
      )}
      {plan.isLoading ? (
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      ) : plan.error ? (
        <>
          <p className="text-red-400">{(plan.error as Error).message}</p>
          {/* The link stays in the URL: it opens once the plan loads (a refresh tries again). */}
          {linked !== null && <LinkNotes notes={[t('farm.plan.link.waitPlan', { codes: linked })]} />}
        </>
      ) : rows.length === 0 ? (
        <p className="text-bambu-gray">{t('farm.plan.nothing')}</p>
      ) : (
        <>
          <NotPrintable components={rows} />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-bambu-gray">
              {t('farm.plan.summary', { count: rows.length, pieces: rows.reduce((n, r) => n + r.missing, 0) })}
              {plan.data?.fetched_at && ` · ${t('farm.plan.updated', { at: plan.data.fetched_at })}`}
            </p>
            <Button size="sm" disabled={selected.size === 0}
              onClick={() => setSummary(rows.filter((r) => selected.has(r.code)))}>
              <ListPlus className="w-4 h-4" /> {t('farm.jobs.accodaSelected', { count: selected.size })}
            </Button>
          </div>
          <ul className="space-y-3">
            {rows.map((c) => (
              <ComponentCard key={c.code} c={c} selected={selected.has(c.code)}
                onSelect={(on) => {
                  const next = new Set(selected);
                  if (on) next.add(c.code);
                  else next.delete(c.code);
                  setSelected(next);
                }}
                onAccoda={() => setSummary([c])} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
