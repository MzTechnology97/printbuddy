// Printfarm (fork): confirm the plates proposed from the NAS 3MF files, linking each object to a shape.
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, ChevronDown, ChevronRight, FileBox, Loader2, Search } from 'lucide-react';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmLibraryFileState, type FarmPlateDetail, type FarmShape } from './api';
import './i18n';

const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';
const PAGE = 40;
// Same rule as the NAS analysis: old, test, archived, sliced and copied files are not production files.
const NOT_PRODUCTION = /(^|[\\/ _-])(old|test\w*|archiviat\w*|gcode|copia)([\\/ _.-]|$)/i;
const NEW_SHAPE = '__new__';

type Show = 'todo' | 'confirmed' | 'all';

const duration = (s: number | null) => (s ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : null);

function PlateCard({ plate, shapes, onShapeCreated }: {
  plate: FarmPlateDetail;
  shapes: FarmShape[];
  onShapeCreated: () => void;
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState<Record<string, string>>(() =>
    Object.fromEntries(plate.yields.map((y) => [y.object_name, String(y.shape_id ?? y.suggested_shape_id ?? '')])),
  );
  const confirm = useMutation({
    mutationFn: () =>
      farmApi.confirmPlate(
        plate.id,
        plate.yields.map((y) => ({
          object_name: y.object_name,
          quantity: y.quantity,
          shape_id: choice[y.object_name] ? Number(choice[y.object_name]) : null,
        })),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['farm'] });
      showToast(t('farm.plates.confirmed', { index: plate.plate_index }), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const pick = async (object: string, value: string) => {
    if (value !== NEW_SHAPE) {
      setChoice((prev) => ({ ...prev, [object]: value }));
      return;
    }
    const name = window.prompt(t('farm.products.newShape'), object.replace(/\.(stl|3mf)$/i, ''));
    if (!name?.trim()) return;
    try {
      const shape = await farmApi.createShape(name.trim());
      onShapeCreated();
      setChoice((prev) => ({ ...prev, [object]: String(shape.id) }));
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  };

  const facts = [
    plate.target_model || plate.printer_model,
    (plate.materials || []).join('+'),
    plate.nozzle_diameter ? `${plate.nozzle_diameter} mm` : null,
    duration(plate.print_time_seconds),
    plate.filament_grams ? `${Math.round(plate.filament_grams)} g` : null,
  ].filter(Boolean);

  return (
    <li className="bg-bambu-dark rounded-lg p-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-white">
          {t('farm.plates.plate', { index: plate.plate_index })}
          {plate.name && <span className="text-bambu-gray"> · {plate.name}</span>}
          <span className="text-xs text-bambu-gray"> · {facts.join(' · ')}</span>
        </p>
        {plate.status === 'confirmed' ? (
          <span className="text-xs text-bambu-green flex items-center gap-1">
            <CheckCircle2 className="w-3.5 h-3.5" /> {t('farm.plates.isConfirmed')}
          </span>
        ) : (
          <span className="text-xs text-amber-400">{t('farm.plates.toConfirm')}</span>
        )}
      </div>
      {plate.warning && <p className="text-xs text-amber-400">{plate.warning}</p>}
      <ul className="space-y-1">
        {plate.yields.map((y) => {
          const value = choice[y.object_name] ?? '';
          const suggested = !y.shape_id && !!y.suggested_shape_id && value === String(y.suggested_shape_id);
          return (
            <li key={y.object_name} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-white w-10 text-right shrink-0">{y.quantity}×</span>
              <span className="text-bambu-gray break-all min-w-0 flex-1">{y.object_name}</span>
              <select
                aria-label={`${t('farm.products.shape')} ${y.object_name}`}
                className={`${INPUT} py-1 w-full sm:w-64 ${suggested ? 'border-bambu-green/60' : ''}`}
                value={value}
                onChange={(e) => pick(y.object_name, e.target.value)}
              >
                <option value="">{t('farm.plates.notAPart')}</option>
                {shapes.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
                <option value={NEW_SHAPE}>{t('farm.plates.newShapeOption')}</option>
              </select>
              {suggested && <span className="text-xs text-bambu-green">{t('farm.plates.suggested')}</span>}
            </li>
          );
        })}
      </ul>
      <div className="flex justify-end">
        <Button size="sm" variant={plate.status === 'confirmed' ? 'secondary' : 'primary'}
          disabled={confirm.isPending} onClick={() => confirm.mutate()}>
          {confirm.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            t(plate.status === 'confirmed' ? 'farm.plates.saveAgain' : 'farm.plates.confirm')
          )}
        </Button>
      </div>
    </li>
  );
}

function FilePlates({ file, shapes, onShapeCreated }: {
  file: FarmLibraryFileState;
  shapes: FarmShape[];
  onShapeCreated: () => void;
}) {
  const { t } = useTranslation();
  const plates = useQuery({
    queryKey: ['farm', 'file-plates', file.library_file_id],
    queryFn: () => farmApi.getFilePlates(file.library_file_id!),
  });
  if (plates.isLoading) return <Loader2 className="w-5 h-5 animate-spin text-bambu-green m-3" />;
  if (plates.error) return <p className="text-sm text-red-400 p-3">{(plates.error as Error).message}</p>;
  if (!plates.data?.length) return <p className="text-sm text-bambu-gray p-3">{t('farm.plates.none')}</p>;
  return (
    <ul className="space-y-2 p-3 pt-0">
      {plates.data.map((p) => (
        <PlateCard key={`${p.id}-${p.status}`} plate={p} shapes={shapes} onShapeCreated={onShapeCreated} />
      ))}
    </ul>
  );
}

export function PlatesPanel() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [show, setShow] = useState<Show>('todo');
  const [allFiles, setAllFiles] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [limit, setLimit] = useState(PAGE);

  const files = useQuery({ queryKey: ['farm', 'library-files'], queryFn: farmApi.getFarmFiles });
  const summary = useQuery({ queryKey: ['farm', 'plates-summary'], queryFn: farmApi.getPlatesSummary });
  const shapes = useQuery({ queryKey: ['farm', 'shapes'], queryFn: farmApi.getShapes });
  const counts = useMemo(() => new Map((summary.data || []).map((s) => [s.library_file_id, s])), [summary.data]);

  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (files.data || []).filter((f) => {
    if (f.library_file_id == null || !f.path.toLowerCase().endsWith('.3mf')) return false;
    if (!allFiles && (!['orcaslicer', 'bambu_studio'].includes(f.slicer || '') || NOT_PRODUCTION.test(f.path)))
      return false;
    const c = counts.get(f.library_file_id);
    if (show === 'todo' && c && c.proposed === 0) return false; // files never opened are still to do
    if (show === 'confirmed' && !c?.confirmed) return false;
    const text = f.path.toLowerCase();
    return words.every((w) => text.includes(w));
  });
  const confirmedTotal = (summary.data || []).reduce((n, s) => n + s.confirmed, 0);
  const onShapeCreated = () => queryClient.invalidateQueries({ queryKey: ['farm', 'shapes'] });

  return (
    <div className="space-y-4">
      <p className="text-bambu-gray text-sm max-w-3xl">{t('farm.plates.intro')}</p>
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[16rem] max-w-md">
          <Search className="w-4 h-4 text-bambu-gray absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input type="search" aria-label={t('farm.plates.search')} placeholder={t('farm.plates.search')}
            className={`${INPUT} w-full pl-8`} value={query}
            onChange={(e) => { setQuery(e.target.value); setLimit(PAGE); }} />
        </div>
        <select aria-label={t('farm.components.filter')} className={INPUT} value={show}
          onChange={(e) => { setShow(e.target.value as Show); setLimit(PAGE); }}>
          <option value="todo">{t('farm.plates.showTodo')}</option>
          <option value="confirmed">{t('farm.plates.showConfirmed')}</option>
          <option value="all">{t('farm.components.all')}</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-bambu-gray">
          <input type="checkbox" checked={allFiles} onChange={(e) => setAllFiles(e.target.checked)} />
          {t('farm.plates.allFiles')}
        </label>
      </div>
      <p className="text-xs text-bambu-gray">
        {t('farm.plates.summary', { files: shown.length, confirmed: confirmedTotal })}
      </p>

      {files.isLoading ? (
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      ) : (
        <ul className="space-y-2">
          {shown.slice(0, limit).map((f) => {
            const c = counts.get(f.library_file_id!);
            const isOpen = open === f.library_file_id;
            const slash = Math.max(f.path.lastIndexOf('/'), f.path.lastIndexOf('\\'));
            return (
              <li key={f.library_file_id}
                className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
                <button type="button" aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : f.library_file_id)}
                  className="w-full text-left p-3 flex items-start gap-2">
                  {isOpen ? (
                    <ChevronDown className="w-4 h-4 mt-0.5 shrink-0" />
                  ) : (
                    <ChevronRight className="w-4 h-4 mt-0.5 shrink-0" />
                  )}
                  <FileBox className="w-4 h-4 mt-0.5 text-bambu-gray shrink-0" />
                  <span className="min-w-0 flex-1 break-all">
                    <span className="text-xs text-bambu-gray block">{f.path.slice(0, slash + 1)}</span>
                    <span className="text-sm text-white">{f.path.slice(slash + 1)}</span>
                  </span>
                  <span className="text-xs text-bambu-gray shrink-0 text-right">
                    {f.printer_model}
                    <br />
                    {c ? t('farm.plates.counts', { todo: c.proposed, done: c.confirmed }) : t('farm.plates.notOpened')}
                  </span>
                </button>
                {isOpen && <FilePlates file={f} shapes={shapes.data || []} onShapeCreated={onShapeCreated} />}
              </li>
            );
          })}
        </ul>
      )}
      {shown.length > limit && (
        <Button variant="secondary" onClick={() => setLimit(limit + PAGE)}>
          {t('farm.plates.more', { count: shown.length - limit })}
        </Button>
      )}
    </div>
  );
}
