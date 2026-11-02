// Printfarm (fork): what to print, per component, in Seller Hub's order of urgency (phase 3).
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ClipboardList, ListPlus, Loader2, RefreshCw, Settings2, X } from 'lucide-react';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmJob, type FarmPlanComponent, type FarmPlanPlate, type FarmPlanSettings } from './api';
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

function PrinterChip({ p }: { p: FarmPlanPlate['printers'][number] }) {
  const { t } = useTranslation();
  const ready = p.idle && p.material_ok && p.color_ok;
  const dot = ready ? 'bg-bambu-green' : p.idle ? 'bg-amber-400' : 'bg-bambu-gray';
  const notes = [
    p.idle ? t('farm.plan.free') : t('farm.plan.busy'),
    p.material_ok === false ? t('farm.plan.noMaterial') : null,
    p.color_ok === false ? t('farm.plan.noColor') : null,
    p.color_ok === null ? t('farm.plan.colorUnknown') : null,
  ].filter(Boolean);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs bg-bambu-dark rounded px-2 py-1" title={notes.join(' · ')}>
      <span className={`w-2 h-2 rounded-full ${dot}`} />
      {p.name}
      <span className="text-bambu-gray">· {notes.join(' · ')}</span>
    </span>
  );
}

function PlateRow({ plate, proposed }: { plate: FarmPlanPlate; proposed: boolean }) {
  const { t } = useTranslation();
  const facts = [
    plate.printer_model,
    plate.material,
    t('farm.plan.yield', { count: plate.yield }),
    duration(plate.print_time_seconds),
    plate.filament_grams ? `${Math.round(plate.filament_grams)} g` : null,
  ].filter(Boolean);
  return (
    <li className={`rounded-lg p-2 space-y-1 ${proposed ? 'bg-bambu-green/10 border border-bambu-green/40' : 'bg-bambu-dark'}`}>
      <p className="text-sm text-white break-all">
        {proposed && <span className="text-xs text-bambu-green mr-2">{t('farm.plan.proposed')}</span>}
        {fileName(plate.file)} · {t('farm.plates.plate', { index: plate.plate })}
        <span className="text-xs text-bambu-gray"> · {facts.join(' · ')}</span>
        {!plate.sliced && <span className="text-xs text-amber-400"> · {t('farm.plan.notSliced')}</span>}
      </p>
      <div className="flex flex-wrap gap-1">
        {plate.printers.length ? (
          plate.printers.map((p) => <PrinterChip key={p.id} p={p} />)
        ) : (
          <span className="text-xs text-bambu-gray">{t('farm.plan.noPrinter', { model: plate.printer_model })}</span>
        )}
      </div>
    </li>
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
        <div className="min-w-0 flex-1">
          <p className="text-white">
            <span className="font-mono text-bambu-green mr-2">{c.code}</span>
            {c.name}
          </p>
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

function AccodaSummary({ components, onClose, onDone }: {
  components: FarmPlanComponent[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const usable = components.filter((c) => c.plates.length > 0 && c.missing > 0);
  const create = useMutation({
    mutationFn: () =>
      farmApi.createJobs(usable.map((c) => ({ component_code: c.code, plate_ids: c.plates.map((p) => p.plate_id) }))),
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
          {usable.map((c) => {
            const best = c.plates[0];
            return (
              <section key={c.code} className="space-y-2">
                <p className="text-white flex items-center gap-2">
                  {c.color_hex && (
                    <span aria-hidden className="w-4 h-4 rounded-full border border-bambu-gray/60"
                      style={{ background: `#${c.color_hex}` }} />
                  )}
                  <span className="font-mono text-bambu-green">{c.code}</span> {c.name}
                </p>
                <p className="text-xs text-bambu-gray">
                  {t('farm.jobs.onePrint', { pieces: best.yield, missing: c.missing })}
                  {best.print_time_seconds ? ` · ${duration(best.print_time_seconds)}` : ''}
                  {best.filament_grams ? ` · ${Math.round(best.filament_grams)} g` : ''}
                </p>
                <ul className="space-y-2">
                  {c.plates.map((p, i) => (
                    <PlateRow key={p.plate_id} plate={p} proposed={i === 0 && p.score > 0} />
                  ))}
                </ul>
              </section>
            );
          })}
          {usable.length < components.length && <p className="text-xs text-amber-400">{t('farm.jobs.skipped')}</p>}
        </div>
        <div className="p-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>{t('farm.products.cancel')}</Button>
          <Button disabled={!usable.length || create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : t('farm.jobs.confirm', { count: usable.length })}
          </Button>
        </div>
      </div>
    </div>
  );
}

function JobsList() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const jobs = useQuery({ queryKey: ['farm', 'jobs'], queryFn: farmApi.getJobs, refetchInterval: 30_000 });
  const cancel = useMutation({
    mutationFn: (id: number) => farmApi.cancelJob(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  if (!jobs.data?.length) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-semibold text-white">{t('farm.jobs.title')}</h2>
      <ul className="divide-y divide-bambu-dark-tertiary bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
        {jobs.data.map((j: FarmJob) => (
          <li key={j.id} className="p-3 flex flex-wrap items-center gap-3 text-sm">
            <span className="font-mono text-bambu-green">{j.component_code}</span>
            <span className="text-white">{j.color}</span>
            <span className="text-bambu-gray">{t('farm.plan.yield', { count: j.pieces })}</span>
            <span className="text-white">{t(`farm.jobs.state.${j.state}`, { defaultValue: j.state })}</span>
            {j.printer && <span className="text-bambu-gray">· {j.printer}</span>}
            {j.state === 'waiting' && j.note && <span className="text-xs text-amber-400 break-words min-w-0 flex-1">{j.note}</span>}
            {(j.state === 'waiting' || j.state === 'pending') && (
              <Button size="sm" variant="ghost" className="ml-auto" onClick={() => cancel.mutate(j.id)}>
                {t('farm.jobs.cancel')}
              </Button>
            )}
          </li>
        ))}
      </ul>
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
  const rows = plan.data?.components || [];

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
      <JobsList />
      {summary && <AccodaSummary components={summary} onClose={() => setSummary(null)} onDone={() => setSelected(new Set())} />}
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
        <p className="text-red-400">{(plan.error as Error).message}</p>
      ) : rows.length === 0 ? (
        <p className="text-bambu-gray">{t('farm.plan.nothing')}</p>
      ) : (
        <>
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
